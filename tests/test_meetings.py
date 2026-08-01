"""
Tests for cuny_scouter.meetings — meeting-time parser and overlap logic.
"""
import pytest
from cuny_scouter.meetings import Meeting, parse_meetings, overlaps, _to_minutes


class TestToMinutes:
    def test_am(self):
        assert _to_minutes("9:05AM") == 9 * 60 + 5

    def test_pm(self):
        assert _to_minutes("6:05PM") == 18 * 60 + 5

    def test_noon(self):
        assert _to_minutes("12:00PM") == 12 * 60

    def test_midnight(self):
        assert _to_minutes("12:00AM") == 0

    def test_spaces_ok(self):
        assert _to_minutes("9:05 AM") == 9 * 60 + 5

    def test_12pm_not_0(self):
        assert _to_minutes("12:50PM") == 12 * 60 + 50


class TestParseMeetings:
    def test_empty_string(self):
        assert parse_meetings("") == []

    def test_none(self):
        assert parse_meetings(None) == []

    def test_tba(self):
        assert parse_meetings("TBA") == []

    def test_tba_case_insensitive(self):
        assert parse_meetings("tba") == []

    def test_whitespace_only(self):
        assert parse_meetings("   ") == []

    def test_single_day(self):
        result = parse_meetings("Fr 11:10AM - 2:05PM")
        assert len(result) == 1
        m = result[0]
        assert m.day_of_week == 4  # Friday
        assert m.start_minute == 11 * 60 + 10
        assert m.end_minute == 14 * 60 + 5

    def test_saturday(self):
        result = parse_meetings("Sa 11:10AM - 2:05PM")
        assert len(result) == 1
        assert result[0].day_of_week == 5  # Saturday

    def test_two_day_token(self):
        result = parse_meetings("MoWe 9:05AM - 10:20AM")
        assert len(result) == 2
        days = {m.day_of_week for m in result}
        assert days == {0, 2}  # Mon, Wed
        for m in result:
            assert m.start_minute == 9 * 60 + 5
            assert m.end_minute == 10 * 60 + 20

    def test_tuth(self):
        result = parse_meetings("TuTh 9:05AM - 10:20AM")
        assert len(result) == 2
        days = {m.day_of_week for m in result}
        assert days == {1, 3}  # Tue, Thu

    def test_three_day_token(self):
        result = parse_meetings("MoWeFr 10:00AM - 11:00AM")
        assert len(result) == 3
        days = {m.day_of_week for m in result}
        assert days == {0, 2, 4}

    def test_pm_times(self):
        result = parse_meetings("TuTh 6:05PM - 7:20PM")
        assert len(result) == 2
        for m in result:
            assert m.start_minute == 18 * 60 + 5
            assert m.end_minute == 19 * 60 + 20

    def test_smushed_two_meeting_hybrid(self):
        # This is how multi-meeting cells arrive after get_text(strip=True)
        result = parse_meetings("Th 4:10PM - 5:25PMTu 4:10PM - 5:25PM")
        assert len(result) == 2
        days = {m.day_of_week for m in result}
        assert days == {1, 3}  # Tue, Thu

    def test_smushed_different_times(self):
        result = parse_meetings("MoWe 9:05AM - 10:20AMFr 2:00PM - 3:15PM")
        assert len(result) == 3
        days = {m.day_of_week for m in result}
        assert days == {0, 2, 4}

    def test_no_match_returns_empty(self):
        assert parse_meetings("Online Asynchronous") == []

    def test_returns_frozen_dataclasses(self):
        result = parse_meetings("Mo 9:00AM - 10:00AM")
        assert len(result) == 1
        with pytest.raises((AttributeError, TypeError)):
            result[0].day_of_week = 99  # frozen


class TestOverlaps:
    def test_same_day_overlapping(self):
        a = Meeting(day_of_week=0, start_minute=540, end_minute=620)   # 9:00–10:20
        b = Meeting(day_of_week=0, start_minute=600, end_minute=700)   # 10:00–11:40
        assert overlaps(a, b) is True
        assert overlaps(b, a) is True

    def test_same_day_adjacent_not_overlapping(self):
        a = Meeting(day_of_week=0, start_minute=540, end_minute=620)
        b = Meeting(day_of_week=0, start_minute=620, end_minute=700)
        assert overlaps(a, b) is False

    def test_same_day_no_overlap(self):
        a = Meeting(day_of_week=0, start_minute=540, end_minute=600)
        b = Meeting(day_of_week=0, start_minute=660, end_minute=720)
        assert overlaps(a, b) is False

    def test_different_days_same_times(self):
        a = Meeting(day_of_week=0, start_minute=540, end_minute=620)  # Mon
        b = Meeting(day_of_week=2, start_minute=540, end_minute=620)  # Wed
        assert overlaps(a, b) is False

    def test_contained_within(self):
        a = Meeting(day_of_week=1, start_minute=540, end_minute=720)
        b = Meeting(day_of_week=1, start_minute=560, end_minute=600)
        assert overlaps(a, b) is True

    def test_identical_meetings(self):
        a = Meeting(day_of_week=3, start_minute=600, end_minute=660)
        assert overlaps(a, a) is True
