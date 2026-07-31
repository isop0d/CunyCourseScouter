"""
Background worker: runs poll_once() on a configurable interval with exponential backoff.

Run with: python -m cuny_scouter.scheduler
"""
import logging
import time
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert

from cuny_scouter.config import settings
from cuny_scouter.db.models import Section, ScrapeRun, SectionSnapshot
from cuny_scouter.db.session import get_session
from cuny_scouter.scraper.client import fetch_subject_html
from cuny_scouter.scraper.parser import parse_sections, StructuralValidationError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MAX_BACKOFF = 3600


def poll_once() -> None:
    db = get_session()
    run = ScrapeRun(
        institution=settings.institution,
        term_code=settings.term_code,
        subject=settings.subject,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        log.info("Fetching HTML from CUNY Global Search...")
        html = fetch_subject_html()

        log.info("Parsing sections...")
        records = parse_sections(
            html,
            term_code=settings.term_code,
            institution=settings.institution,
            subject=settings.subject,
        )
        log.info(f"Parsed {len(records)} sections.")

        now = datetime.now(timezone.utc)

        # Upsert into sections (current authoritative state)
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

        # Append snapshots for full history
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

        run.sections_found = len(records)
        run.status = "ok"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        log.info(f"Poll complete. Run id={run.id}, sections={len(records)}.")

    except StructuralValidationError as exc:
        run.status = "empty"
        run.error_msg = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        log.error(f"Structural validation failed: {exc}")
        raise

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
