import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from cuny_scouter.db.models import ScheduleEntry, Section, SectionMeeting, ScrapeRun, Student, Watch
from cuny_scouter.db.session import get_session
from cuny_scouter.web.auth import discord_authorize_url, exchange_code, fetch_discord_user, join_guild

_ET = ZoneInfo("America/New_York")

templates = Jinja2Templates(directory="cuny_scouter/web/templates")
def _to_et(dt):
    if not dt:
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_ET)

templates.env.filters["to_et"] = _to_et
router = APIRouter()


def _current_student(request: Request, db: Session) -> Student | None:
    discord_id = request.session.get("discord_id")
    if not discord_id:
        return None
    return db.query(Student).filter(Student.discord_id == discord_id).first()


def _watched_classes(student: Student | None) -> set[int]:
    if not student:
        return set()
    return {w.class_number for w in student.watches if w.active}


# ── Section browser ──────────────────────────────────────────────────────────

MODES = [
    ("In Person", "In Person"),
    ("Online Synchronous", "Online Sync"),
    ("Online Asynchronous", "Online Async"),
    ("Hybrid Synchronous", "Hybrid Sync"),
    ("Hybrid Asynchronous", "Hybrid Async"),
    ("Online Mix", "Online Mix"),
    ("HyField", "HyField"),
]


def _apply_filters(query, q: str, subject: str, status: str, mode: str):
    if subject:
        query = query.filter(Section.subject == subject)
    if status:
        query = query.filter(Section.status == status)
    if mode:
        query = query.filter(Section.instruction_mode == mode)
    if q:
        for token in q.split():
            term = f"%{token}%"
            query = query.filter(or_(
                Section.course_name.ilike(term),
                Section.instructor.ilike(term),
                Section.course_number.ilike(term),
                Section.subject.ilike(term),
            ))
    return query


def _group_into_courses(sections) -> list[dict]:
    courses: dict[tuple, dict] = {}
    for s in sections:
        key = (s.subject, s.course_number)
        if key not in courses:
            courses[key] = {
                "subject": s.subject,
                "course_number": s.course_number,
                "course_name": s.course_name,
                "sections": [],
            }
        courses[key]["sections"].append(s)
    return list(courses.values())


def _subject_list(db: Session) -> list[tuple[str, str]]:
    rows = (
        db.query(Section.subject, Section.course_name)
        .distinct(Section.subject)
        .order_by(Section.subject)
        .all()
    )
    # Return (code, label) — use the subject code as label since names vary per course
    return [(row[0], row[0]) for row in rows]


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    q: str = "",
    subject: str = "",
    status: str = "",
    mode: str = "",
    db: Session = Depends(get_session),
):
    student = _current_student(request, db)
    watching = _watched_classes(student)

    base_query = db.query(Section).order_by(Section.subject, Section.course_number, Section.section_code)
    sections = _apply_filters(base_query, q, subject, status, mode).all()

    last_run = (
        db.query(ScrapeRun)
        .filter(ScrapeRun.status.in_(["ok", "partial"]))
        .order_by(ScrapeRun.finished_at.desc())
        .first()
    )

    return templates.TemplateResponse(request, "index.html", {
        "courses": _group_into_courses(sections),
        "section_count": len(sections),
        "subject_list": _subject_list(db),
        "mode_options": MODES,
        "q": q,
        "selected_subject": subject,
        "selected_status": status,
        "selected_mode": mode,
        "watching": watching,
        "student": student,
        "last_run": last_run,
    })


@router.get("/sections/partial", response_class=HTMLResponse)
async def sections_partial(
    request: Request,
    q: str = "",
    subject: str = "",
    status: str = "",
    mode: str = "",
    db: Session = Depends(get_session),
):
    student = _current_student(request, db)
    watching = _watched_classes(student)

    base_query = db.query(Section).order_by(Section.subject, Section.course_number, Section.section_code)
    sections = _apply_filters(base_query, q, subject, status, mode).all()

    return templates.TemplateResponse(request, "partials/courses.html", {
        "courses": _group_into_courses(sections),
        "section_count": len(sections),
        "watching": watching,
        "student": student,
    })


# ── Watch management ─────────────────────────────────────────────────────────

@router.post("/watch/{class_number}", response_class=HTMLResponse)
async def add_watch(class_number: int, request: Request, db: Session = Depends(get_session)):
    student = _current_student(request, db)
    if not student:
        return HTMLResponse('<a href="/auth/discord" class="btn btn-discord btn-sm">Login to watch</a>')

    section = db.get(Section, class_number)
    if not section:
        raise HTTPException(status_code=404)

    existing = db.query(Watch).filter(
        Watch.student_id == student.id,
        Watch.class_number == class_number,
    ).first()

    if existing:
        existing.active = True
        existing.deactivated_at = None
    else:
        db.add(Watch(student_id=student.id, class_number=class_number))
    db.commit()

    return templates.TemplateResponse(request, "partials/watch_button.html", {
        "section": section,
        "is_watching": True,
        "student": student,
    })


@router.delete("/watch/{class_number}", response_class=HTMLResponse)
async def remove_watch(class_number: int, request: Request, db: Session = Depends(get_session)):
    student = _current_student(request, db)
    if not student:
        raise HTTPException(status_code=401)

    section = db.get(Section, class_number)
    watch = db.query(Watch).filter(
        Watch.student_id == student.id,
        Watch.class_number == class_number,
        Watch.active == True,
    ).first()

    if watch:
        watch.active = False
        watch.deactivated_at = datetime.now(timezone.utc)
        db.commit()

    return templates.TemplateResponse(request, "partials/watch_button.html", {
        "section": section,
        "is_watching": False,
        "student": student,
    })


@router.get("/me", response_class=HTMLResponse)
async def my_watches(request: Request, db: Session = Depends(get_session)):
    student = _current_student(request, db)
    if not student:
        return RedirectResponse("/")

    watches = db.query(Watch).filter(
        Watch.student_id == student.id,
        Watch.active == True,
    ).all()

    sections = {s.class_number: s for s in db.query(Section).all()}

    return templates.TemplateResponse(request, "me.html", {
        "student": student,
        "watches": watches,
        "sections": sections,
    })


# ── Discord OAuth ─────────────────────────────────────────────────────────────

@router.get("/auth/discord")
async def auth_discord():
    return RedirectResponse(discord_authorize_url())


@router.get("/auth/discord/callback")
async def auth_discord_callback(code: str, request: Request, db: Session = Depends(get_session)):
    try:
        token_data = await exchange_code(code)
        access_token = token_data["access_token"]
        user_data = await fetch_discord_user(access_token)
        await join_guild(user_data["id"], access_token)
    except Exception:
        raise HTTPException(status_code=400, detail="Discord authentication failed.")

    discord_id = user_data["id"]
    username = user_data.get("username", "unknown")
    avatar = user_data.get("avatar")

    student = db.query(Student).filter(Student.discord_id == discord_id).first()
    if student:
        student.discord_username = username
        student.discord_avatar = avatar
        student.last_seen_at = datetime.now(timezone.utc)
    else:
        student = Student(discord_id=discord_id, discord_username=username, discord_avatar=avatar)
        db.add(student)
    db.commit()

    request.session["discord_id"] = discord_id
    return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


# ── Schedule builder ─────────────────────────────────────────────────────────

_GRID_START = 7 * 60    # 7:00 AM in minutes
_GRID_END = 22 * 60     # 10:00 PM in minutes
_GRID_SPAN = _GRID_END - _GRID_START
_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_HOUR_LABELS = list(range(7, 22))  # 7am..9pm labels


def _assign_lanes_for_day(day_meetings: list) -> list[dict]:
    """Assign side-by-side overlap lanes for a single day's meeting list."""
    if not day_meetings:
        return []

    sorted_ms = sorted(day_meetings, key=lambda x: (x[1].start_minute, x[0].class_number))

    lanes: list[list] = []
    assignments: dict = {}  # (class_number, start, end) -> lane_index

    for section, meeting in sorted_ms:
        key = (section.class_number, meeting.start_minute, meeting.end_minute)
        placed = False
        for i, lane in enumerate(lanes):
            last_s, last_m = lane[-1]
            if last_m.end_minute <= meeting.start_minute:
                lane.append((section, meeting))
                assignments[key] = i
                placed = True
                break
        if not placed:
            assignments[key] = len(lanes)
            lanes.append([(section, meeting)])

    result = []
    for section, meeting in sorted_ms:
        key = (section.class_number, meeting.start_minute, meeting.end_minute)
        my_lane = assignments[key]

        concurrent_lanes = {
            assignments[(s2.class_number, m2.start_minute, m2.end_minute)]
            for s2, m2 in sorted_ms
            if m2.start_minute < meeting.end_minute and meeting.start_minute < m2.end_minute
        }

        sorted_concurrent = sorted(concurrent_lanes)
        lane_index = sorted_concurrent.index(my_lane)
        lane_count = len(concurrent_lanes)

        start = max(meeting.start_minute, _GRID_START)
        end = min(meeting.end_minute, _GRID_END)
        if start >= end:
            continue

        result.append({
            "section": section,
            "meeting": meeting,
            "day_of_week": meeting.day_of_week,
            "is_conflict": lane_count > 1,
            "top_pct": round((start - _GRID_START) / _GRID_SPAN * 100, 3),
            "height_pct": round((end - start) / _GRID_SPAN * 100, 3),
            "left_pct": round(lane_index / lane_count * 100, 3),
            "width_pct": round(100 / lane_count, 3),
        })

    return result


def _compute_grid_events(sections, meetings_by_section):
    unscheduled = []
    day_meetings: dict[int, list] = defaultdict(list)

    for section in sections:
        sm = meetings_by_section.get(section.class_number, [])
        if not sm:
            unscheduled.append(section)
        else:
            for m in sm:
                day_meetings[m.day_of_week].append((section, m))

    events = []
    for day in sorted(day_meetings):
        events.extend(_assign_lanes_for_day(day_meetings[day]))

    return events, unscheduled


@router.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request, db: Session = Depends(get_session)):
    student = _current_student(request, db)
    saved_ids: list[int] = []
    if student:
        saved_ids = [
            e.section_id
            for e in db.query(ScheduleEntry).filter(ScheduleEntry.student_id == student.id).all()
        ]
    return templates.TemplateResponse(request, "schedule.html", {
        "student": student,
        "watching": _watched_classes(student),
        "saved_ids_json": json.dumps(saved_ids),
        "subject_list": _subject_list(db),
        "mode_options": MODES,
    })


@router.get("/schedule/grid", response_class=HTMLResponse)
async def schedule_grid_partial(
    request: Request,
    ids: Annotated[list[int], Query()] = [],
    db: Session = Depends(get_session),
):
    if not ids:
        return templates.TemplateResponse(request, "partials/schedule_grid.html", {
            "events": [],
            "unscheduled": [],
            "days": _DAY_NAMES[:5],
            "hour_labels": _HOUR_LABELS,
            "grid_start": _GRID_START,
            "grid_span": _GRID_SPAN,
        })

    sections = db.query(Section).filter(Section.class_number.in_(ids)).all()
    db_meetings = db.query(SectionMeeting).filter(SectionMeeting.section_id.in_(ids)).all()

    meetings_by_section: dict[int, list] = defaultdict(list)
    for m in db_meetings:
        meetings_by_section[m.section_id].append(m)

    events, unscheduled = _compute_grid_events(sections, meetings_by_section)

    show_sat = any(e["day_of_week"] == 5 for e in events)
    days = _DAY_NAMES[:6] if show_sat else _DAY_NAMES[:5]

    return templates.TemplateResponse(request, "partials/schedule_grid.html", {
        "events": events,
        "unscheduled": unscheduled,
        "days": days,
        "hour_labels": _HOUR_LABELS,
        "grid_start": _GRID_START,
        "grid_span": _GRID_SPAN,
    })


@router.get("/schedule/search", response_class=HTMLResponse)
async def schedule_search(
    request: Request,
    q: str = "",
    subject: str = "",
    status: str = "",
    mode: str = "",
    db: Session = Depends(get_session),
):
    student = _current_student(request, db)
    watching = _watched_classes(student)
    base_query = db.query(Section).order_by(Section.subject, Section.course_number, Section.section_code)
    sections = _apply_filters(base_query, q, subject, status, mode).all()
    return templates.TemplateResponse(request, "partials/courses.html", {
        "courses": _group_into_courses(sections),
        "section_count": len(sections),
        "watching": watching,
        "student": student,
        "show_add": True,
    })


@router.post("/schedule/save", response_class=HTMLResponse)
async def schedule_save(
    request: Request,
    ids: Annotated[list[int], Form()] = [],
    db: Session = Depends(get_session),
):
    student = _current_student(request, db)
    if not student:
        return HTMLResponse('<a href="/auth/discord" class="btn btn-discord btn-sm">Login to save</a>')

    existing = {
        e.section_id
        for e in db.query(ScheduleEntry).filter(ScheduleEntry.student_id == student.id).all()
    }
    new_ids = set(ids)

    for sid in existing - new_ids:
        db.query(ScheduleEntry).filter(
            ScheduleEntry.student_id == student.id,
            ScheduleEntry.section_id == sid,
        ).delete()
    for sid in new_ids - existing:
        db.add(ScheduleEntry(student_id=student.id, section_id=sid))
    db.commit()

    return HTMLResponse('<span class="save-status">Saved ✓</span>')


# ── Health check ──────────────────────────────────────────────────────────────

@router.get("/health")
async def health(db: Session = Depends(get_session)):
    last_run = db.query(ScrapeRun).order_by(ScrapeRun.started_at.desc()).first()
    return {
        "status": "ok",
        "last_run_at": last_run.finished_at.isoformat() if last_run and last_run.finished_at else None,
        "last_run_status": last_run.status if last_run else None,
        "sections_found": last_run.sections_found if last_run else None,
    }
