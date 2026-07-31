import requests

s = requests.Session()  # carries JSESSIONID across all three requests automatically

BASE = "https://globalsearch.cuny.edu/CFGlobalSearchTool"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; baruch-class-tracker/0.1)"}

# Step 0: land on the page to get an initial session cookie
s.get(f"{BASE}/search.jsp", headers=HEADERS)

# Step 1: select institution + term
resp1 = s.post(f"{BASE}/CFSearchToolController", headers=HEADERS, data={
    "selectedInstName": "Baruch College | ",
    "inst_selection": "BAR01",
    "selectedTermName": "2026 Fall Term",
    "term_value": "1269",
    "next_btn": "Next",
})

# Step 2: subject-level search — send every field, even the empty-string ones
resp2 = s.post(f"{BASE}/CFSearchToolController", headers=HEADERS, data={
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
    "open_class": "O",
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
})

print(resp2.status_code, len(resp2.text))
with open("results.html", "w", encoding="ISO-8859-1") as f:
    f.write(resp2.text)  # save it so you can eyeball the real structure
