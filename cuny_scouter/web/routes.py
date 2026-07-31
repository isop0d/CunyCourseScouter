from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, cast, String
from sqlalchemy.orm import Session

from cuny_scouter.db.models import Section, ScrapeRun, Student, Watch
from cuny_scouter.db.session import get_session
from cuny_scouter.web.auth import discord_authorize_url, exchange_code, fetch_discord_user

templates = Jinja2Templates(directory="cuny_scouter/web/templates")
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

DAYS = [
    ("Mo", "Mon"), ("Tu", "Tue"), ("We", "Wed"),
    ("Th", "Thu"), ("Fr", "Fri"), ("Sa", "Sat"),
]

MODES = [
    ("In Person", "In Person"),
    ("Online Synchronous", "Online Sync"),
    ("Online Asynchronous", "Online Async"),
    ("Hybrid Synchronous", "Hybrid Sync"),
    ("Hybrid Asynchronous", "Hybrid Async"),
    ("Online Mix", "Online Mix"),
    ("HyField", "HyField"),
]


def _apply_filters(query, q: str, subject: str, status: str, mode: str, days: list[str]):
    if subject:
        query = query.filter(Section.subject == subject)
    if status:
        query = query.filter(Section.status == status)
    if mode:
        query = query.filter(Section.instruction_mode == mode)
    if days:
        for day in days:
            query = query.filter(Section.days_times.ilike(f"%{day}%"))
    if q:
        term = f"%{q}%"
        query = query.filter(or_(
            Section.course_name.ilike(term),
            Section.instructor.ilike(term),
            Section.section_code.ilike(term),
            Section.course_number.ilike(term),
            Section.subject.ilike(term),
            cast(Section.class_number, String).ilike(term),
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
    days: list[str] = [],
    db: Session = Depends(get_session),
):
    student = _current_student(request, db)
    watching = _watched_classes(student)

    base_query = db.query(Section).order_by(Section.subject, Section.course_number, Section.section_code)
    sections = _apply_filters(base_query, q, subject, status, mode, days).all()

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
        "days_options": DAYS,
        "mode_options": MODES,
        "q": q,
        "selected_subject": subject,
        "selected_status": status,
        "selected_mode": mode,
        "selected_days": days,
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
    days: list[str] = [],
    db: Session = Depends(get_session),
):
    student = _current_student(request, db)
    watching = _watched_classes(student)

    base_query = db.query(Section).order_by(Section.subject, Section.course_number, Section.section_code)
    sections = _apply_filters(base_query, q, subject, status, mode, days).all()

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
        user_data = await fetch_discord_user(token_data["access_token"])
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
