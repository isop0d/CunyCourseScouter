"""
Risk 1 verification: does open_class="O" hide closed/waitlisted sections?

Runs the same two-step POST chain twice:
  - once with open_class="O"  (PoC default — open only)
  - once with open_class=""   (all sections)

Prints the declared section count from each response and saves the
all-sections HTML as results_all.html for inspection.
"""
import re
import requests

BASE = "https://globalsearch.cuny.edu/CFGlobalSearchTool"
HEADERS = {"User-Agent": "baruch-class-tracker/0.1-risk-check (contact: stefanekfernandez@gmail.com)"}

STEP1_DATA = {
    "selectedInstName": "Baruch College | ",
    "inst_selection": "BAR01",
    "selectedTermName": "2026 Fall Term",
    "term_value": "1269",
    "next_btn": "Next",
}

STEP2_BASE = {
    "selectedSubjectName": "Computer Information Systems",
    "subject_name": "CMIS",
    "selectedCCareerName": "Undergraduate",
    "courseCareer": "UGRD",
    "selectedCAttrName": "",
    "courseAttr": "",
    "selectedCAttrVName": "",
    "courseAttValue": "",
    "selectedReqDName": "",
    "reqDesignation": "",
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
}


def fetch(open_class_value: str) -> str:
    s = requests.Session()
    s.get(f"{BASE}/search.jsp", headers=HEADERS, timeout=30)
    s.post(f"{BASE}/CFSearchToolController", headers=HEADERS, data=STEP1_DATA, timeout=30)
    resp = s.post(
        f"{BASE}/CFSearchToolController",
        headers=HEADERS,
        data={**STEP2_BASE, "open_class": open_class_value},
        timeout=30,
    )
    resp.encoding = "ISO-8859-1"
    return resp.text


def extract_count(html: str) -> str:
    match = re.search(r'class="classfound_msg"[^>]*>\s*([^<]+)', html)
    return match.group(1).strip() if match else "classfound_msg NOT FOUND"


def count_status_imgs(html: str) -> dict:
    return {
        "open":     len(re.findall(r'status_open\.gif', html)),
        "closed":   len(re.findall(r'status_closed\.gif', html)),
        "waitlist": len(re.findall(r'status_waiting\.gif', html)),
    }


print("Fetching with open_class='O' (open only) ...")
html_open_only = fetch("O")
count_open_only = extract_count(html_open_only)
imgs_open_only = count_status_imgs(html_open_only)

print("Fetching with open_class='' (all sections) ...")
html_all = fetch("")
count_all = extract_count(html_all)
imgs_all = count_status_imgs(html_all)

print()
print("=" * 50)
print(f"open_class='O'  → {count_open_only}")
print(f"  img breakdown: {imgs_open_only}")
print()
print(f"open_class=''   → {count_all}")
print(f"  img breakdown: {imgs_all}")
print("=" * 50)

if imgs_open_only != imgs_all:
    print("\nRESULT: Filter IS hiding sections — remove open_class='O' from the scraper.")
else:
    print("\nRESULT: No difference — filter had no effect (safe to leave blank).")

with open("results_all.html", "w", encoding="ISO-8859-1") as f:
    f.write(html_all)
print("\nSaved all-sections response to results_all.html")
