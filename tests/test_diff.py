from cuny_scouter.diff import compute_diff, StatusEvent
from cuny_scouter.scraper.parser import SectionRecord


def _record(class_number: int, status: str) -> SectionRecord:
    return SectionRecord(
        class_number=class_number,
        term_code="1269",
        institution="BAR01",
        subject="CMIS",
        course_number="2200",
        course_name="Intro Info Systems",
        section_code="LEC",
        days_times="MoWe",
        room="",
        instructor="",
        instruction_mode="In Person",
        meeting_dates="",
        course_topic="",
        status=status,
    )


class TestComputeDiff:
    def test_no_changes_returns_empty(self):
        records = [_record(1001, "open"), _record(1002, "waitlist")]
        prev = [(1001, "open"), (1002, "waitlist")]
        assert compute_diff(records, prev, run_id=2) == []

    def test_closed_to_open_emits_event(self):
        records = [_record(1001, "open")]
        prev = [(1001, "closed")]
        events = compute_diff(records, prev, run_id=2)
        assert len(events) == 1
        assert events[0].from_status == "closed"
        assert events[0].to_status == "open"
        assert events[0].class_number == 1001

    def test_open_to_closed_emits_event(self):
        records = [_record(1001, "closed")]
        prev = [(1001, "open")]
        events = compute_diff(records, prev, run_id=2)
        assert len(events) == 1
        assert events[0].from_status == "open"
        assert events[0].to_status == "closed"

    def test_open_to_waitlist_emits_event(self):
        records = [_record(1001, "waitlist")]
        prev = [(1001, "open")]
        events = compute_diff(records, prev, run_id=2)
        assert len(events) == 1
        assert events[0].to_status == "waitlist"

    def test_new_section_emits_unknown_from(self):
        records = [_record(9999, "open")]
        prev = []
        events = compute_diff(records, prev, run_id=2)
        assert len(events) == 1
        assert events[0].from_status == "unknown"
        assert events[0].to_status == "open"

    def test_multiple_changes_detected(self):
        records = [
            _record(1001, "open"),
            _record(1002, "closed"),
            _record(1003, "waitlist"),
        ]
        prev = [
            (1001, "closed"),
            (1002, "closed"),
            (1003, "open"),
        ]
        events = compute_diff(records, prev, run_id=2)
        assert len(events) == 2
        changed = {e.class_number for e in events}
        assert changed == {1001, 1003}

    def test_run_id_attached_to_events(self):
        records = [_record(1001, "open")]
        prev = [(1001, "closed")]
        events = compute_diff(records, prev, run_id=42)
        assert events[0].run_id == 42

    def test_removed_section_not_emitted(self):
        """Sections that vanish from results are not diffed — CUNY may hide them temporarily."""
        records = [_record(1001, "open")]
        prev = [(1001, "open"), (1002, "closed")]
        events = compute_diff(records, prev, run_id=2)
        assert events == []
