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
the server before the interface updates. Backlog email reminders are available as described below.

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
### Scheduling, subtasks, estimates and recurrence

Use **Edit list**, **Edit category**, or the task editor to set optional start times
and deadlines. Values are local wall times in the account's saved time zone; date
summaries use the selected display calendar. Date/time inputs use the browser's
native calendar. A start after its deadline, or dates outside the enclosing list
or category, is allowed with a live warning and visible conflict tags. Tags are
recomputed after edits and moves; clients cannot suppress them by sending tags.
Missing dates do not impose or inherit additional bounds.

Tasks support optional whole-minute estimates and up to 200 editable subtasks.
Subtask completion is independent of parent completion. Estimates must be positive
whole numbers. A recurring successor copies the description, category, estimate,
and subtask titles, and resets its subtask completion state.

Recurrence supports every 1–365 days, weeks or months. Completing a task creates
one successor, advancing its deadline (or start when no deadline exists) to the
next occurrence after completion. Missed occurrences are skipped. Early completion
still advances by at least one interval. When both dates exist, both advance by
the same number of intervals. Monthly repeats clamp to the target month's end
and preserve each date's original day, so January 31 → February 28 → March 31.
When neither date exists, the successor gets a start time one interval after
completion in the user's saved time zone.

The completion endpoint, `POST /api/backlogs/{list}/tasks/{task}/completion`, accepts
`{"done": true}` or `{"done": false}`. It locks the user's list and creates the next
occurrence in the same transaction. Repeated/concurrent completion requests and
reopening/recompleting an occurrence do not create additional successors. Deleting
a successor does not make its original occurrence generate another one. Custom
queues append the successor; default queues place it last in its category.

Change 3 verification: all 31 frontend checks and all 13 backlog backend checks
passed. Full backend suite: 31/32 passed; the previously recorded Auckland agenda
date assertion still fails in unchanged code. Live PostgreSQL/API checks passed
for date/tag persistence, estimates, subtasks, monthly anchors, concurrent
completion idempotence, time-zone-based recurrence, archive protection,
authentication, user isolation, and account deletion. Temporary test accounts were
removed. Browser checks covered the list/category/task forms, an unsaved conflict
preview, subtask controls and form overflow; no example tasks were saved to the
user's lists. The local API was rebuilt from the cached dependency image.
### Dashboard and detailed backlog tracking

The dashboard adds one compact backlog card with **View backlog details**. Among
unfinished tasks in active lists, it shows the task with the nearest future start
or deadline (excluding overdue tasks from this first selection). If none qualifies,
it shows the oldest overdue task, then falls back to the first unfinished task in
list/queue order. Completed tasks and archived lists are excluded. Ties follow
list and queue order. Only that one task is shown on the dashboard.

The linked **Backlog tracking** view is also available from the Backlog page.
Filter by any active list or show all lists. It displays open/overdue task counts,
upcoming deadline counts, total completion percentage, upcoming and overdue
deadlines, and per-list progress with expandable ordered queues. Progress is
completed tasks divided by all tasks, with empty lists shown as 0%. Deadline
sections include task, category and list deadlines. List/category deadlines are
omitted when their nonempty task scope is entirely complete; empty scopes retain
their deadlines. No dates are inherited from parents. **Open queue** opens the
selected list's saved queue for editing.

All comparisons use the account's saved time zone and minute precision; dates
use the selected display calendar. Tracking is recalculated when the view renders
from the same user-scoped records already loaded by the app. This change adds no
backend endpoints, database schema changes or extra data sharing.

Change 4 verification: all 36 frontend checks passed, covering nearest-event
selection, overdue/queue fallbacks, completed/archived exclusion, empty states,
counts and progress, deadline scopes, navigation, title escaping and saved-time-zone
behavior. Browser checks verified the compact dashboard card, details link,
list filter, queue navigation and rendered layout. No example tasks or lists were
saved. The frontend is served directly by the existing Docker bind mount; the
backend was unchanged, including the previously recorded agenda-date test issue.
### Customizable backlog email reminders

In **Settings > Backlog email reminders**, opt in and choose default reminder
minutes for starts and deadlines. Delivery is off by default. Initial choices are
at the start time, and one day / one hour / at the deadline. Enter comma-separated
whole minutes; 0 means at the event, and an empty field disables that event.
Offsets accept up to 12 values, from 0 to 525600 minutes. Optional overdue repeats
use 0 for off, or an interval of at least 15 minutes (up to 525600).

Each list/category/task editor has an **Email reminders** disclosure with three
modes: inherit parent settings, custom reminders, or off. Inheritance follows
account defaults → list → category → task. An explicit child custom policy can
override a parent's off setting, but the account-wide opt-in is always required.
Recurring successors preserve their task overrides. Missing dates generate no
reminders, and no date is inherited from a parent.

Messages go only to the signed-in user's server-verified Google account email,
through the existing shared SMTP relay. There is no arbitrary recipient field.
English/Persian messages include titles, list, scheduled time and time zone, with
escaped HTML and a plain-text alternative. The existing agenda email transport
still uses STARTTLS on ordinary ports and implicit TLS on port 465.

The existing scheduler checks reminders every 30 seconds after the agenda tick.
Archived lists and completed tasks are skipped; list/category reminders also stop
when all tasks in their nonempty scope are complete. A due reminder has a
three-hour catch-up window. Before-event reminders are not sent after the original
event time. Repeated overdue reminders use only the latest interval slot, so an
outage does not replay a backlog of emails. Time-zone conversion uses the saved
account zone; daylight-saving gaps shift forward, and repeated hours use their
first occurrence.

The additive `backlog_reminder_deliveries` table stores durable per-user event
claims and recent status. It is created by the existing startup table-creation
mechanism. Claims are committed before SMTP delivery; concurrent scheduler runs
cannot claim the same event twice. Known rejections retry no faster than once per
minute, up to five attempts while the event is still eligible. A disconnected or
interrupted send is **Delivery uncertain** and is not automatically resent;
stale in-flight claims are treated as uncertain after five minutes. SMTP cannot
prove end-to-end inbox delivery. This favors avoiding duplicate mail when the
connection outcome is ambiguous.

Settings shows the ten most recent reminder deliveries and a refresh control.
`GET /api/backlog-reminders/status` is authenticated and returns only the current
user's verified address, availability, defaults and delivery history. Account
deletion cascades to delivery history. Existing preference updates that omit
reminder settings preserve the saved reminder opt-in and policy.

Change 5 verification: all 41 frontend checks passed. Backend unit run: 43 passed,
six opt-in integration checks skipped, and the same previously recorded Auckland
agenda-date assertion failed in unchanged code. The six PostgreSQL/API integration
checks were then run explicitly and all passed, using temporary future-dated
accounts and an injected fake sender. They cover concurrent claims, retry limits
and recovery, uncertain/interrupted sends, repeated overdue slots, reminders at
all three levels, preference preservation, opt-out, completion/archive exclusion,
user isolation and account-deletion cleanup. SMTP rendering/transport tests cover
HTML escaping, multipart messages, TLS and uncertain connection outcomes. Browser
checks covered the default-off checkbox, custom/off item controls and layout.
No real reminder email was sent; inbox delivery was not tested. Existing SMTP
configuration is available. The user's reminder opt-in remains off.

To repeat the opt-in integration checks from PowerShell in the project root:

    docker compose run --rm --no-deps -e RUN_BACKLOG_REMINDER_INTEGRATION=1 --volume "${PWD}/backend:/srv:ro" api python -B -m unittest discover -s tests -p test_backlog_reminder_delivery.py -v

These checks create and remove temporary accounts in the configured database;
all mail is intercepted by a fake sender and their dates cannot trigger the live
scheduler. The API was rebuilt locally using its cached dependency image.

Backlog ordering correction: category Move up/down now selects each task by a fixed
id before mapping the reordered category back into the full list. It preserves
all task metadata and other categories; an existing custom queue remains independent.
The regression check covers the reported two-task case and interleaved categories.
