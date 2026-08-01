"""
Parse CUNY meeting-time strings into structured Meeting records.

CUNY day/time strings arrive smushed from `get_text(strip=True)` — a multi-meeting
hybrid cell like "<td>Th 4:10PM - 5:25PM<br>Tu 4:10PM - 5:25PM</td>" becomes the
string "Th 4:10PM - 5:25PMTu 4:10PM - 5:25PM". A regex tokenizer handles this
without any separator assumption.

Day codes: Mo Tu We Th Fr Sa Su
"""
import re
from dataclasses import dataclass

_DAY_TO_INT: dict[str, int] = {
    "Mo": 0, "Tu": 1, "We": 2, "Th": 3, "Fr": 4, "Sa": 5, "Su": 6
}

# Matches one meeting token anywhere in the string, even when smushed together.
# Group 1: one or more two-letter day codes (e.g. "MoWe", "TuTh", "Fr")
# Group 2: start time  (e.g. "9:05AM")
# Group 3: end time    (e.g. "10:20AM")
_MEETING_RE = re.compile(
    r"((?:Mo|Tu|We|Th|Fr|Sa|Su)+)"
    r"\s*"
    r"(\d{1,2}:\d{2}\s*[AP]M)"
    r"\s*-\s*"
    r"(\d{1,2}:\d{2}\s*[AP]M)"
)

_DAY_TOKEN_RE = re.compile(r"Mo|Tu|We|Th|Fr|Sa|Su")


@dataclass(frozen=True)
class Meeting:
    day_of_week: int   # 0=Mon .. 6=Sun
    start_minute: int  # minutes since midnight
    end_minute: int


def _to_minutes(t: str) -> int:
    """Convert a time string like '9:05AM' or '12:50PM' to minutes since midnight."""
    t = t.replace(" ", "")
    ampm = t[-2:]
    h, m = t[:-2].split(":")
    hours, minutes = int(h), int(m)
    if ampm == "AM":
        if hours == 12:
            hours = 0
    else:
        if hours != 12:
            hours += 12
    return hours * 60 + minutes


def parse_meetings(days_times: str | None) -> list[Meeting]:
    """
    Parse a CUNY DaysAndTimes string into a list of Meeting records.
    Returns [] for TBA, empty, None, or any unrecognised format.
    """
    if not days_times:
        return []
    s = days_times.strip()
    if not s or s.upper() == "TBA":
        return []

    meetings: list[Meeting] = []
    for m in _MEETING_RE.finditer(s):
        day_block, start_str, end_str = m.group(1), m.group(2), m.group(3)
        start = _to_minutes(start_str)
        end = _to_minutes(end_str)
        for day_code in _DAY_TOKEN_RE.findall(day_block):
            day_int = _DAY_TO_INT.get(day_code)
            if day_int is not None:
                meetings.append(Meeting(day_of_week=day_int, start_minute=start, end_minute=end))

    return meetings


def overlaps(a: Meeting, b: Meeting) -> bool:
    """Return True if two meetings overlap (same day, overlapping time ranges)."""
    if a.day_of_week != b.day_of_week:
        return False
    return a.start_minute < b.end_minute and b.start_minute < a.end_minute
