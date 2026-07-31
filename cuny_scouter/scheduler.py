"""
Background worker: polls all Baruch subjects on a configurable interval.

Run with: python -m cuny_scouter.scheduler
"""
import logging
import time
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert

from cuny_scouter.config import settings
from cuny_scouter.db.models import Section, ScrapeRun, SectionSnapshot
from cuny_scouter.db.session import get_session
from cuny_scouter.diff import compute_diff
from cuny_scouter.notifier import dispatch_notifications
from cuny_scouter.scraper.client import fetch_all_subjects, ScraperError
from cuny_scouter.scraper.parser import parse_sections, StructuralValidationError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MAX_BACKOFF = 3600


def _upsert_subject(db, run: ScrapeRun, subject_code: str, html: str) -> list:
    """Parse and upsert one subject's sections. Returns list of SectionRecord."""
    records = parse_sections(
        html,
        term_code=settings.term_code,
        institution=settings.institution,
        subject=subject_code,
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
            last_seen_run=run.id,
            first_seen_at=now,
            updated_at=now,
        ).on_conflict_do_update(
            index_elements=["class_number"],
            set_={
                "status": record.status,
                "section_code": record.section_code,
                "days_times": record.days_times,
                "room": record.room,
                "instructor": record.instructor,
                "instruction_mode": record.instruction_mode,
                "meeting_dates": record.meeting_dates,
                "course_topic": record.course_topic,
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


def poll_once() -> None:
    db = get_session()
    run = ScrapeRun(
        institution=settings.institution,
        term_code=settings.term_code,
        subject="ALL",
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        log.info("Fetching all Baruch subjects...")
        subject_results = fetch_all_subjects()
        log.info(f"Fetched HTML for {len(subject_results)} subjects.")

        all_records = []
        failed_subjects = []

        for (subject_code, subject_name), html in subject_results:
            try:
                records = _upsert_subject(db, run, subject_code, html)
                all_records.extend(records)
                log.info(f"  {subject_code}: {len(records)} sections")
            except Exception as exc:
                db.rollback()
                log.warning(f"  {subject_code}: failed — {exc}")
                failed_subjects.append(subject_code)

        db.commit()

        total = len(all_records)
        run.sections_found = total
        run.status = "ok" if not failed_subjects else "partial"
        run.finished_at = datetime.now(timezone.utc)
        if failed_subjects:
            run.error_msg = f"Failed subjects: {', '.join(failed_subjects)}"
        db.commit()

        log.info(f"Poll complete. Run id={run.id}, total sections={total}, failed={len(failed_subjects)}.")

        # Diff against previous successful run
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
        log.error(f"Poll failed: {exc}")
        raise

    finally:
        db.close()


def run_worker() -> None:
    interval = settings.poll_interval_seconds
    consecutive_errors = 0

    log.info(f"Worker started. Poll interval: {interval}s.")

    while True:
        try:
            poll_once()
            consecutive_errors = 0
            backoff = interval
        except Exception:
            consecutive_errors += 1
            backoff = min(interval * (2 ** (consecutive_errors - 1)), MAX_BACKOFF)
            log.warning(f"Error #{consecutive_errors}. Next poll in {backoff}s.")

        log.info(f"Sleeping {backoff}s until next poll.")
        time.sleep(backoff)


if __name__ == "__main__":
    run_worker()
