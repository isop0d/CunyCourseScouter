"""
Background worker: two-tier polling.

  Fast poll (every 2 min by default): only fetches subjects that have active
  watches. Cheap and near-real-time for notifications.

  Full poll (every 60 min by default): scrapes all subjects to keep the
  browse catalog fresh and pick up newly added sections.

Run with: python -m cuny_scouter.scheduler
"""
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from cuny_scouter.config import settings
from cuny_scouter.db.models import Section, ScrapeRun, SectionSnapshot, Watch
from cuny_scouter.db.session import get_session
from cuny_scouter.diff import compute_diff
from cuny_scouter.notifier import dispatch_notifications
from cuny_scouter.scraper.client import (
    fetch_all_subjects,
    fetch_subject_html,
    fetch_subjects,
    ScraperError,
    ScraperNoResultsError,
)
from cuny_scouter.scraper.parser import parse_sections

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MAX_BACKOFF = 3600


def _upsert_subject(db, run: ScrapeRun, fetch_key: str, html: str) -> list:
    """Parse and upsert one subject's sections. Returns list of SectionRecord."""
    records = parse_sections(
        html,
        term_code=settings.term_code,
        institution=settings.institution,
        subject=fetch_key,
    )

    now = datetime.now(timezone.utc)

    for record in records:
        stmt = insert(Section).values(
            class_number=record.class_number,
            term_code=record.term_code,
            institution=record.institution,
            subject=record.subject,
            course_number=record.course_number,
            course_name=record.course_name,
            section_code=record.section_code,
            days_times=record.days_times,
            room=record.room,
            instructor=record.instructor,
            instruction_mode=record.instruction_mode,
            meeting_dates=record.meeting_dates,
            course_topic=record.course_topic,
            status=record.status,
            fetch_key=fetch_key,
            last_seen_run=run.id,
            first_seen_at=now,
            updated_at=now,
        ).on_conflict_do_update(
            index_elements=["class_number"],
            set_={
                "subject": record.subject,
                "course_number": record.course_number,
                "course_name": record.course_name,
                "status": record.status,
                "section_code": record.section_code,
                "days_times": record.days_times,
                "room": record.room,
                "instructor": record.instructor,
                "instruction_mode": record.instruction_mode,
                "meeting_dates": record.meeting_dates,
                "course_topic": record.course_topic,
                "fetch_key": fetch_key,
                "last_seen_run": run.id,
                "updated_at": now,
            },
        )
        db.execute(stmt)

    db.bulk_insert_mappings(
        SectionSnapshot,
        [
            {
                "run_id": run.id,
                "class_number": r.class_number,
                "status": r.status,
                "scraped_at": now,
            }
            for r in records
        ],
    )

    return records


def _fetch_and_upsert_key(db, run: ScrapeRun, fetch_key: str, fetch_name: str, delay: float) -> tuple[list, bool]:
    """Fetch UGRD + GRAD for one fetch_key, upsert both. Returns (records, had_error)."""
    all_records = []
    had_error = False

    for career in ("UGRD", "GRAD"):
        try:
            html = fetch_subject_html(fetch_key, fetch_name, career=career)
            records = _upsert_subject(db, run, fetch_key, html)
            all_records.extend(records)
            log.info(f"  {fetch_key} ({career}): {len(records)} sections")
        except ScraperNoResultsError:
            pass
        except Exception as exc:
            db.rollback()
            log.warning(f"  {fetch_key} ({career}): failed — {exc}")
            had_error = True
        time.sleep(delay)

    return all_records, had_error


def _get_watched_fetch_keys(db) -> list[tuple[str, str]]:
    """
    Return [(fetch_key, fetch_name), ...] for subjects that have at least one
    active watch. Uses the fetch_key stored on sections rows.
    Falls back to fetch_subjects() for the display name.
    """
    rows = db.execute(text(
        "SELECT DISTINCT s.fetch_key "
        "FROM sections s "
        "JOIN watches w ON s.class_number = w.class_number "
        "WHERE w.active = TRUE AND s.fetch_key IS NOT NULL"
    )).fetchall()

    if not rows:
        return []

    keys_needed = {row[0] for row in rows}

    # Get display names from CUNY (one session, cheap)
    all_subjects = fetch_subjects()
    name_map = {code: name for code, name in all_subjects}

    return [(key, name_map.get(key, key)) for key in keys_needed]


def _run_poll(label: str, subject_pairs: list[tuple[str, str]], delay: float = 1.5) -> None:
    """Core poll logic: fetch given subjects, upsert, diff, notify."""
    db = get_session()
    run = ScrapeRun(
        institution=settings.institution,
        term_code=settings.term_code,
        subject=label,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        all_records = []
        failed = []

        for i, (fetch_key, fetch_name) in enumerate(subject_pairs):
            if i > 0:
                time.sleep(delay)
            records, had_error = _fetch_and_upsert_key(db, run, fetch_key, fetch_name, delay=delay)
            all_records.extend(records)
            if had_error:
                failed.append(fetch_key)

        db.commit()

        run.sections_found = len(all_records)
        run.status = "ok" if not failed else "partial"
        run.finished_at = datetime.now(timezone.utc)
        if failed:
            run.error_msg = f"Failed: {', '.join(failed)}"
        db.commit()

        log.info(f"{label} poll complete. sections={len(all_records)}, failed={len(failed)}")

        # Diff and notify
        prev_run = (
            db.query(ScrapeRun)
            .filter(ScrapeRun.status.in_(["ok", "partial"]), ScrapeRun.id != run.id)
            .order_by(ScrapeRun.finished_at.desc())
            .first()
        )
        if prev_run:
            prev_snapshots = [
                (s.class_number, s.status)
                for s in db.query(SectionSnapshot)
                .filter(SectionSnapshot.run_id == prev_run.id)
                .all()
            ]
            events = compute_diff(all_records, prev_snapshots, run.id)
            if events:
                log.info(f"Detected {len(events)} status change(s).")
                sent = dispatch_notifications(db, events, settings.discord_webhook_url)
                log.info(f"Sent {sent} notification(s).")
            else:
                log.info("No status changes detected.")

    except Exception as exc:
        run.status = "error"
        run.error_msg = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        log.error(f"{label} poll failed: {exc}")
        raise
    finally:
        db.close()


def poll_fast() -> bool:
    """Poll only subjects with active watches. Returns False if nothing to watch."""
    db = get_session()
    try:
        watched = _get_watched_fetch_keys(db)
    finally:
        db.close()

    if not watched:
        log.info("Fast poll: no active watches, skipping.")
        return False

    log.info(f"Fast poll: {len(watched)} subject(s) with active watches — {[k for k, _ in watched]}")
    _run_poll("FAST", watched, delay=1.0)
    return True


def poll_full() -> None:
    """Full poll of all subjects — keeps the browse catalog fresh."""
    log.info("Full poll: fetching all subjects...")
    all_subjects = fetch_subjects()
    name_map = {code: name for code, name in all_subjects}
    subject_pairs = list(name_map.items())
    _run_poll("FULL", subject_pairs, delay=1.5)


def run_worker() -> None:
    fast_interval = settings.fast_poll_interval_seconds
    full_interval = settings.full_poll_interval_seconds

    log.info(f"Worker started. Fast poll: {fast_interval}s, Full poll: {full_interval}s.")

    last_full = float("-inf")  # force full poll on startup

    while True:
        now = time.monotonic()

        if now - last_full >= full_interval:
            try:
                poll_full()
                last_full = time.monotonic()
            except Exception:
                log.exception("Full poll failed.")
        else:
            try:
                poll_fast()
            except Exception:
                log.exception("Fast poll failed.")

        log.info(f"Sleeping {fast_interval}s.")
        time.sleep(fast_interval)


if __name__ == "__main__":
    run_worker()
