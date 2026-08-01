import time

import requests
from bs4 import BeautifulSoup

from cuny_scouter.config import settings

BASE = "https://globalsearch.cuny.edu/CFGlobalSearchTool"


class ScraperError(Exception):
    pass


class ScraperNoResultsError(ScraperError):
    pass


def _step0_and_step1(session: requests.Session) -> None:
    """Seed JSESSIONID and POST institution + term. Mutates session in place."""
    headers = {"User-Agent": settings.scraper_user_agent}

    session.get(f"{BASE}/search.jsp", headers=headers, timeout=30)

    if "JSESSIONID" not in session.cookies:
        raise ScraperError("No JSESSIONID after initial GET — server may be down.")

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


def fetch_subjects() -> list[tuple[str, str]]:
    """
    Return all available subjects for the configured institution and term as
    [(subject_code, subject_name), ...], e.g. [("ACCT", "Accounting"), ...].
    """
    headers = {"User-Agent": settings.scraper_user_agent}
    session = requests.Session()
    _step0_and_step1(session)

    # After Step 1 the server returns a page with the subject dropdown
    resp = session.get(
        f"{BASE}/CFSearchToolController",
        headers=headers,
        timeout=30,
    )
    resp.encoding = "ISO-8859-1"

    soup = BeautifulSoup(resp.text, "html.parser")
    select = soup.find("select", {"name": "subject_name"})

    if not select:
        # Step 1 POST itself returns the subject page — use that response
        # Re-do with POST and capture the response
        resp2 = session.post(
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
        resp2.encoding = "ISO-8859-1"
        soup = BeautifulSoup(resp2.text, "html.parser")
        select = soup.find("select", {"name": "subject_name"})

    if not select:
        raise ScraperError("Could not find subject dropdown in Step 1 response.")

    subjects = []
    for option in select.find_all("option"):
        code = option.get("value", "").strip()
        name = option.get_text(strip=True)
        if code:  # skip the blank "all" option
            subjects.append((code, name))

    return subjects


CAREER_LABELS = {
    "UGRD": "Undergraduate",
    "GRAD": "Graduate",
}


def fetch_subject_html(subject_code: str, subject_name: str, career: str = "UGRD") -> str:
    """
    Execute the three-step session flow for a specific subject and return the
    raw HTML response decoded as ISO-8859-1.
    """
    headers = {"User-Agent": settings.scraper_user_agent}
    session = requests.Session()
    _step0_and_step1(session)

    resp = session.post(
        f"{BASE}/CFSearchToolController",
        headers=headers,
        timeout=30,
        data={
            "selectedSubjectName": subject_name,
            "subject_name": subject_code,
            "selectedCCareerName": CAREER_LABELS.get(career, career),
            "courseCareer": career,
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
    html = resp.text

    # CUNY returns 200 + criteria page when the search yields nothing
    if "The search returns no results" in html or "classfound_msg" not in html:
        raise ScraperNoResultsError(f"{subject_code} ({career}): no sections found")

    return html


def fetch_class_html(class_number: int) -> str:
    """
    Fetch the section page for a single class number.
    Uses the class_nbr field on CUNY's search form to bypass subject-level scraping.
    Raises ScraperNoResultsError if the class is not found.
    """
    headers = {"User-Agent": settings.scraper_user_agent}
    session = requests.Session()
    _step0_and_step1(session)

    resp = session.post(
        f"{BASE}/CFSearchToolController",
        headers=headers,
        timeout=30,
        data={
            "selectedSubjectName": "",
            "subject_name": "",
            "selectedCCareerName": "",
            "courseCareer": "",
            "selectedCAttrName": "",
            "courseAttr": "",
            "selectedCAttrVName": "",
            "courseAttValue": "",
            "selectedReqDName": "",
            "reqDesignation": "",
            "open_class": "",
            "class_nbr": str(class_number),
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
    html = resp.text

    if "The search returns no results" in html or "classfound_msg" not in html:
        raise ScraperNoResultsError(f"Class {class_number}: not found")

    return html


def fetch_all_subjects(
    delay_seconds: float = 1.5,
) -> list[tuple[tuple[str, str], str]]:
    """
    Fetch HTML for every subject available at the configured institution/term.
    Returns [(subject_code, subject_name), html] pairs.
    Sleeps delay_seconds between requests to avoid hammering the server.
    """
    import logging
    log = logging.getLogger(__name__)

    subjects = fetch_subjects()
    results = []
    for i, (code, name) in enumerate(subjects):
        if i > 0:
            time.sleep(delay_seconds)

        for career in ("UGRD", "GRAD"):
            if career == "GRAD":
                time.sleep(delay_seconds)
            try:
                html = fetch_subject_html(code, name, career=career)
                results.append(((code, name), html))
            except ScraperNoResultsError:
                pass  # No sections for this career level — normal for most subjects
            except ScraperError as exc:
                log.warning(f"  {code} ({career}): failed to fetch — {exc}")

    return results
