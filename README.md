# CUNY Course Scouter

A seat-tracker for Baruch College students. Search and browse Fall 2026 course sections, watch the ones you want, and get a Discord ping the moment a seat opens or changes status.

Live at: [http://cunycoursescouter.duckdns.org](http://cunycoursescouter.duckdns.org)

---

## How it works

```mermaid
flowchart TD
    A([Student visits site]) --> B[Browse & filter sections\ncourse name · instructor · code\nsubject · status · mode]
    B --> C{Logged in?}
    C -- No --> D[Login with Discord OAuth]
    D --> C
    C -- Yes --> E[Click Watch on a section]
    E --> F[(watches table\nactive = TRUE)]

    subgraph Worker ["Background Worker"]
        G([Fast poll every 2 min]) --> H{Active watches?}
        H -- Yes --> I[Fetch only watched subjects\nfrom CUNY Global Search]
        H -- No --> J([Sleep])
        I --> K[Upsert sections table\nAppend section_snapshots]
        K --> L[Diff against previous snapshots]
        L --> M{Status change?}
        M -- No --> J
        M -- Yes --> N[Find active watches\nfor changed sections]
        N --> O[Post Discord embed\n@ mention student]
        O --> P[Record in notifications\nauto-deactivate watch]
        P --> J

        Q([Full poll every 60 min]) --> R[Fetch all 63+ subjects]
        R --> K
    end

    F -.->|next fast poll picks up| N
    O -.->|DM or channel ping| S([Student gets notified])
    S --> T[Student claims seat\nin CUNYfirst]
```

---

## Features

- **Live search** — filter by course name, instructor, subject, or course number as you type (300 ms debounce, no page reload)
- **Composable filters** — department dropdown, open/waitlist/closed status, and instruction mode all work together
- **Two-tier polling** — fast poll every 2 minutes for watched subjects, full catalog refresh every 60 minutes
- **GRAD + UGRD support** — scrapes both undergraduate and graduate sections (BUSI, BUAD, CMIS, etc.)
- **Discord login** — one click via OAuth2, no password or email required
- **Watch / Unwatch** — HTMX toggle swaps just the button, no full reload
- **Notify on any change** — pings you on open, waitlist, and closed status changes
- **Smart dedup** — three layers prevent duplicate pings: unique DB index per transition, 60-minute cooldown, and auto-deactivation after first alert
- **Dark mode** — op.gg-style navy and blue theme, persisted in localStorage
- **2,700+ sections** — all Baruch subjects scraped each full poll cycle

---

## Tech stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI + Jinja2 |
| Frontend interactivity | HTMX (CDN, no build step) |
| Database | PostgreSQL 16 (Docker) |
| ORM / migrations | SQLAlchemy 2.x + Alembic |
| Auth | Discord OAuth2 + signed session cookie (`itsdangerous`) |
| Notifications | Discord webhook embeds |
| Scraping | `requests` + BeautifulSoup4 |
| Config | pydantic-settings (`.env` file) |
| Deployment | Docker Compose on AWS EC2 t3.micro |

---

## Project structure

```
cuny_scouter/
├── scraper/
│   ├── client.py        # HTTP session, 3-step POST chain to CUNY Global Search, UGRD+GRAD support
│   └── parser.py        # HTML → SectionRecord dataclasses, extracts subject from header
├── db/
│   ├── models.py        # SQLAlchemy ORM (sections, watches, students, notifications)
│   └── session.py       # engine + Session factory
├── diff.py              # compare snapshots → list[StatusEvent]
├── notifier.py          # Discord embed dispatch + 3-layer dedup
├── scheduler.py         # two-tier poll loop (fast + full)
├── config.py            # all env vars via pydantic-settings
└── web/
    ├── __init__.py      # FastAPI app factory + SessionMiddleware
    ├── auth.py          # Discord OAuth2 flow
    ├── routes.py        # route handlers + filter logic
    └── templates/
        ├── base.html              # CSS theme tokens, dark mode, nav
        ├── index.html             # main browse page
        ├── me.html                # student's active watches
        └── partials/
            ├── courses.html       # HTMX fragment: course blocks + result count
            └── watch_button.html  # HTMX fragment: watch/unwatch toggle

alembic/versions/        # DB migrations
tests/                   # pytest (parser + diff)
Dockerfile               # single image for web + worker
docker-compose.yml       # db + migrate + web + worker services
```

---

## Local development setup

### Prerequisites

- Python 3.12+
- Docker
- A Discord application ([discord.com/developers/applications](https://discord.com/developers/applications))

### 1. Clone and create virtualenv

```bash
git clone https://github.com/isop0d/CunyCourseScouter.git
cd CunyCourseScouter
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure environment

Create a `.env` file in the project root:

```env
DATABASE_URL=postgresql://scouter:scouter@localhost:5432/cuny_scouter
POSTGRES_PASSWORD=scouter
SECRET_KEY=some-random-secret-string
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
DISCORD_CLIENT_ID=your_client_id
DISCORD_CLIENT_SECRET=your_client_secret
DISCORD_REDIRECT_URI=http://localhost:8000/auth/discord/callback
FAST_POLL_INTERVAL_SECONDS=120
FULL_POLL_INTERVAL_SECONDS=3600
```

In your Discord application go to **OAuth2 → Redirects** and add `http://localhost:8000/auth/discord/callback`.

### 3. Start the database and run migrations

```bash
docker compose up -d db
alembic upgrade head
```

### 4. Start the web app

```bash
python -m uvicorn cuny_scouter.web:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

### 5. Run the background worker (separate terminal)

```bash
python -m cuny_scouter.scheduler
```

The worker runs a full poll on startup (~8-10 minutes to scrape all subjects), then switches to fast polls every 2 minutes for watched subjects and a full refresh every 60 minutes.

---

## Production deployment (AWS EC2)

The app runs fully in Docker Compose on a single EC2 t3.micro instance (AWS Free Tier).

### Services

| Service | Command |
|---|---|
| `db` | PostgreSQL 16 |
| `migrate` | `alembic upgrade head` (runs once at deploy, then exits) |
| `web` | `uvicorn cuny_scouter.web:app --host 0.0.0.0 --port 8000` |
| `worker` | `python -m cuny_scouter.scheduler` |

### Deploy steps

```bash
# On EC2 after git pull
docker compose run --rm migrate
docker compose up -d --build web worker
```

### Required `.env` on EC2

```env
POSTGRES_PASSWORD=strong-password
SECRET_KEY=generate-with-python3-secrets-token-hex-32
DISCORD_CLIENT_ID=your_client_id
DISCORD_CLIENT_SECRET=your_client_secret
DISCORD_REDIRECT_URI=http://cunycoursescouter.duckdns.org/auth/discord/callback
DISCORD_WEBHOOK_URL=your_webhook_url
FAST_POLL_INTERVAL_SECONDS=120
FULL_POLL_INTERVAL_SECONDS=3600
```

Nginx proxies port 80 → 8000 so the site is reachable without a port number.

---

## Running tests

```bash
pytest
```

---

## Scraper notes

CUNY's course search uses a PeopleSoft wizard requiring a 3-step POST chain per subject:

1. **GET** — establishes a `JSESSIONID` session cookie
2. **POST** (institution + term) — selects Baruch, Fall 2026
3. **POST** (subject) — returns section HTML with `open_class=""` to include waitlisted sections

Each subject is tried with `courseCareer=UGRD` first, then `GRAD` if no results are found. Subject codes are parsed from the HTML header (e.g. `BUAD` POST key → `BUS` display code) rather than the POST key.

---

## Notification dedup

Three layers prevent duplicate pings:

1. **Unique DB index** — one `notifications` row per `(watch_id, from_status, to_status)` transition
2. **60-minute cooldown** — skips if same watch+status was notified within the last hour
3. **Auto-deactivation** — watch is deactivated after first alert; student can re-watch from `/me`
