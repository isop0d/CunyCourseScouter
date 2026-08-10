import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from cuny_scouter.config import settings
from cuny_scouter.db.models import Professor, ScheduleEntry, Section, SectionMeeting, ScrapeRun, Student, Watch
from cuny_scouter.db.session import get_session, SessionLocal
from cuny_scouter.professors import match_rmp, normalize_instructor
from cuny_scouter.rmp import search_teacher
from cuny_scouter.web.auth import discord_authorize_url, exchange_code, fetch_discord_user, join_guild

log = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

templates = Jinja2Templates(directory="cuny_scouter/web/templates")
def _to_et(dt):
    if not dt:
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_ET)

templates.env.filters["to_et"] = _to_et
templates.env.globals["site_institution"] = settings.institution_name.rstrip(" |").strip()
templates.env.globals["site_term"] = settings.term_name.replace("Term", "").strip()
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


def _apply_filters(query, q: str, subject: str, status: str, mode: str, open_only: bool = False):
    if subject:
        query = query.filter(Section.subject == subject)
    if status:
        query = query.filter(Section.status == status)
    elif open_only:
        query = query.filter(Section.status == "open")
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


_PAGE_SIZE = 25


def _fetch_professor_map(
    db: Session, sections
) -> tuple[dict[str, "Professor"], list[tuple[str, str, str]]]:
    """
    Return (prof_map, missing) where:
    - prof_map: normalized_name -> Professor for rows already in DB
    - missing:  [(key, last, initial)] for instructors with no DB row yet
    """
    keys: dict[str, tuple[str, str]] = {}  # key -> (last, initial)
    for s in sections:
        if s.instructor:
            key, last, initial = normalize_instructor(s.instructor)
            if key:
                keys[key] = (last, initial)
    if not keys:
        return {}, []
    profs = db.query(Professor).filter(Professor.normalized_name.in_(keys)).all()
    prof_map = {p.normalized_name: p for p in profs}
    missing = [(k, v[0], v[1]) for k, v in keys.items() if k not in prof_map]
    return prof_map, missing


def _rmp_fetch_bg(missing: list[tuple[str, str, str]]) -> None:
    """Background task: fetch RMP for professors not yet in DB after the response is sent."""
    if not missing:
        return
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        for key, last, initial in missing:
            if db.query(Professor).filter(Professor.normalized_name == key).first():
                continue  # another request already fetched it
            nodes = search_teacher(last, settings.rmp_school_id)
            node, status = match_rmp(last, initial, nodes)
            display = f"{last}, {initial}." if initial else last
            prof = Professor(
                name=f"{node['firstName']} {node['lastName']}".strip() if node else display,
                normalized_name=key,
                rmp_legacy_id=node.get("legacyId") if node else None,
                avg_rating=node.get("avgRating") if node else None,
                avg_difficulty=node.get("avgDifficulty") if node else None,
                num_ratings=node.get("numRatings", 0) if node else 0,
                would_take_again=node.get("wouldTakeAgainPercent") if node else None,
                match_status=status,
                rmp_url=node.get("rmpUrl") if node else None,
                fetched_at=now,
            )
            db.add(prof)
            time.sleep(0.3)
        db.commit()
    except Exception:
        log.exception("Background RMP fetch failed")
    finally:
        db.close()


def _attach_professor(section, prof_map: dict) -> "Professor | None":
    if not section.instructor:
        return None
    key, _, _ = normalize_instructor(section.instructor)
    return prof_map.get(key)


def _group_into_courses(sections, prof_map: dict | None = None) -> list[dict]:
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
        entry = {"section": s, "professor": _attach_professor(s, prof_map) if prof_map is not None else None}
        courses[key]["sections"].append(entry)
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
    background_tasks: BackgroundTasks,
    q: str = "",
    subject: str = "",
    status: str = "",
    mode: str = "",
    open_only: bool = False,
    db: Session = Depends(get_session),
):
    student = _current_student(request, db)
    watching = _watched_classes(student)

    base_query = db.query(Section).order_by(Section.subject, Section.course_number, Section.section_code)
    filtered = _apply_filters(base_query, q, subject, status, mode, open_only)
    total_count = filtered.count()
    sections = filtered.limit(_PAGE_SIZE).all()
    prof_map, missing = _fetch_professor_map(db, sections)
    if missing:
        background_tasks.add_task(_rmp_fetch_bg, missing)

    last_run = (
        db.query(ScrapeRun)
        .filter(ScrapeRun.status.in_(["ok", "partial"]))
        .order_by(ScrapeRun.finished_at.desc())
        .first()
    )

    next_offset = _PAGE_SIZE
    has_more = next_offset < total_count

    return templates.TemplateResponse(request, "index.html", {
        "courses": _group_into_courses(sections, prof_map),
        "section_count": total_count,
        "shown_count": len(sections),
        "has_more": has_more,
        "next_offset": next_offset,
        "is_append": False,
        "subject_list": _subject_list(db),
        "mode_options": MODES,
        "q": q,
        "selected_subject": subject,
        "selected_status": status,
        "selected_mode": mode,
        "open_only": open_only,
        "watching": watching,
        "student": student,
        "last_run": last_run,
    })


@router.get("/sections/partial", response_class=HTMLResponse)
async def sections_partial(
    request: Request,
    background_tasks: BackgroundTasks,
    q: str = "",
    subject: str = "",
    status: str = "",
    mode: str = "",
    open_only: bool = False,
    offset: int = 0,
    db: Session = Depends(get_session),
):
    student = _current_student(request, db)
    watching = _watched_classes(student)

    base_query = db.query(Section).order_by(Section.subject, Section.course_number, Section.section_code)
    filtered = _apply_filters(base_query, q, subject, status, mode, open_only)
    total_count = filtered.count()
    sections = filtered.offset(offset).limit(_PAGE_SIZE).all()
    prof_map, missing = _fetch_professor_map(db, sections)
    if missing:
        background_tasks.add_task(_rmp_fetch_bg, missing)

    next_offset = offset + _PAGE_SIZE
    has_more = next_offset < total_count

    return templates.TemplateResponse(request, "partials/courses.html", {
        "courses": _group_into_courses(sections, prof_map),
        "section_count": total_count,
        "shown_count": offset + len(sections),
        "watching": watching,
        "student": student,
        "has_more": has_more,
        "next_offset": next_offset,
        "is_append": offset > 0,
        "is_htmx": True,
        "q": q,
        "selected_subject": subject,
        "selected_status": status,
        "selected_mode": mode,
        "open_only": open_only,
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


@router.get("/faq", response_class=HTMLResponse)
async def faq(request: Request, db: Session = Depends(get_session)):
    student = _current_student(request, db)
    return templates.TemplateResponse(request, "faq.html", {"student": student})


# ── Schedule builder ─────────────────────────────────────────────────────────

def _fmt_minute(m: int) -> str:
    h, mn = divmod(m, 60)
    period = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}:{mn:02d}{period}"


_GRID_START = 7 * 60    # 7:00 AM in minutes
_GRID_END = 21 * 60     # 9:00 PM in minutes
_GRID_SPAN = _GRID_END - _GRID_START   # 840 minutes
_GRID_HEIGHT = 600      # px — decoupled from span so 1px != 1min
_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_HOUR_LABELS = list(range(7, 21))  # 7am..8pm labels; 9pm is the bottom edge


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
            "start_time": _fmt_minute(meeting.start_minute),
            "end_time": _fmt_minute(meeting.end_minute),
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
    })


@router.get("/schedule/grid", response_class=HTMLResponse)
async def schedule_grid_partial(
    request: Request,
    ids: Annotated[list[int], Query()] = [],
    db: Session = Depends(get_session),
):
    if not ids:
        return templates.TemplateResponse(request, "partials/schedule_panels.html", {
            "events": [],
            "unscheduled": [],
            "days": _DAY_NAMES[:5],
            "hour_labels": _HOUR_LABELS,
            "grid_start": _GRID_START,
            "grid_span": _GRID_SPAN,
            "grid_height": _GRID_HEIGHT,
            "class_list": [],
            "color_map": {},
        })

    section_map = {
        s.class_number: s
        for s in db.query(Section).filter(Section.class_number.in_(ids)).all()
    }
    db_meetings = db.query(SectionMeeting).filter(SectionMeeting.section_id.in_(ids)).all()

    meetings_by_section: dict[int, list] = defaultdict(list)
    for m in db_meetings:
        meetings_by_section[m.section_id].append(m)

    # Build class_list in ids order (preserves insertion order from localStorage)
    color_map: dict[int, int] = {}
    class_list = []
    for idx, cn in enumerate(ids):
        s = section_map.get(cn)
        if s:
            cidx = idx % 6
            color_map[cn] = cidx
            class_list.append({"section": s, "color_idx": cidx, "prof": None})

    # Fetch professor info for class list
    all_sections = list(section_map.values())
    prof_map, _ = _fetch_professor_map(db, all_sections)
    for item in class_list:
        item["prof"] = _attach_professor(item["section"], prof_map)

    events, unscheduled = _compute_grid_events(all_sections, meetings_by_section)

    show_sat = any(e["day_of_week"] == 5 for e in events)
    days = _DAY_NAMES[:6] if show_sat else _DAY_NAMES[:5]

    return templates.TemplateResponse(request, "partials/schedule_panels.html", {
        "events": events,
        "unscheduled": unscheduled,
        "days": days,
        "hour_labels": _HOUR_LABELS,
        "grid_start": _GRID_START,
        "grid_span": _GRID_SPAN,
        "grid_height": _GRID_HEIGHT,
        "class_list": class_list,
        "color_map": color_map,
    })


@router.get("/schedule/search", response_class=HTMLResponse)
async def schedule_search(
    request: Request,
    background_tasks: BackgroundTasks,
    q: str = "",
    db: Session = Depends(get_session),
):
    if not q:
        return HTMLResponse("")
    base_query = db.query(Section).order_by(Section.subject, Section.course_number, Section.section_code)
    filtered = _apply_filters(base_query, q, "", "", "")
    sections = filtered.limit(_PAGE_SIZE).all()
    prof_map, missing = _fetch_professor_map(db, sections)
    if missing:
        background_tasks.add_task(_rmp_fetch_bg, missing)
    return templates.TemplateResponse(request, "partials/schedule_search_results.html", {
        "courses": _group_into_courses(sections, prof_map),
        "is_htmx": True,
        "q": q,
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
