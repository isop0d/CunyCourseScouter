# CUNY Course Scouter

A seat-tracker for Baruch College students. Search and browse Fall 2026 course sections, watch the ones you want, and get a Discord ping the moment a seat opens.

---

## How it works

```mermaid
flowchart TD
    A([Student visits site]) --> B[Browse & filter sections\ncourse name · instructor · code\nsubject · status · mode · days]
    B --> C{Logged in?}
    C -- No --> D[Login with Discord OAuth]
    D --> C
    C -- Yes --> E[Click Watch on a section]
    E --> F[(watches table\nactive = TRUE)]

    subgraph Worker ["Background Worker  (runs every 10 min)"]
        G([Poll cycle starts]) --> H[GET CUNY Global Search\nfresh session cookie]
        H --> I[POST institution + term\nPOST subject  ×63 subjects\nopen_class='' to include waitlisted]
        I --> J[Parse HTML response\nper section: class number, status,\ninstructor, days/times, mode]
        J --> K[Upsert sections table\nAppend section_snapshots]
        K --> L[Diff against previous\npoll's snapshots]
        L --> M{Any status\nchanges?}
        M -- No --> N([Sleep until next poll])
        M -- Yes --> O[Find active watches\nfor changed sections]
        O --> P{Dedup checks}
        P -- Already sent\ntoday --> N
        P -- Within 60 min\ncooldown --> N
        P -- OK to send --> Q[Post Discord embed\n@ mention student]
        Q --> R[Record in notifications\nauto-deactivate watch]
        R --> N
    end

    F -.->|next poll picks up| O
    Q -.->|DM or channel ping| S([Student gets notified])
    S --> T[Student claims seat\nin CUNYfirst]
```

---

## Features

- **Live search** — filter by course name, instructor, section code, class number, or subject as you type (300 ms debounce, no page reload)
- **Composable filters** — department dropdown, open/waitlist/closed status, instruction mode (in-person, online, hybrid), and day-of-week chip toggles all work together
- **Discord login** — one click via OAuth2, no password or email required
- **Watch / Unwatch** — HTMX toggle swaps just the button, no full reload
- **Smart dedup** — three layers prevent duplicate pings: unique DB index per transition, 60-minute cooldown window, and auto-deactivation after the first alert
- **2,300+ sections** — all 63 active Baruch subjects scraped every poll cycle

---

## Tech stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI + Jinja2 |
| Frontend interactivity | HTMX (CDN, no build step) |
| Database | PostgreSQL 16 (Docker) |
| ORM / migrations | SQLAlchemy 2.x + Alembic |
| Auth | Discord OAuth2 + signed session cookie |
| Notifications | Discord webhook embeds |
| Scraping | `requests` + BeautifulSoup4 |
| Config | pydantic-settings (`.env` file) |

---

## Project structure

```
cuny_scouter/
├── scraper/
│   ├── client.py        # HTTP session, 3-step POST chain to CUNY Global Search
│   └── parser.py        # HTML → SectionRecord dataclasses, structural validation
├── db/
│   ├── models.py        # SQLAlchemy ORM (sections, watches, students, notifications)
│   └── session.py       # engine + Session factory
├── diff.py              # compare snapshots → list[StatusEvent]
├── notifier.py          # Discord embed dispatch + 3-layer dedup
├── scheduler.py         # poll loop with exponential backoff
├── config.py            # all env vars via pydantic-settings
└── web/
    ├── __init__.py      # FastAPI app factory + SessionMiddleware
    ├── auth.py          # Discord OAuth2 flow
    ├── routes.py        # route handlers + filter logic
    └── templates/
        ├── base.html
        ├── index.html   # main browse page
        ├── me.html      # student's active watches
        └── partials/
            ├── courses.html       # HTMX fragment: course blocks + result count
            └── watch_button.html  # HTMX fragment: watch/unwatch toggle

alembic/versions/        # DB migrations
tests/                   # pytest (parser + diff)
docker-compose.yml       # postgres:16-alpine
app.py                   # dev server entrypoint
```

---

## Setup

### Prerequisites

- Python 3.12+
- Docker (for PostgreSQL)
- A Discord application ([discord.com/developers/applications](https://discord.com/developers/applications))

### 1. Clone and create virtualenv

```bash
git clone <repo-url>
cd CunyCourseScouter
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pip install psycopg2-binary itsdangerous
```

### 2. Configure environment

Create a `.env` file in the project root:

```env
# Database
DATABASE_URL=postgresql://scouter:scouter@localhost:5432/cuny_scouter
POSTGRES_PASSWORD=scouter

# Session security (change in production)
SECRET_KEY=some-random-secret-string

# Discord webhook — post notifications here
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Discord OAuth — from your Discord application
DISCORD_CLIENT_ID=your_client_id
DISCORD_CLIENT_SECRET=your_client_secret
DISCORD_REDIRECT_URI=http://localhost:8000/auth/discord/callback
```

In your Discord application, go to **OAuth2 → Redirects** and add `http://localhost:8000/auth/discord/callback`.

### 3. Start the database

```bash
docker compose up -d db
```

### 4. Run migrations

```bash
.venv/bin/alembic upgrade head
```

### 5. Start the web app

```bash
.venv/bin/python3 app.py
```

Open [http://localhost:8000](http://localhost:8000).

### 6. Run the background worker (separate terminal)

```bash
.venv/bin/python3 -m cuny_scouter.scheduler
```

The worker scrapes all 63 Baruch subjects and populates the database on its first run (~3–4 minutes). Subsequent polls run every 10 minutes by default (`POLL_INTERVAL_SECONDS` in `.env`).

---

## Running tests

```bash
.venv/bin/pytest
```

Tests cover the HTML parser (all 63+ section fields, status detection, structural validation) and the diff engine (open→closed, closed→open, waitlist transitions, new sections, dedup).

---

## Scraper notes

CUNY's course search uses a PeopleSoft wizard that requires a 3-step POST chain per subject:

1. **GET** — establishes a `JSESSIONID` session cookie
2. **POST** (institution + term) — selects Baruch, Fall 2026
3. **POST** (subject) — returns the section HTML with `open_class=""` to include waitlisted sections (omitting this hides them)

Section status is parsed from the `src` attribute of a status image — the `alt` attribute incorrectly says "Open" for all statuses. The HTML has unclosed `<tbody>/<tr>` tags, so sections are selected directly by `td[data-label='Class']` rather than traversing the table structure.

---

## Notification dedup

Three layers prevent students from being spammed:

1. **Unique DB index** — one `notifications` row per `(watch_id, from_status, to_status)` per calendar day
2. **60-minute cooldown** — skips sending if the same watch+status was notified within the last hour (guards against midnight resetting the daily index)
3. **Auto-deactivation** — after the first open alert, the watch is deactivated so the student only gets one ping per enrollment attempt. They can re-watch from the `/me` page.
