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
from cuny_scouter.db.models import Professor, Section, ScrapeRun, SectionMeeting, SectionSnapshot, Watch
from cuny_scouter.meetings import parse_meetings
from cuny_scouter.professors import classify, match_rmp, normalize_instructor
from cuny_scouter.rmp import search_teacher
from cuny_scouter.db.session import get_session
from cuny_scouter.diff import compute_diff
from cuny_scouter.notifier import dispatch_notifications
from cuny_scouter.scraper.client import (
    fetch_all_subjects,
    fetch_class_html,
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

    # Replace meeting rows for every section in this batch.
    class_numbers = [r.class_number for r in records]
    if class_numbers:
        db.execute(
            text("DELETE FROM section_meetings WHERE section_id = ANY(:ids)"),
            {"ids": class_numbers},
        )
        db.bulk_insert_mappings(
            SectionMeeting,
            [
                {
                    "section_id": r.class_number,
                    "day_of_week": m.day_of_week,
                    "start_minute": m.start_minute,
                    "end_minute": m.end_minute,
                }
                for r in records
                for m in parse_meetings(r.days_times)
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


def _get_watched_classes(db) -> dict[str, list[tuple[int, str]]]:
    """
    Return {fetch_key: [(class_number, current_status), ...]} for all active watches.
    Groups watched classes by subject so we make one HTTP request per subject.
    """
    rows = db.execute(text(
        "SELECT s.fetch_key, w.class_number, s.status "
        "FROM watches w "
        "JOIN sections s ON s.class_number = w.class_number "
        "WHERE w.active = TRUE AND s.fetch_key IS NOT NULL"
    )).fetchall()

    grouped: dict[str, list[tuple[int, str]]] = {}
    for fetch_key, class_number, status in rows:
        grouped.setdefault(fetch_key, []).append((class_number, status))
    return grouped


def _run_fast_poll(grouped: dict[str, list[tuple[int, str]]]) -> None:
    """
    Fetch each watched subject once, but only diff/notify the specific watched classes.
    One HTTP request per unique subject with active watches.
    """
    db = get_session()
    run = ScrapeRun(
        institution=settings.institution,
        term_code=settings.term_code,
        subject="FAST",
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    all_records = []
    prev_snapshots = []
    failed = []
    now = datetime.now(timezone.utc)

    all_subjects = fetch_subjects()
    name_map = {code: name for code, name in all_subjects}

    for i, (fetch_key, class_watches) in enumerate(grouped.items()):
        if i > 0:
            time.sleep(1.0)

        watched_numbers = {cn for cn, _ in class_watches}
        prev_snapshots.extend(class_watches)

        for career in ("UGRD", "GRAD"):
            try:
                html = fetch_subject_html(fetch_key, name_map.get(fetch_key, fetch_key), career=career)
                records = parse_sections(html, term_code=settings.term_code, institution=settings.institution, subject=fetch_key)
                for r in records:
                    if r.class_number in watched_numbers:
                        db.execute(text(
                            "UPDATE sections SET status=:s, updated_at=:t, last_seen_run=:r WHERE class_number=:cn"
                        ), {"s": r.status, "t": now, "r": run.id, "cn": r.class_number})
                        db.execute(text(
                            "INSERT INTO section_snapshots (run_id, class_number, status, scraped_at) "
                            "VALUES (:r, :cn, :s, :t)"
                        ), {"r": run.id, "cn": r.class_number, "s": r.status, "t": now})
                        all_records.append(r)
                        log.info(f"  Class {r.class_number} ({fetch_key}/{career}): {r.status}")
            except ScraperNoResultsError:
                pass
            except Exception as exc:
                db.rollback()
                log.warning(f"  {fetch_key} ({career}): failed — {exc}")
                failed.append(fetch_key)
            time.sleep(1.0)

    db.commit()
    run.sections_found = len(all_records)
    run.status = "ok" if not failed else "partial"
    run.finished_at = now
    db.commit()

    log.info(f"FAST poll complete. subjects={len(grouped)}, classes checked={len(all_records)}, failed={len(failed)}")

    if all_records:
        events = compute_diff(all_records, prev_snapshots, run.id)
        if events:
            log.info(f"Detected {len(events)} status change(s).")
            sent = dispatch_notifications(db, events, settings.discord_webhook_url)
            log.info(f"Sent {sent} notification(s).")
        else:
            log.info("No status changes detected.")

    db.close()


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
    """Poll only subjects with active watches, diffing only watched classes. Returns False if nothing to watch."""
    db = get_session()
    try:
        grouped = _get_watched_classes(db)
    finally:
        db.close()

    if not grouped:
        log.info("Fast poll: no active watches, skipping.")
        return False

    total_classes = sum(len(v) for v in grouped.values())
    log.info(f"Fast poll: {len(grouped)} subject(s), {total_classes} class(es) — {list(grouped.keys())}")
    _run_fast_poll(grouped)
    return True


def poll_full() -> None:
    """Full poll of all subjects — keeps the browse catalog fresh."""
    log.info("Full poll: fetching all subjects...")
    all_subjects = fetch_subjects()
    name_map = {code: name for code, name in all_subjects}
    subject_pairs = list(name_map.items())
    _run_poll("FULL", subject_pairs, delay=1.5)


def refresh_professors() -> None:
    """Backfill / refresh the professors table from RMP. Runs on a slow cadence."""
    db = get_session()
    try:
        # Collect distinct non-staff instructor strings
        rows = db.query(Section.instructor).distinct().all()
        names = [r[0] for r in rows if r[0] and classify(r[0]) == "name"]
        log.info(f"RMP refresh: {len(names)} distinct instructor strings.")

        stale_threshold = datetime.now(timezone.utc)

        for raw in names:
            normalized_key, last, initial = normalize_instructor(raw)
            if not normalized_key:
                continue

            existing = db.query(Professor).filter(Professor.normalized_name == normalized_key).first()
            if existing and existing.fetched_at:
                # Skip recently fetched entries
                age_s = (stale_threshold - existing.fetched_at).total_seconds()
                if age_s < settings.rmp_refresh_interval_seconds:
                    continue

            if classify(raw) == "staff":
                _upsert_professor(db, normalized_key, last, initial, [], "staff")
                continue

            nodes = search_teacher(last, settings.rmp_school_id)
            node, status = match_rmp(last, initial, nodes)
            _upsert_professor(db, normalized_key, last, initial, [node] if node else [], status)
            time.sleep(0.5)  # gentle rate limiting

        db.commit()
        log.info("RMP refresh complete.")
    except Exception:
        log.exception("RMP refresh failed.")
    finally:
        db.close()


def _upsert_professor(db, normalized_key: str, last: str, initial: str, nodes: list, status: str) -> None:
    now = datetime.now(timezone.utc)
    node = nodes[0] if nodes else None
    existing = db.query(Professor).filter(Professor.normalized_name == normalized_key).first()

    display_name = f"{last}, {initial}." if initial else last

    if existing:
        existing.match_status = status
        existing.fetched_at = now
        if node:
            existing.name = f"{node.get('firstName', '')} {node.get('lastName', '')}".strip() or display_name
            existing.rmp_legacy_id = node.get("legacyId")
            existing.avg_rating = node.get("avgRating")
            existing.avg_difficulty = node.get("avgDifficulty")
            existing.num_ratings = node.get("numRatings", 0)
            existing.would_take_again = node.get("wouldTakeAgainPercent")
            existing.rmp_url = node.get("rmpUrl")
    else:
        prof = Professor(
            name=f"{node.get('firstName', '')} {node.get('lastName', '')}".strip() if node else display_name,
            normalized_name=normalized_key,
            rmp_legacy_id=node.get("legacyId") if node else None,
            avg_rating=node.get("avgRating") if node else None,
            avg_difficulty=node.get("avgDifficulty") if node else None,
            num_ratings=node.get("numRatings", 0) if node else 0,
            would_take_again=node.get("wouldTakeAgainPercent") if node else None,
            match_status=status,
            rmp_url=node.get("rmpUrl") if node else None,
            fetched_at=now,
        )
        db.add(prof)


def run_worker() -> None:
    fast_interval = settings.fast_poll_interval_seconds
    full_interval = settings.full_poll_interval_seconds

    log.info(f"Worker started. Fast poll: {fast_interval}s, Full poll: {full_interval}s.")

    last_full = float("-inf")   # force full poll on startup

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
