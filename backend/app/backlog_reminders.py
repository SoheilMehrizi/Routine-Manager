"""Backlog email preferences and deterministic, time-zone-aware reminder events."""
import datetime as dt
import hashlib
import json
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc
CATCHUP_SECONDS = 3 * 60 * 60
DEFAULTS = {"enabled": False, "start_offsets": [0], "deadline_offsets": [1440, 60, 0], "overdue_every": 0}


def normalize_policy(value, defaults=False):
    if value is None:
        return {k: list(v) if isinstance(v, list) else v for k, v in DEFAULTS.items()} if defaults else None
    if not isinstance(value, dict):
        raise ValueError("Reminder settings must be an object")
    if not defaults:
        mode = value.get("mode", "inherit")
        if mode == "inherit":
            return None
        if mode == "off":
            return {"mode": "off"}
        if mode != "custom":
            raise ValueError("Reminder mode must be inherit, custom or off")
    result = {"enabled": value.get("enabled", False)} if defaults else {"mode": "custom"}
    if defaults and not isinstance(result["enabled"], bool):
        raise ValueError("Reminder delivery enabled must be a boolean")
    for name in ("start_offsets", "deadline_offsets"):
        offsets = value.get(name, DEFAULTS[name])
        if (not isinstance(offsets, list) or len(offsets) > 12
                or any(isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 525600 for n in offsets)):
            raise ValueError("Use at most 12 reminder offsets, in whole minutes from 0 to 525600")
        result[name] = sorted(set(offsets), reverse=True)
    repeat = value.get("overdue_every", 0)
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat != 0 and not 15 <= repeat <= 525600:
        raise ValueError("Overdue repeat must be 0 (off), or 15 to 525600 whole minutes")
    result["overdue_every"] = repeat
    return result


def effective_policy(parent, override):
    override = normalize_policy(override)
    if override is None:
        return parent
    if override["mode"] == "off":
        return {"start_offsets": [], "deadline_offsets": [], "overdue_every": 0}
    return override


def reminder_events(doc, prefs, now):
    """Only currently due slots; never replay a backlog of missed overdue emails."""
    settings = normalize_policy(prefs.get("backlog_reminders"), defaults=True)
    if not settings["enabled"] or doc.get("archived"):
        return []
    try:
        zone = ZoneInfo(prefs.get("tz") or "UTC")
    except Exception:
        zone = ZoneInfo("UTC")
    now = now.astimezone(UTC)
    output = []
    list_policy = effective_policy(settings, doc.get("reminders"))
    categories = {c["id"]: c for c in doc.get("categories", [])}
    cat_policies = {cid: effective_policy(list_policy, cat.get("reminders")) for cid, cat in categories.items()}
    tasks = doc.get("tasks", [])

    def add(item, scope, policy, scope_tasks=None):
        if scope_tasks and all(t.get("done") for t in scope_tasks):
            return
        for kind in ("start", "deadline"):
            value = item.get(kind)
            if not value:
                continue
            # fold=0 selects the first repeated hour; spring gaps shift forward on UTC conversion.
            try:
                target = dt.datetime.fromisoformat(value).replace(tzinfo=zone, fold=0).astimezone(UTC)
            except (ValueError, OverflowError):
                continue
            triggers = []
            for offset in policy[kind + "_offsets"]:
                try:
                    triggers.append((kind, offset, target - dt.timedelta(minutes=offset)))
                except OverflowError:
                    continue
            repeat = policy["overdue_every"] if kind == "deadline" else 0
            if repeat and now > target:
                slot = int((now - target).total_seconds() // (repeat * 60))
                if slot >= 1:
                    triggers.append(("overdue", repeat, target + dt.timedelta(minutes=repeat * slot)))
            for event_kind, offset, trigger in triggers:
                age = (now - trigger).total_seconds()
                if not 0 <= age < CATCHUP_SECONDS:
                    continue
                if event_kind != "overdue" and offset > 0 and now >= target:
                    continue  # A before-event reminder must not arrive after the event.
                identity = [doc["id"], scope, item["id"], event_kind, target.isoformat(), trigger.isoformat()]
                output.append({"key": hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                               "list_id": doc["id"], "list_title": doc["title"], "item_id": item["id"],
                               "title": item["title"], "scope": scope, "event": event_kind,
                               "target": target, "scheduled_at": trigger, "timezone": str(zone)})

    add(doc, "List", list_policy, tasks)
    for cid, category in categories.items():
        add(category, "Category", cat_policies[cid], [t for t in tasks if t.get("category") == cid])
    for task in tasks:
        if not task.get("done"):
            parent = cat_policies.get(task.get("category"), list_policy)
            add(task, "Task", effective_policy(parent, task.get("reminders")))
    return output


def reminder_content(event, lang="en"):
    fa = lang == "fa"
    kind = ({"start": "یادآوری شروع", "deadline": "یادآوری مهلت", "overdue": "مهلت گذشته"} if fa else
            {"start": "Start reminder", "deadline": "Deadline reminder", "overdue": "Overdue reminder"})[event["event"]]
    scope = {"List": "فهرست", "Category": "دسته", "Task": "کار"}[event["scope"]] if fa else event["scope"]
    when = event["target"].astimezone(ZoneInfo(event["timezone"])).strftime("%Y-%m-%d %H:%M")
    subject = (kind + " · " + event["title"]).replace("\r", " ").replace("\n", " ")[:240]
    body = (f"{kind}\n\n{scope}: {event['title']}\n" +
            ("فهرست: " if fa else "List: ") + event["list_title"] + "\n" +
            ("زمان: " if fa else "Scheduled time: ") + when + " · " + event["timezone"] + "\n\n" +
            ("تنظیم یادآورها در تنظیمات یا ویرایش مورد مربوط در My EveryDay Life." if fa else
             "Manage reminders in Settings or the item's editor in My EveryDay Life."))
    return subject, body
