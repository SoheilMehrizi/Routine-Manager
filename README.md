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

**07:00 daily agenda**: Settings > Telegram Bot API: paste the token from @BotFather, press **Link chat**, send
`/start CODE` to your bot. The scheduler (inside the API container, checks every 30 s) sends the agenda to each
linked user at 07:00 in the time zone saved in their settings, once per user and local date (retried up to 5 times,
and only until 10:00 local time so a stale agenda is never sent late). **Send now** tests delivery immediately;
Settings lists the last deliveries with any error. Run exactly one API container.

**Streak tracker**: Dashboard heatmap of completed activities per day for the last year; click a day for its logs.

## 5. Tests
    cd backend && python3 -m unittest discover -s tests -v     # agenda timing (DST, time zones), SSRF guard, AI helpers
    cd frontend && node tests/ui.test.js                      # forms, quick add, streaks, AI/Telegram flows (fake API)

## 6. Status (read before relying on it)
Implemented: Google sign-in (server-verified token), per-user records with server-side ownership,
encrypted write-only secrets, per-user read-only Google Calendar (OAuth, refresh, revoke on disconnect),
AI recommendations, Telegram linking and 07:00 agenda, streak heatmap, one-tap logging and quick add,
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
