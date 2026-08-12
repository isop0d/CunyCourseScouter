"""
Discord webhook notifier with three-layer dedup:
  1. DB unique index on (watch_id, from_status, to_status, day) — hard stop
  2. 60-minute cooldown window — protects midnight day-rollover edge case
  3. Watch auto-deactivation after first alert — prevents oscillation spam
"""
import logging
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cuny_scouter.diff import StatusEvent
from cuny_scouter.db.models import Notification, Watch, Section

log = logging.getLogger(__name__)

COOLDOWN_MINUTES = 60

STATUS_LABEL = {
    "open": "Open",
    "closed": "Closed",
    "waitlist": "Wait List",
}

STATUS_COLOR = {
    "open": 0x2ECC71,    # green
    "closed": 0xE74C3C,  # red
    "waitlist": 0xF39C12, # orange
}


def _build_message(event: StatusEvent, section: Section) -> dict:
    course = f"CIS {section.course_number} {section.section_code}"
    to_label = STATUS_LABEL.get(event.to_status, event.to_status.title())
    color = STATUS_COLOR.get(event.to_status, 0x95A5A6)

    return {
        "content": f"<@{'{discord_id}'}> A section you're watching changed status.",
        "embeds": [{
            "title": f"{course} is now {to_label}",
            "description": (
                f"**Course:** CIS {section.course_number} — {section.course_name}\n"
                f"**Section:** {section.section_code}\n"
                f"**Instructor:** {section.instructor or 'TBA'}\n"
                f"**Days/Times:** {section.days_times or 'TBA'}\n"
                f"**Status:** {STATUS_LABEL.get(event.from_status, event.from_status.title())} → **{to_label}**"
            ),
            "color": color,
        }],
    }


def send_discord_notification(
    webhook_url: str,
    discord_id: str,
    event: StatusEvent,
    section: Section,
) -> bool:
    payload = _build_message(event, section)
    payload["content"] = payload["content"].replace("{discord_id}", discord_id)

    resp = requests.post(webhook_url, json=payload, timeout=10)
    if resp.status_code not in (200, 204):
        log.error(f"Discord webhook failed: {resp.status_code} {resp.text[:200]}")
        return False
    return True


def _cooldown_passed(db: Session, watch_id: int, to_status: str) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=COOLDOWN_MINUTES)
    row = db.execute(
        text(
            "SELECT sent_at FROM notifications "
            "WHERE watch_id = :wid AND to_status = :ts "
            "ORDER BY sent_at DESC LIMIT 1"
        ),
        {"wid": watch_id, "ts": to_status},
    ).fetchone()
    if row is None:
        return True
    return row[0].replace(tzinfo=timezone.utc) < cutoff


def dispatch_notifications(
    db: Session,
    events: list[StatusEvent],
    webhook_url: str,
) -> int:
    if not events or not webhook_url:
        return 0

    sent = 0
    for event in events:
        if event.from_status == "unknown":
            continue  # first-time-seen, not a real status change

        section = db.get(Section, event.class_number)
        if section is None:
            continue

        watches = (
            db.query(Watch)
            .filter(
                Watch.class_number == event.class_number,
                Watch.active == True,
                Watch.notify_on.in_([event.to_status, "any"]),
            )
            .all()
        )

        for watch in watches:
            if not _cooldown_passed(db, watch.id, event.to_status):
                log.info(f"Cooldown active for watch {watch.id}, skipping.")
                continue

            discord_id = watch.student.discord_id
            success = send_discord_notification(webhook_url, discord_id, event, section)
            if not success:
                continue

            try:
                db.add(Notification(
                    watch_id=watch.id,
                    run_id=event.run_id,
                    from_status=event.from_status,
                    to_status=event.to_status,
                    payload={"discord_id": discord_id},
                ))
                db.flush()
            except IntegrityError:
                db.rollback()
                log.info(f"Dedup: notification already sent for watch {watch.id} today.")
                continue

            if watch.auto_deactivate and event.to_status == "open":
                watch.active = False
                watch.deactivated_at = datetime.now(timezone.utc)
                log.info(f"Auto-deactivated watch {watch.id} after open alert.")

            db.commit()
            sent += 1
            log.info(f"Notified discord_id={discord_id} about class {event.class_number} → {event.to_status}")

    return sent
