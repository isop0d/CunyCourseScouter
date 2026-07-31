from dataclasses import dataclass

from cuny_scouter.scraper.parser import SectionRecord


@dataclass
class StatusEvent:
    class_number: int
    from_status: str  # "unknown" if first time seen, "removed" never sent — handled separately
    to_status: str
    run_id: int


def compute_diff(
    new_records: list[SectionRecord],
    previous_snapshots: list[tuple[int, str]],  # [(class_number, status), ...]
    run_id: int,
) -> list[StatusEvent]:
    """
    Compare new poll results against the previous successful run's snapshots.
    Returns one StatusEvent per section whose status changed.
    """
    prev = {class_number: status for class_number, status in previous_snapshots}
    new = {r.class_number: r.status for r in new_records}

    events: list[StatusEvent] = []

    for class_number, new_status in new.items():
        old_status = prev.get(class_number, "unknown")
        if old_status != new_status:
            events.append(StatusEvent(
                class_number=class_number,
                from_status=old_status,
                to_status=new_status,
                run_id=run_id,
            ))

    return events
