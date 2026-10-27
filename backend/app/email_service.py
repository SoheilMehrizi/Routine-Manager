"""SMTP delivery for daily agendas, with a plain-text and email-safe HTML version."""
import html
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr


class MailConfigError(ValueError):
    """The server has missing or invalid mail settings."""


class MailDeliveryError(RuntimeError):
    """The configured SMTP relay rejected or could not deliver a message."""


def smtp_config_status(env=os.environ):
    host = (env.get("SMTP_HOST") or "").strip()
    sender = (env.get("SMTP_FROM") or "").strip()
    if not host:
        return False, "Set SMTP_HOST in the server environment."
    if not sender or "@" not in parseaddr(sender)[1]:
        return False, "Set SMTP_FROM to a valid sender address."
    try:
        port = int(env.get("SMTP_PORT", "587"))
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        return False, "SMTP_PORT must be a valid port number."
    username, password = env.get("SMTP_USER", "").strip(), env.get("SMTP_PASSWORD", "")
    if bool(username) != bool(password):
        return False, "Set both SMTP_USER and SMTP_PASSWORD, or leave both empty for an unauthenticated relay."
    return True, ""


def email_configured():
    return smtp_config_status()[0]


def agenda_email_html(local_date: str, body: str, lang: str = "en", test: bool = False, schedule: str = "07:00") -> str:
    """Render agenda text into self-contained, email-client-friendly HTML."""
    groups = [part.splitlines() for part in body.split("\n\n") if part.strip()]
    hero = groups.pop(0) if groups else [local_date]
    intro = ("This is a test email. Your daily agenda will use this same design."
             if test else "Your day, thoughtfully laid out.")
    direction = "rtl" if lang == "fa" else "ltr"
    align = "right" if lang == "fa" else "left"
    hero_html = "<br>".join(html.escape(line) for line in hero)
    cards = []
    for group in groups:
        heading = html.escape(group[0])
        items = group[1:]
        if items and all(line.lstrip().startswith("•") for line in items):
            content = '<ul style="margin:0;padding-inline-start:22px;color:#51445a;line-height:1.8">' + "".join(
                "<li>" + html.escape(line.lstrip()[1:].strip()) + "</li>" for line in items) + "</ul>"
        else:
            content = "".join("<p>" + html.escape(line) + "</p>" for line in items)
        cards.append(f'<tr><td style="padding:0 18px 14px"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #eadfed;border-radius:14px"><tr><td style="padding:18px 20px"><h2 style="margin:0 0 10px;color:#382340;font-size:16px">{heading}</h2>{content}</td></tr></table></td></tr>')
    if not cards:
        cards.append(f'<tr><td style="padding:0 18px 14px"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #eadfed;border-radius:14px"><tr><td style="padding:18px 20px;color:#75647d">{html.escape(body)}</td></tr></table></td></tr>')
    return f'''<!doctype html><html lang="{html.escape(lang)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Daily agenda</title></head>
<body dir="{direction}" style="margin:0;padding:0;background:#f5eff7;font-family:Arial,Helvetica,sans-serif;color:#382340;text-align:{align}">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f5eff7;padding:28px 12px"><tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:620px;background:#fff;border:1px solid #eadfed;border-radius:20px;overflow:hidden">
<tr><td style="padding:24px 28px;background:#382340;color:#fff"><div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#e8c8f2">My EveryDay Life</div><div style="font-size:12px;color:#ddc9e2;margin-top:5px">{'TEST EMAIL · ' if test else ''}{html.escape(local_date)}</div></td></tr>
<tr><td style="padding:28px 28px 16px"><div style="font-size:25px;font-weight:700;line-height:1.45">{hero_html}</div><p style="margin:10px 0 0;color:#75647d;font-size:14px;line-height:1.6">{html.escape(intro)}</p></td></tr>
{''.join(cards)}
<tr><td style="padding:18px 28px;background:#faf7fb;color:#8a7a91;font-size:12px;line-height:1.6">Sent at {html.escape(schedule)} in your saved time zone · Manage delivery in Settings.</td></tr>
</table></td></tr></table></body></html>'''


def send_agenda_email(recipient: str, local_date: str, body: str, lang: str = "en", test: bool = False, env=os.environ, schedule: str = "07:00") -> None:
    configured, reason = smtp_config_status(env)
    if not configured:
        raise MailConfigError(reason)
    host, sender = env["SMTP_HOST"].strip(), env["SMTP_FROM"].strip()
    recipient = parseaddr(recipient)[1]
    if "@" not in recipient:
        raise MailDeliveryError("The account email address is invalid.")
    msg = EmailMessage()
    msg["Subject"] = f"{'Test: ' if test else ''}Your daily agenda · {local_date}"
    msg["From"], msg["To"] = sender, recipient
    msg.set_content(body)
    msg.add_alternative(agenda_email_html(local_date, body, lang, test, schedule), subtype="html")
    try:
        port = int(env.get("SMTP_PORT", "587"))
        context = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as smtp:
                if env.get("SMTP_USER"):
                    smtp.login(env["SMTP_USER"], env["SMTP_PASSWORD"])
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                if env.get("SMTP_USER"):
                    smtp.login(env["SMTP_USER"], env["SMTP_PASSWORD"])
                smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailDeliveryError("SMTP authentication failed. Check SMTP_USER and SMTP_PASSWORD.") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailDeliveryError("The SMTP relay rejected the account email recipient.") from exc
    except smtplib.SMTPNotSupportedError as exc:
        raise MailDeliveryError("The SMTP relay does not support the required secure connection.") from exc
    except smtplib.SMTPException as exc:
        raise MailDeliveryError("The SMTP relay rejected the message. Check its settings and sender policy.") from exc
    except (OSError, ValueError) as exc:
        raise MailDeliveryError("Could not connect to the SMTP relay. Check SMTP_HOST and SMTP_PORT.") from exc
