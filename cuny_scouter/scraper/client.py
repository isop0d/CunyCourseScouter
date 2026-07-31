import requests

from cuny_scouter.config import settings

BASE = "https://globalsearch.cuny.edu/CFGlobalSearchTool"


class ScraperError(Exception):
    pass


def fetch_subject_html() -> str:
    """
    Execute the three-step session flow against CUNY Global Search and return
    the raw HTML response for the configured subject/term/institution.

    Step 0: GET search.jsp — seeds the JSESSIONID session cookie.
    Step 1: POST institution + term selection.
    Step 2: POST subject-level search filters.

    open_class is intentionally left blank so closed and waitlisted sections
    are included — sending "O" hides all non-open sections.
    """
    headers = {"User-Agent": settings.scraper_user_agent}
    session = requests.Session()

    session.get(f"{BASE}/search.jsp", headers=headers, timeout=30)

    if "JSESSIONID" not in session.cookies:
        raise ScraperError("No JSESSIONID cookie after initial GET — server may be down.")

    session.post(
        f"{BASE}/CFSearchToolController",
        headers=headers,
        timeout=30,
        data={
            "selectedInstName": settings.institution_name,
            "inst_selection": settings.institution,
            "selectedTermName": settings.term_name,
            "term_value": settings.term_code,
            "next_btn": "Next",
        },
    )

    resp = session.post(
        f"{BASE}/CFSearchToolController",
        headers=headers,
        timeout=30,
        data={
            "selectedSubjectName": settings.subject_name,
            "subject_name": settings.subject,
            "selectedCCareerName": "Undergraduate",
            "courseCareer": "UGRD",
            "selectedCAttrName": "",
            "courseAttr": "",
            "selectedCAttrVName": "",
            "courseAttValue": "",
            "selectedReqDName": "",
            "reqDesignation": "",
            "open_class": "",
            "selectedSessionName": "",
            "class_session": "",
            "selectedModeInsName": "",
            "meetingStart": "LT",
            "selectedMeetingStartName": "less than",
            "meetingStartText": "",
            "AndMeetingStartText": "",
            "meetingEnd": "LE",
            "selectedMeetingEndName": "less than or equal to",
            "meetingEndText": "",
            "AndMeetingEndText": "",
            "daysOfWeek": "I",
            "selectedDaysOfWeekName": "include only these days",
            "instructor": "B",
            "selectedInstructorName": "begins with",
            "instructorName": "",
            "search_btn_search": "Search",
        },
    )

    if resp.status_code != 200:
        raise ScraperError(f"Search POST returned HTTP {resp.status_code}")

    resp.encoding = "ISO-8859-1"
    return resp.text
