# My EveryDay Life

Bilingual (English / فارسی) life manager: tasks, routines, goals, activity logs, calendar
(Gregorian or Jalali), dashboard with drill-down, per-user Google Calendar, Telegram and AI-model
connections. FastAPI + PostgreSQL backend, static frontend, Caddy in front (automatic HTTPS).

    frontend/index.html   single-page app (also works standalone as a demo with mock data)
    backend/app/main.py   FastAPI API
    Caddyfile             serves the frontend, proxies /api and /auth, writes /config.js
    docker-compose.yml    db + api + web

## 1. Google Cloud setup (once)
1. Create a project, enable **Google Calendar API**.
2. OAuth consent screen: add scope `.../auth/calendar.readonly`. While the app is in *Testing* mode,
   only listed test users can sign in and Google expires calendar refresh tokens after 7 days.
   Publish and complete verification before real users.
3. Credentials -> OAuth client ID -> **Web application**:
   - Authorized JavaScript origin: `https://YOUR_DOMAIN`
   - Authorized redirect URI: `https://YOUR_DOMAIN/auth/google-calendar/callback`
   (For a local test use `http://localhost` and `http://localhost/auth/google-calendar/callback`.)

## 2. Configure and start
    cp .env.example .env
    # generate the two secrets and paste them into .env
    openssl rand -hex 32                                                        # JWT_SECRET
    docker run --rm python:3.12-slim sh -c "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"   # SECRETS_KEY
    # edit DOMAIN, PUBLIC_URL, POSTGRES_PASSWORD, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    docker compose up -d --build
    docker compose logs -f api

Open `PUBLIC_URL`, sign in with Google. Local test: `DOMAIN=http://localhost`, `PUBLIC_URL=http://localhost`.

**Back up SECRETS_KEY**: without it the stored bot tokens, API keys and Google refresh tokens cannot be decrypted.

## 3. Operations
    docker compose exec db pg_dump -U mel mel > backup.sql            # backup
    docker compose exec -T db psql -U mel mel < backup.sql            # restore into an empty db
    docker compose pull && docker compose up -d --build               # update
Run the checks in `backend/tests_isolation.md` after every deployment.

## 4. Features that need setup after the stack is up
**AI recommendations** (Dashboard): Settings > Integrations > AI model. Enter an https base URL (default
`https://api.arvancloudai.ir/v1`), the model name, the API key, and tick the data-sharing box. Press **Test**.
The server calls `POST {base}/chat/completions` (OpenAI-compatible). Auth header is tried as `apikey KEY` and
`Bearer KEY` ("Auto"). Only summary numbers are sent (counts, minutes, streaks; no titles or notes), and the
dashboard shows exactly what ("What data is sent?"). The base URL must be https, port 443 and resolve to a public
address; set `AI_ALLOWED_HOSTS` in `.env` to allow only specific hosts.

**Daily agenda schedule**: Settings > Integrations > Telegram Bot API: paste the token from @BotFather, press **Link chat**, send
`/start CODE` to your bot. In Settings, opt in to **Email delivery** to also send the agenda to your verified Google account email. Email uses the server's shared SMTP relay; set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_FROM` in `.env` and restart the stack. Port 587 uses STARTTLS; port 465 uses implicit TLS. Leave SMTP unset to keep email disabled. The scheduler (inside the API container, checks every 30 s) sends each selected channel at the user-configured **Agenda time** in Settings and saved time zone, once per channel and local date (retried up to 5 times, and only within the three-hour catch-up window so a stale agenda is never sent late). **Send now** and **Send email now** test delivery immediately; **Test email** sends a clearly marked sample to the signed-in email address using the same HTML template and plain-text fallback. Settings lists recent deliveries. Run exactly one API container.

**Streak tracker**: Dashboard heatmap of completed activities per day for the last year; click a day for its logs.

## 5. Tests
    cd backend && python3 -m unittest discover -s tests -v     # agenda timing (DST, time zones), SSRF guard, AI helpers
    cd frontend && node tests/ui.test.js                      # forms, quick add, streaks, AI/Telegram flows (fake API)

## 6. Status (read before relying on it)
Implemented: Google sign-in (server-verified token), per-user records with server-side ownership,
encrypted write-only secrets, per-user read-only Google Calendar (OAuth, refresh, revoke on disconnect),
AI recommendations, Telegram linking and opt-in email/Telegram agenda with a configurable delivery time, streak heatmap, one-tap logging and quick add,
Jalali/Gregorian calendar, English/Persian RTL, Light / Dark / System theme (Pastel Pink palette).

Verified by the author: the unit tests above (they cover the helper logic and the page logic with a fake API).
NOT run by the author (no Docker, Postgres, internet or real accounts were available): the Docker stack, the API
endpoints, Google OAuth, real Telegram delivery, and a real call to ArvanCloud. The ArvanCloud endpoint path and
header format are unconfirmed (hence "Auto"); the 07:00 delivery delay has not been measured. Expect first-run fixes.

Not built: Telegram commands (/today, /tasks) and webhook, Google incremental sync and writing tasks to Google,
reminders before activities, data export, rate limiting beyond simple cooldowns, migrations (tables are created at
startup; add Alembic before production), JSON "records" table (move to explicit columns).
The session token is kept in the browser's localStorage; serve the site over HTTPS only.
Agenda text shows dates in Gregorian form.

## Backlog lists

Open **Backlog** in the sidebar. Each user starts with four editable lists (first,
second and last priority projects, and free-time work). Add more lists with **New
list**, rename them, or archive and restore them using the Active/Archived views.
Deleting a list asks for confirmation and is permanent. Deleting every list does
not recreate the defaults. Default titles use the user's language at initialization;
custom titles are preserved when changing the interface language.

Lists are stored as user-scoped `backlogs` records; a private `backlog_meta` record
marks initialization. No schema migration is needed. Writes are acknowledged by
the server before the interface updates. Scheduling, dashboard tracking and backlog email reminders are subsequent feature changes.

Backlog verification is included in the existing frontend suite and
`backend/tests/test_backlogs.py` (isolated SQLite storage using the actual API
handler functions; FastAPI and SQLAlchemy required). Live PostgreSQL concurrency
and real authenticated browser sessions require a separate integration check.

### Categories, tasks and queues

Choose **Open list** to manage a list. Create and rename categories, reorder them
by dragging their headers or using Move up/down, and create tasks with descriptions.
Deleting a category keeps its tasks under **Uncategorized**. Any task can be
completed or reopened independently of its queue position.

The **Queue** view defaults to category order, with uncategorized tasks last.
Drag queue rows or use Move up/down to save a custom sequence that can interleave
categories arbitrarily. Category changes keep that custom queue intact. **Use
category order** resets it after confirmation. Completed tasks remain in the queue;
**Next in queue** identifies the first unfinished task.

Drag tasks onto a category to recategorize them, onto another task to place them
before it, or onto a list card to move them to that list as uncategorized. Task
editing also offers **Move to list** and category selectors for keyboard/touch use.
Cross-list moves save both lists through `POST /api/backlogs/batch` in one database
transaction. The endpoint validates all documents before saving and only accesses
the authenticated user's lists; archived contents must be restored before editing.
Categories, tasks and explicit queue order are nested in each backlog record,
so existing lists gain empty categories/tasks without a database schema migration.
List renames and archive/restore preserve their contents. Concurrent full-document
edits in different browser tabs use last-save-wins semantics.

Change 2 verification: all 26 frontend checks and all six backlog backend checks
passed. Full backend suite: 24/25 passed; the same pre-existing Auckland agenda-date
assertion still fails in unchanged scheduler code. Live PostgreSQL/API checks
passed for default/custom queues, atomic task transfers, persistence after reload,
completion, validation, archive protection, cross-user isolation, and account
deletion; temporary verification accounts were removed. The browser list-detail
view and task form were inspected without adding data to the user's lists. Native
drag gestures were not exercised in the browser; their operations are covered by
the frontend checks. The API was rebuilt locally from its cached dependency image.