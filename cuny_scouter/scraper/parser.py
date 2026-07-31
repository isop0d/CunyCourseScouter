"""
Parse CUNY Global Search HTML responses into structured SectionRecord objects.

Key quirks of the target HTML:
- <tbody>/<tr> tags are unclosed; select td[data-label=...] directly, not via row parents
- Status is conveyed by img src, not alt text (alt is always "Open" regardless of status)
- open_class="" must be used in the POST; "O" hides all waitlisted sections
"""
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup


@dataclass
class SectionRecord:
    class_number: int
    term_code: str
    institution: str
    subject: str
    course_number: str
    course_name: str
    section_code: str
    days_times: str
    room: str
    instructor: str
    instruction_mode: str
    meeting_dates: str
    course_topic: str
    status: str  # "open" | "closed" | "waitlist"


class ParseError(Exception):
    pass


class StructuralValidationError(ParseError):
    pass


_STATUS_MAP = {
    "status_open.gif": "open",
    "status_closed.gif": "closed",
    "status_waiting.gif": "waitlist",
}


def _parse_status(td) -> str:
    img = td.find("img")
    if not img:
        raise ParseError(f"No img in Status td: {td}")
    src = img.get("src", "")
    for key, value in _STATUS_MAP.items():
        if key in src:
            return value
    raise ParseError(f"Unrecognised status img src: {src!r}")


def _parse_course_header(testing_msg_div) -> tuple[str, str, str]:
    """Return (subject, course_number, course_name) from a div.testing_msg text."""
    # Normalise non-breaking spaces (\xa0) to regular spaces before splitting
    text = testing_msg_div.get_text(separator=" ", strip=True).replace("\xa0", " ")
    # Format: "BUS 2000 - Business Fundamentals" or "CIS 2200 - Intro Info Systems"
    if " - " in text:
        left, course_name = text.split(" - ", 1)
        parts = left.strip().split()
        if len(parts) >= 2:
            subject = parts[0]
            course_number = parts[1]
        else:
            subject = ""
            course_number = left.strip()
    else:
        subject = ""
        course_number = ""
        course_name = text.strip()
    return subject, course_number, course_name


def parse_sections(
    html: str,
    term_code: str = "1269",
    institution: str = "BAR01",
    subject: str = "CMIS",
) -> list[SectionRecord]:
    soup = BeautifulSoup(html, "html.parser")

    # Declared count for structural validation
    msg_div = soup.find(class_="classfound_msg")
    declared_count: int | None = None
    if msg_div:
        m = re.search(r"(\d+)\s+class section", msg_div.get_text())
        if m:
            declared_count = int(m.group(1))

    if declared_count == 0:
        raise StructuralValidationError("Server returned 0 class sections — possible error page or maintenance.")

    records: list[SectionRecord] = []

    for table in soup.find_all("table", class_="classinfo"):
        course_header = table.find_previous("div", class_="testing_msg")
        if course_header:
            parsed_subject, course_number, course_name = _parse_course_header(course_header)
            # Use the subject code from the header (e.g. "BUS") rather than
            # the POST key (e.g. "BUAD") — they often differ.
            effective_subject = parsed_subject or subject
        else:
            course_number, course_name = "", ""
            effective_subject = subject

        for class_td in table.find_all("td", attrs={"data-label": "Class"}):
            row: dict[str, str] = {}
            node = class_td
            while node:
                if hasattr(node, "get") and node.get("data-label"):
                    label = node["data-label"]
                    if label == "Status":
                        row[label] = _parse_status(node)
                    else:
                        row[label] = node.get_text(strip=True)
                node = node.next_sibling

            try:
                records.append(
                    SectionRecord(
                        class_number=int(row["Class"]),
                        term_code=term_code,
                        institution=institution,
                        subject=effective_subject,
                        course_number=course_number,
                        course_name=course_name,
                        section_code=row.get("Section", ""),
                        days_times=row.get("DaysAndTimes", ""),
                        room=row.get("Room", ""),
                        instructor=row.get("Instructor", ""),
                        instruction_mode=row.get("Instruction Mode", ""),
                        meeting_dates=row.get("Meeting Dates", ""),
                        course_topic=row.get("Course Topic", ""),
                        status=row.get("Status", ""),
                    )
                )
            except (KeyError, ValueError) as exc:
                raise ParseError(f"Malformed row data {row}: {exc}") from exc

    if declared_count is not None and len(records) != declared_count:
        raise StructuralValidationError(
            f"Parsed {len(records)} sections but page declared {declared_count}. "
            "HTML structure may have changed."
        )

    if not records:
        raise StructuralValidationError(
            "No sections parsed and no classfound_msg present — likely an error page."
        )

    return records
