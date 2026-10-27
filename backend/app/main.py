"""My EveryDay Life API: Google sign-in, per-user records, encrypted integrations,
AI recommendations, Telegram linking and the 07:00 daily agenda scheduler.

First cut: one `records` table (kind + JSONB data) keeps the API small; promote
tasks/routines/goals/logs to explicit columns and add Alembic before production.
Every query filters by the authenticated user's id; client-supplied owner ids are ignored.
Run a single API instance: the scheduler lives in-process (a unique DB row per user/day
prevents double sends even so).
"""
import datetime as dt
import logging
import math
import os
import re
import secrets
import threading
import time
import urllib.parse
import uuid
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

import jwt
import requests
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from google.auth.transport import requests as grequests
from google.oauth2 import id_token
from pydantic import BaseModel
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .logic import (auth_header, build_agenda, build_ai_messages, is_due, safe_public_https_url, schemes_to_try,
                    strip_think, user_zone, validate_stats, valid_agenda_time)
from .email_service import MailConfigError, MailDeliveryError, email_configured, send_agenda_email, smtp_config_status

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mel")  # never log tokens, keys or message bodies

DATABASE_URL = os.environ["DATABASE_URL"]
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
JWT_SECRET = os.environ["JWT_SECRET"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_REDIRECT_URI = os.environ["GOOGLE_REDIRECT_URI"]  # e.g. https://life.example.com/auth/google-calendar/callback
FRONTEND_URL = os.environ["FRONTEND_URL"]
AI_ALLOWED_HOSTS = [h for h in os.environ.get("AI_ALLOWED_HOSTS", "").split(",") if h.strip()]
vault = Fernet(os.environ["SECRETS_KEY"])  # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
KINDS = {"tasks", "routines", "goals", "logs", "cats"}
INTEGRATIONS = {"telegram", "llm"}

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(200), default="")
    prefs: Mapped[dict] = mapped_column(JSONB, default=dict)  # lang, cal, tz, theme
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Record(Base):
    __tablename__ = "records"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    data: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (Index("ix_records_user_kind", "user_id", "kind"),)


class Integration(Base):
    """Per-user connection. Secrets are encrypted at rest and are never returned by any endpoint."""
    __tablename__ = "integrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(16))  # telegram | llm | gcal
    config: Mapped[dict] = mapped_column(JSONB, default=dict)  # non-secret: URL, model, bot name, chat id, consent...
    secret_enc: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("user_id", "name"),)


class Delivery(Base):
    """One row per user, kind and local date: the idempotency key for the daily agenda."""
    __tablename__ = "deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16), default="agenda")
    local_date: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | sent | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("user_id", "kind", "local_date"),)


Base.metadata.create_all(engine)  # dev only; use Alembic migrations in production


# ------------------------------------------------------------------ helpers
def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def db():
    with SessionLocal() as s:
        yield s


COOLDOWN: dict = {}


def cooldown(key, seconds):
    t = time.monotonic()
    if t - COOLDOWN.get(key, -1e9) < seconds:
        raise HTTPException(429, "Please wait a few seconds before trying again")
    COOLDOWN[key] = t


def get_integ(s: Session, u_id: int, name: str):
    return s.scalar(select(Integration).where(Integration.user_id == u_id, Integration.name == name))


def secret_of(rec: Integration) -> str:
    return vault.decrypt(rec.secret_enc.encode()).decode()


def tg_call(token, method, **params):
    """Returns (result, error). The URL contains the token, so exceptions are never logged verbatim."""
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=15)
        data = r.json()
    except (requests.RequestException, ValueError):
        return None, "network error"
    if not data.get("ok"):
        return None, str(data.get("description", "error"))[:120]
    return data["result"], ""


def normalize_record_data(kind: str, data: dict) -> dict:
    """Keep focus sessions as first-class activity log entries in JSONB records."""
    clean = {k: v for k, v in data.items() if k != "id"}
    if kind == "logs" and (clean.get("focus_session") or clean.get("activity_type") == "focus"):
        clean["focus_session"] = True
        clean["activity_type"] = "focus"
        if clean.get("result") not in ("Completed", "Partial"):
            raise HTTPException(422, "Focus activity result must be Completed or Partial")
        duration = clean.get("dur")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            raise HTTPException(422, "Focus activity duration must be a positive number of minutes")
        clean["dur"] = int(round(duration))
        clean.setdefault("title", "Focus session")
    elif kind == "routines" and clean.get("rec") == "custom":
        days = clean.get("days")
        if not isinstance(days, list) or not days or any(isinstance(day, bool) or not isinstance(day, int) or day < 0 or day > 6 for day in days):
            raise HTTPException(422, "Custom routines need one or more weekdays from 0 to 6")
        clean["days"] = sorted(set(days))
    return clean

def load_data(s: Session, uid: int) -> dict:
    out = {k: [] for k in ("tasks", "routines", "goals", "logs")}
    for r in s.scalars(select(Record).where(Record.user_id == uid, Record.kind.in_(list(out)))):
        out[r.kind].append({**normalize_record_data(r.kind, r.data), "id": r.id})
    return out


def agenda_for(s: Session, user: User, local_date: str) -> str:
    prefs = user.prefs or {}
    return build_agenda(load_data(s, user.id), local_date, prefs.get("lang", "en"), prefs.get("tz") or "UTC",
                        os.environ.get("PUBLIC_URL", ""))


def email_agenda(user: User, local_date: str, body: str, test: bool = False) -> str:
    try:
        send_agenda_email(user.email, local_date, body, (user.prefs or {}).get("lang", "en"), test, schedule=(user.prefs or {}).get("agenda_time", "07:00"))
    except (MailConfigError, MailDeliveryError) as exc:
        return str(exc)
    return ""

# ------------------------------------------------------------------ 07:00 agenda scheduler
def run_due_agendas():
    now = now_utc()
    with SessionLocal() as s:
        for user in s.scalars(select(User)).all():
            prefs = user.prefs or {}
            tg = get_integ(s, user.id, "telegram")
            channels = []
            if tg and tg.config.get("chat_id"):
                channels.append(("agenda", "telegram", tg))
            if prefs.get("agenda_email") and email_configured():
                channels.append(("agenda_email", "email", None))
            if not channels:
                continue
            due, local_date = is_due(now, prefs.get("tz"), prefs.get("agenda_time", "07:00"))
            if not due:
                continue
            for kind, channel, rec in channels:
                d = s.scalar(select(Delivery).where(Delivery.user_id == user.id, Delivery.kind == kind, Delivery.local_date == local_date))
                if d and (d.status == "sent" or d.attempts >= 5):
                    continue
                if d and (now - d.updated_at).total_seconds() < 60:
                    continue
                if not d:
                    d = Delivery(user_id=user.id, kind=kind, local_date=local_date)
                    s.add(d)
                    try:
                        s.commit()
                    except IntegrityError:
                        s.rollback(); continue
                body = agenda_for(s, user, local_date)
                if channel == "email":
                    err = email_agenda(user, local_date, body); sent = not err
                else:
                    res, err = tg_call(secret_of(rec), "sendMessage", chat_id=rec.config["chat_id"], text=body,
                                       disable_web_page_preview=True)
                    sent = bool(res)
                d.attempts += 1
                d.status, d.last_error = ("sent", "") if sent else ("failed", err)
                s.commit()
                log.info("agenda channel=%s user=%s date=%s status=%s attempts=%s", channel, user.id, local_date, d.status, d.attempts)


def scheduler_loop(stop: threading.Event):
    while not stop.wait(30):  # tick twice a minute: worst-case start delay is about 30 s plus Telegram latency
        try:
            run_due_agendas()
        except Exception:  # keep the loop alive whatever happens
            log.exception("agenda tick failed")


@asynccontextmanager
async def lifespan(_app):
    stop = threading.Event()
    if os.environ.get("SCHEDULER", "1") == "1":
        threading.Thread(target=scheduler_loop, args=(stop,), daemon=True, name="agenda").start()
    yield
    stop.set()


app = FastAPI(title="My EveryDay Life", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.environ.get("CORS_ORIGINS", "").split(",") if o],
    allow_headers=["Authorization", "Content-Type"],
    allow_methods=["*"],
)


def current_user(authorization: str = Header(""), s: Session = Depends(db)) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing token")
    try:
        sub = jwt.decode(authorization[7:], JWT_SECRET, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid token")
    user = s.get(User, int(sub))
    if not user:
        raise HTTPException(401, "Unknown user")
    return user


# ------------------------------------------------------------------ auth + profile
class GoogleLogin(BaseModel):
    credential: str


@app.post("/auth/google")
def google_login(body: GoogleLogin, s: Session = Depends(db)):
    try:
        info = id_token.verify_oauth2_token(body.credential, grequests.Request(), GOOGLE_CLIENT_ID)
    except ValueError:
        raise HTTPException(401, "Invalid Google credential")
    if not info.get("email_verified"):
        raise HTTPException(403, "Email not verified")
    user = s.scalar(select(User).where(User.google_sub == info["sub"]))
    if not user:
        user = User(google_sub=info["sub"], email=info["email"], name=info.get("name", ""))
        s.add(user)
        s.commit()
    exp = now_utc() + dt.timedelta(days=7)
    return {"token": jwt.encode({"sub": str(user.id), "exp": exp}, JWT_SECRET, algorithm="HS256")}


def kind_or_404(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(404, "Unknown collection")
    return kind


@app.get("/api/me")
def me(u: User = Depends(current_user)):
    return {"email": u.email, "name": u.name, "prefs": u.prefs}


@app.put("/api/me/prefs")
def set_prefs(prefs: dict, u: User = Depends(current_user), s: Session = Depends(db)):
    clean = {k: prefs[k] for k in ("lang", "cal", "tz", "theme", "agenda_email", "agenda_time") if k in prefs}
    if "agenda_email" in clean:
        clean["agenda_email"] = bool(clean["agenda_email"])
    if "tz" in clean:
        try:
            ZoneInfo(str(clean["tz"]))
        except Exception:
            raise HTTPException(422, "Unknown time zone")
    if "agenda_time" in clean and not valid_agenda_time(clean["agenda_time"]):
        raise HTTPException(422, "Agenda time must use 24-hour HH:MM format")
    u.prefs = clean
    s.add(u)
    s.commit()
    return u.prefs


@app.delete("/api/me", status_code=204)
def delete_account(u: User = Depends(current_user), s: Session = Depends(db)):
    s.execute(delete(User).where(User.id == u.id))  # records, integrations, deliveries go with it (ON DELETE CASCADE)
    s.commit()


# ------------------------------------------------------------------ integrations (write-only secrets)
class IntegrationIn(BaseModel):
    config: dict = {}
    secret: str | None = None  # write-only; omit to keep the stored one


def public_config(rec: Integration) -> dict:
    c = rec.config or {}
    if rec.name == "telegram":
        return {"bot": c.get("bot", ""), "linked": bool(c.get("chat_id"))}
    return c


@app.get("/api/integrations")
def list_integrations(u: User = Depends(current_user), s: Session = Depends(db)):
    rows = s.scalars(select(Integration).where(Integration.user_id == u.id))
    return [{"name": r.name, "config": public_config(r), "has_secret": bool(r.secret_enc)} for r in rows]


@app.put("/api/integrations/{name}")
def put_integration(name: str, body: IntegrationIn, u: User = Depends(current_user), s: Session = Depends(db)):
    if name not in INTEGRATIONS:
        raise HTTPException(404, "Unknown integration")
    rec = get_integ(s, u.id, name)
    if not rec and not body.secret:
        raise HTTPException(422, "A secret is required")
    cfg = None  # None = keep the stored config
    if name == "llm":
        url, model = str(body.config.get("url", "")), str(body.config.get("model", "")).strip()
        try:
            url = safe_public_https_url(url, AI_ALLOWED_HOSTS)
        except ValueError as e:
            raise HTTPException(422, str(e))
        if not model:
            raise HTTPException(422, "Model name is required")
        scheme = body.config.get("scheme", "auto")
        cfg = {"url": url, "model": model, "consent": bool(body.config.get("consent")),
               "scheme": scheme if scheme in ("auto", "Bearer", "apikey") else "auto"}
    elif body.secret:
        if not re.fullmatch(r"\d{6,}:[\w-]{30,}", body.secret):
            raise HTTPException(422, "Invalid Telegram bot token format")
        me_info, err = tg_call(body.secret, "getMe")
        if me_info is None:
            raise HTTPException(422, "Telegram rejected this token: " + err)
        cfg = {"bot": me_info.get("username", "")}  # a new token starts unlinked
    if not rec:
        rec = Integration(user_id=u.id, name=name)
        s.add(rec)
    if cfg is not None:
        rec.config = cfg
    if body.secret:
        rec.secret_enc = vault.encrypt(body.secret.encode()).decode()
    s.commit()
    return {"name": name, "config": public_config(rec), "has_secret": True}


@app.delete("/api/integrations/{name}", status_code=204)
def delete_integration(name: str, u: User = Depends(current_user), s: Session = Depends(db)):
    rec = get_integ(s, u.id, name)
    if rec and name == "gcal" and rec.secret_enc:  # revoke at Google before forgetting the token
        requests.post("https://oauth2.googleapis.com/revoke", data={"token": secret_of(rec)}, timeout=10)
    if rec:
        s.delete(rec)
        s.commit()


# ------------------------------------------------------------------ Google Calendar (per-user OAuth, read-only)
GCAL_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"


@app.get("/api/gcal/connect")
def gcal_connect(u: User = Depends(current_user)):
    state = jwt.encode({"sub": str(u.id), "exp": now_utc() + dt.timedelta(minutes=10)}, JWT_SECRET, algorithm="HS256")
    q = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID, "redirect_uri": GOOGLE_REDIRECT_URI, "response_type": "code",
        "scope": GCAL_SCOPE, "access_type": "offline", "prompt": "consent",
        "login_hint": u.email, "state": state,  # pre-selects the signed-in Google account
    })
    return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + q}


@app.get("/auth/google-calendar/callback")
def gcal_callback(code: str = "", state: str = "", error: str = "", s: Session = Depends(db)):
    if error or not code:
        return RedirectResponse(f"{FRONTEND_URL}/?gcal=denied")
    try:
        uid = int(jwt.decode(state, JWT_SECRET, algorithms=["HS256"])["sub"])
    except jwt.PyJWTError:
        raise HTTPException(400, "Invalid state")
    r = requests.post("https://oauth2.googleapis.com/token", timeout=10, data={
        "code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI, "grant_type": "authorization_code"})
    refresh = r.json().get("refresh_token") if r.ok else None
    if not refresh:
        return RedirectResponse(f"{FRONTEND_URL}/?gcal=failed")
    user = s.get(User, uid)
    rec = get_integ(s, uid, "gcal") or Integration(user_id=uid, name="gcal")
    rec.config, rec.secret_enc = {"email": user.email}, vault.encrypt(refresh.encode()).decode()
    s.add(rec)
    s.commit()
    return RedirectResponse(f"{FRONTEND_URL}/?gcal=connected")


@app.get("/api/gcal/events")
def gcal_events(start: str, end: str, u: User = Depends(current_user), s: Session = Depends(db)):
    rec = get_integ(s, u.id, "gcal")
    if not rec:
        raise HTTPException(404, "Google Calendar not connected")
    tok = requests.post("https://oauth2.googleapis.com/token", timeout=10, data={
        "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "grant_type": "refresh_token",
        "refresh_token": secret_of(rec)})
    if not tok.ok:  # revoked or expired: drop the token and ask the user to reconnect
        s.delete(rec)
        s.commit()
        raise HTTPException(409, "Google access was revoked; reconnect Google Calendar")
    ev = requests.get("https://www.googleapis.com/calendar/v3/calendars/primary/events", timeout=15,
                      headers={"Authorization": "Bearer " + tok.json()["access_token"]},
                      params={"timeMin": start + "T00:00:00Z", "timeMax": end + "T00:00:00Z", "singleEvents": "true",
                              "orderBy": "startTime", "maxResults": 500})
    if not ev.ok:
        raise HTTPException(502, "Google Calendar request failed")
    out = []
    for e in ev.json().get("items", []):
        st = e.get("start", {})
        out.append({"id": e["id"], "title": e.get("summary", "(no title)"), "start": st.get("dateTime") or st.get("date"),
                    "end": (e.get("end") or {}).get("dateTime") or (e.get("end") or {}).get("date"), "allDay": "date" in st})
    return out


# ------------------------------------------------------------------ Telegram linking + daily agenda
@app.post("/api/telegram/link")
def tg_link(u: User = Depends(current_user), s: Session = Depends(db)):
    rec = get_integ(s, u.id, "telegram")
    if not rec:
        raise HTTPException(404, "Save your bot token first")
    code = secrets.token_hex(4).upper()  # single-use, valid 10 minutes
    rec.config = {**rec.config, "link": {"code": code, "exp": (now_utc() + dt.timedelta(minutes=10)).isoformat()}}
    s.commit()
    return {"code": code, "bot": rec.config.get("bot", ""), "expires_in": 600}


@app.post("/api/telegram/link/check")
def tg_link_check(u: User = Depends(current_user), s: Session = Depends(db)):
    """Polls the user's own bot once (getUpdates) for `/start CODE`; no public webhook needed."""
    rec = get_integ(s, u.id, "telegram")
    if not rec:
        raise HTTPException(404, "Telegram is not connected")
    cfg = dict(rec.config)
    link = cfg.get("link")
    if not link:
        return {"linked": bool(cfg.get("chat_id"))}
    if now_utc() > dt.datetime.fromisoformat(link["exp"]):
        cfg.pop("link")
        rec.config = cfg
        s.commit()
        return {"linked": False, "expired": True}
    token = secret_of(rec)
    offset = int(cfg.get("offset", 0))
    updates, err = tg_call(token, "getUpdates", offset=offset, timeout=0, allowed_updates=["message"])
    if updates is None:
        raise HTTPException(502, "Telegram: " + err)
    linked = False
    for up in updates:
        offset = max(offset, up["update_id"] + 1)  # always advance so an update is never processed twice
        msg = up.get("message") or {}
        chat = msg.get("chat") or {}
        parts = (msg.get("text") or "").strip().split()
        if chat.get("type") == "private" and len(parts) == 2 and parts[0] == "/start" and parts[1].upper() == link["code"]:
            cfg["chat_id"], linked = chat["id"], True
    cfg["offset"] = offset
    if linked:
        cfg.pop("link")
    rec.config = cfg
    s.commit()
    if linked:
        tg_call(token, "sendMessage", chat_id=cfg["chat_id"], text="✅ Linked! Your agenda will arrive here every day at " + str((u.prefs or {}).get("agenda_time") or "07:00") + ".")
    return {"linked": linked}


@app.get("/api/agenda/status")
def agenda_status(u: User = Depends(current_user), s: Session = Depends(db)):
    rec = get_integ(s, u.id, "telegram")
    rows = s.scalars(select(Delivery).where(Delivery.user_id == u.id).order_by(Delivery.local_date.desc()).limit(5))
    return {"linked": bool(rec and rec.config.get("chat_id")), "tz": (u.prefs or {}).get("tz") or "UTC", "time": (u.prefs or {}).get("agenda_time") or "07:00",
            "email": u.email, "email_enabled": bool((u.prefs or {}).get("agenda_email")), "email_configured": email_configured(),
            "email_setup_error": smtp_config_status()[1],
            "deliveries": [{"date": d.local_date, "kind": d.kind, "status": d.status, "attempts": d.attempts, "error": d.last_error} for d in rows]}


@app.post("/api/agenda/send-now")
def agenda_send_now(u: User = Depends(current_user), s: Session = Depends(db)):
    cooldown(("agenda", u.id), 30)
    rec = get_integ(s, u.id, "telegram")
    if not rec or not rec.config.get("chat_id"):
        raise HTTPException(409, "Link Telegram first")
    local_date = now_utc().astimezone(user_zone((u.prefs or {}).get("tz"))).date().isoformat()
    res, err = tg_call(secret_of(rec), "sendMessage", chat_id=rec.config["chat_id"], text=agenda_for(s, u, local_date),
                       disable_web_page_preview=True)
    if res is None:
        raise HTTPException(502, "Telegram: " + err)
    return {"sent": True}


@app.post("/api/agenda/test-email")
def agenda_test_email(u: User = Depends(current_user), s: Session = Depends(db)):
    cooldown(("agenda-email-test", u.id), 30)
    local_date = now_utc().astimezone(user_zone((u.prefs or {}).get("tz"))).date().isoformat()
    try:
        send_agenda_email(u.email, local_date, agenda_for(s, u, local_date), (u.prefs or {}).get("lang", "en"), test=True, schedule=(u.prefs or {}).get("agenda_time", "07:00"))
    except MailConfigError as exc:
        raise HTTPException(503, str(exc))
    except MailDeliveryError as exc:
        raise HTTPException(502, str(exc))
    return {"sent": True, "to": u.email}


@app.post("/api/agenda/send-now/email")
def agenda_send_now_email(u: User = Depends(current_user), s: Session = Depends(db)):
    cooldown(("agenda-email", u.id), 30)
    if not (u.prefs or {}).get("agenda_email"):
        raise HTTPException(409, "Enable email delivery first")
    local_date = now_utc().astimezone(user_zone((u.prefs or {}).get("tz"))).date().isoformat()
    try:
        send_agenda_email(u.email, local_date, agenda_for(s, u, local_date), (u.prefs or {}).get("lang", "en"), schedule=(u.prefs or {}).get("agenda_time", "07:00"))
    except MailConfigError as exc:
        raise HTTPException(503, str(exc))
    except MailDeliveryError as exc:
        raise HTTPException(502, str(exc))
    return {"sent": True}


# ------------------------------------------------------------------ AI (OpenAI-compatible /chat/completions)
def llm_chat(s: Session, rec: Integration, messages, max_tokens):
    cfg = rec.config
    try:
        url = safe_public_https_url(cfg.get("url", ""), AI_ALLOWED_HOSTS)
    except ValueError as e:
        raise HTTPException(422, str(e))
    key, last = secret_of(rec), "The provider rejected the API key"
    for scheme in schemes_to_try(cfg.get("scheme", "auto"), url):
        try:
            r = requests.post(url + "/chat/completions", timeout=(5, 45), allow_redirects=False,
                              headers={**auth_header(scheme, key), "Content-Type": "application/json"},
                              json={"model": cfg["model"], "messages": messages, "max_tokens": max_tokens,
                                    "temperature": 0.4, "stream": False})
        except requests.RequestException:
            raise HTTPException(502, "Could not reach the AI provider (network error or timeout)")
        if r.status_code in (401, 403):
            continue  # maybe the other header scheme
        if r.status_code == 404:
            raise HTTPException(502, "Endpoint or model not found: check the base URL and model name")
        if r.status_code == 429:
            raise HTTPException(502, "The AI provider is rate limiting requests; try again later")
        if not r.ok:
            raise HTTPException(502, f"The AI provider returned HTTP {r.status_code}")
        try:
            text = r.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise HTTPException(502, "Unexpected response: is this an OpenAI-compatible /chat/completions endpoint?")
        if cfg.get("scheme", "auto") == "auto":  # remember what worked
            rec.config = {**cfg, "scheme": scheme}
            s.commit()
        return strip_think(text or "")
    raise HTTPException(502, last)


class AiIn(BaseModel):
    stats: dict
    lang: str = "en"


@app.post("/api/ai/test")
def ai_test(u: User = Depends(current_user), s: Session = Depends(db)):
    rec = get_integ(s, u.id, "llm")
    if not rec:
        raise HTTPException(404, "Connect an AI model in Settings first")
    cooldown(("ai-test", u.id), 5)
    text = llm_chat(s, rec, [{"role": "user", "content": "Reply with the single word: OK"}], 16)
    return {"ok": True, "model": rec.config.get("model"), "reply": text[:40] or "(empty)"}


@app.post("/api/ai/recommendations")
def ai_recommendations(body: AiIn, u: User = Depends(current_user), s: Session = Depends(db)):
    rec = get_integ(s, u.id, "llm")
    if not rec:
        raise HTTPException(404, "Connect an AI model in Settings first")
    if not rec.config.get("consent"):
        raise HTTPException(403, "Allow data sharing for the AI model first")
    if not validate_stats(body.stats):
        raise HTTPException(422, "Invalid statistics payload")
    cooldown(("ai", u.id), 15)
    text = llm_chat(s, rec, build_ai_messages(body.stats, body.lang), 700)
    if not text:
        raise HTTPException(502, "The model returned an empty answer")
    return {"text": text, "model": rec.config.get("model")}


# ------------------------------------------------------------------ generic per-user records (keep last: catches /api/{kind})
@app.get("/api/{kind}")
def list_records(kind: str, u: User = Depends(current_user), s: Session = Depends(db)):
    rows = s.scalars(select(Record).where(Record.user_id == u.id, Record.kind == kind_or_404(kind)))
    return [{**normalize_record_data(kind, r.data), "id": r.id} for r in rows]


@app.post("/api/{kind}", status_code=201)
def create_record(kind: str, data: dict, u: User = Depends(current_user), s: Session = Depends(db)):
    rid = str(uuid.uuid4())
    record_kind = kind_or_404(kind)
    clean = normalize_record_data(record_kind, data)
    s.add(Record(id=rid, user_id=u.id, kind=record_kind, data=clean))
    s.commit()
    return {**clean, "id": rid}


ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,40}$")


@app.put("/api/{kind}/{rid}")
def upsert_record(kind: str, rid: str, data: dict, u: User = Depends(current_user), s: Session = Depends(db)):
    """Create-or-update by client-generated id; ids are scoped to the authenticated user."""
    kind_or_404(kind)
    if not ID_RE.match(rid):
        raise HTTPException(422, "Invalid id")
    clean = normalize_record_data(kind, data)
    rec = s.get(Record, (rid, u.id))
    if rec and rec.kind != kind:
        raise HTTPException(409, "Id already used by another collection")
    if rec:
        rec.data = clean
    else:
        s.add(Record(id=rid, user_id=u.id, kind=kind, data=clean))
    s.commit()
    return {**clean, "id": rid}


@app.delete("/api/{kind}/{rid}", status_code=204)
def delete_record(kind: str, rid: str, u: User = Depends(current_user), s: Session = Depends(db)):
    rec = s.scalar(select(Record).where(Record.id == rid, Record.user_id == u.id, Record.kind == kind_or_404(kind)))
    if not rec:
        raise HTTPException(404, "Not found")  # same answer for "missing" and "someone else's"
    s.delete(rec)
    s.commit()
