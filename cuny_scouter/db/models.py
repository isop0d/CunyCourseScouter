from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    institution: Mapped[str] = mapped_column(String(20), default="BAR01")
    term_code: Mapped[str] = mapped_column(String(10), default="1269")
    subject: Mapped[str] = mapped_column(String(10), default="CMIS")
    sections_found: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|ok|error|empty
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)

    snapshots: Mapped[list["SectionSnapshot"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class Section(Base):
    __tablename__ = "sections"

    class_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    term_code: Mapped[str] = mapped_column(String(10))
    institution: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(10))
    course_number: Mapped[str] = mapped_column(String(10))
    course_name: Mapped[str] = mapped_column(String(200))
    section_code: Mapped[str] = mapped_column(String(30))
    days_times: Mapped[str] = mapped_column(String(300), default="")
    room: Mapped[str] = mapped_column(String(200), default="")
    instructor: Mapped[str] = mapped_column(String(200), default="")
    instruction_mode: Mapped[str] = mapped_column(String(100), default="")
    meeting_dates: Mapped[str] = mapped_column(String(300), default="")
    course_topic: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20))
    fetch_key: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_seen_run: Mapped[int | None] = mapped_column(Integer, ForeignKey("scrape_runs.id"), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class SectionSnapshot(Base):
    __tablename__ = "section_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("scrape_runs.id", ondelete="CASCADE"))
    class_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    scraped_at: Mapped[datetime] = mapped_column(default=_now)

    run: Mapped["ScrapeRun"] = relationship(back_populates="snapshots")

    __table_args__ = (
        Index("idx_snapshots_class", "class_number", "scraped_at"),
    )


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    discord_id: Mapped[str] = mapped_column(String(20), unique=True)
    discord_username: Mapped[str] = mapped_column(String(32))
    discord_avatar: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(default=_now)

    watches: Mapped[list["Watch"]] = relationship(back_populates="student", cascade="all, delete-orphan")


class Watch(Base):
    __tablename__ = "watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(Integer, ForeignKey("students.id", ondelete="CASCADE"))
    class_number: Mapped[int] = mapped_column(Integer)
    notify_on: Mapped[str] = mapped_column(String(20), default="any")  # open|closed|waitlist|any
    auto_deactivate: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    deactivated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    student: Mapped["Student"] = relationship(back_populates="watches")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="watch", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("student_id", "class_number"),
        Index("idx_watches_class", "class_number", postgresql_where="active = TRUE"),
    )


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    watch_id: Mapped[int] = mapped_column(Integer, ForeignKey("watches.id", ondelete="CASCADE"))
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("scrape_runs.id"))
    from_status: Mapped[str] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    channel: Mapped[str] = mapped_column(String(20), default="discord")
    sent_at: Mapped[datetime] = mapped_column(default=_now)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    watch: Mapped["Watch"] = relationship(back_populates="notifications")

    __table_args__ = (
        UniqueConstraint("watch_id", "from_status", "to_status", name="uq_notifications_dedup"),
    )


class SectionMeeting(Base):
    __tablename__ = "section_meetings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    section_id: Mapped[int] = mapped_column(Integer, ForeignKey("sections.class_number", ondelete="CASCADE"))
    day_of_week: Mapped[int] = mapped_column(SmallInteger)   # 0=Mon .. 6=Sun
    start_minute: Mapped[int] = mapped_column(Integer)       # minutes since midnight
    end_minute: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        Index("idx_meetings_section", "section_id"),
    )


class ScheduleEntry(Base):
    __tablename__ = "schedule_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(Integer, ForeignKey("students.id", ondelete="CASCADE"))
    section_id: Mapped[int] = mapped_column(Integer, ForeignKey("sections.class_number"))
    added_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        UniqueConstraint("student_id", "section_id"),
    )


class Professor(Base):
    __tablename__ = "professors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    rmp_legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_difficulty: Mapped[float | None] = mapped_column(Float, nullable=True)
    num_ratings: Mapped[int] = mapped_column(Integer, default=0)
    would_take_again: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_status: Mapped[str] = mapped_column(String(20))   # matched|ambiguous|no_match|staff
    rmp_url: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(nullable=True)
