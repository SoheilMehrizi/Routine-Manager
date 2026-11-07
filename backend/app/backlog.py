"""Validation and default category ordering for backlog list documents."""
import re
import calendar
import copy
import datetime as dt
import uuid


def normalize_backlog(data):
    def title(value, label):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
            raise ValueError(label + " must contain 1 to 200 characters")
        return value.strip()

    def identifier(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{4,40}", value):
            raise ValueError("Invalid backlog item id")
        return value

    archived = data.get("archived", False)
    custom = data.get("custom_order", False)
    if not isinstance(archived, bool) or not isinstance(custom, bool):
        raise ValueError("Archived and custom_order must be booleans")
    categories, tasks = data.get("categories", []), data.get("tasks", [])
    if not isinstance(categories, list) or not isinstance(tasks, list):
        raise ValueError("Categories and tasks must be arrays")
    clean_cats, clean_tasks, ids = [], [], set()
    for category in categories:
        if not isinstance(category, dict):
            raise ValueError("Invalid category")
        cid = identifier(category.get("id"))
        if cid in ids:
            raise ValueError("Duplicate category id")
        ids.add(cid)
        clean_cats.append({"id": cid, "title": title(category.get("title"), "Category title"), **schedule_fields(category)})
    task_ids = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("Invalid task")
        tid = identifier(task.get("id"))
        if tid in task_ids:
            raise ValueError("Duplicate task id")
        task_ids.add(tid)
        cid = task.get("category") or None
        if cid is not None and (not isinstance(cid, str) or cid not in ids):
            raise ValueError("Task category does not belong to this list")
        done = task.get("done", False)
        description = task.get("description", "")
        if not isinstance(done, bool) or not isinstance(description, str) or len(description) > 10000:
            raise ValueError("Invalid task completion or description")
        clean_tasks.append({"id": tid, "title": title(task.get("title"), "Task title"),
                            "category": cid, "done": done, "description": description, **schedule_fields(task),
                            **task_details({**task, **schedule_fields(task)}, identifier, title)})
    if custom:
        queue = data.get("queue", [])
        if (not isinstance(queue, list) or any(not isinstance(t, str) for t in queue)
                or len(queue) != len(task_ids) or set(queue) != task_ids):
            raise ValueError("Custom queue must contain every task exactly once")
    else:
        queue = [task["id"] for cid in [c["id"] for c in clean_cats] + [None]
                 for task in clean_tasks if task["category"] == cid]
    return tag_conflicts({"title": title(data.get("title"), "Backlog title"), "archived": archived,
            "categories": clean_cats, "tasks": clean_tasks, "queue": queue, "custom_order": custom, **schedule_fields(data)})


def schedule_fields(data):
    result = {}
    for key in ("start", "deadline"):
        value = data.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError("Schedule dates must be local date/time strings")
        if value:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", value):
                raise ValueError("Use YYYY-MM-DDTHH:MM for schedule dates")
            try:
                dt.datetime.fromisoformat(value)
            except ValueError:
                raise ValueError("Invalid schedule date")
        result[key] = value
    return result


def date_conflicts(item, parents=()):
    tags = []
    if item.get("start") and item.get("deadline") and item["start"] > item["deadline"]:
        tags.append("reversed_dates")
    for label, parent in parents:
        if any(value and ((parent.get("start") and value < parent["start"])
                          or (parent.get("deadline") and value > parent["deadline"]))
               for value in (item.get("start"), item.get("deadline"))):
            tags.append("outside_" + label)
    return tags


def tag_conflicts(doc):
    doc["date_conflicts"] = date_conflicts(doc)
    categories = {c["id"]: c for c in doc["categories"]}
    for category in doc["categories"]:
        category["date_conflicts"] = date_conflicts(category, [("list", doc)])
    for task in doc["tasks"]:
        parents = [("list", doc)]
        if task["category"]:
            parents.append(("category", categories[task["category"]]))
        task["date_conflicts"] = date_conflicts(task, parents)
    return doc


def task_details(task, identifier, title):
    estimate = task.get("estimate", None)
    if estimate == "":
        estimate = None
    if estimate is not None and (isinstance(estimate, bool) or not isinstance(estimate, int) or not 1 <= estimate <= 1000000):
        raise ValueError("Estimate must be a positive number of minutes")
    subtasks = task.get("subtasks", [])
    if not isinstance(subtasks, list) or len(subtasks) > 200:
        raise ValueError("Provide at most 200 subtasks")
    clean_subtasks, seen = [], set()
    for sub in subtasks:
        if not isinstance(sub, dict):
            raise ValueError("Invalid subtask")
        sid = identifier(sub.get("id"))
        if sid in seen or not isinstance(sub.get("done", False), bool):
            raise ValueError("Invalid or duplicate subtask")
        seen.add(sid)
        clean_subtasks.append({"id": sid, "title": title(sub.get("title"), "Subtask title"), "done": sub.get("done", False)})
    recurrence = task.get("recurrence")
    clean_rec = None
    if recurrence is not None:
        if not isinstance(recurrence, dict) or recurrence.get("unit") not in ("day", "week", "month"):
            raise ValueError("Recurrence must use day, week or month")
        every = recurrence.get("every", 1)
        if isinstance(every, bool) or not isinstance(every, int) or not 1 <= every <= 365:
            raise ValueError("Recurrence interval must be between 1 and 365")
        clean_rec = {"unit": recurrence["unit"], "every": every}
        if recurrence["unit"] == "month":
            for key in ("start", "deadline"):
                if task.get(key):
                    day = recurrence.get(key + "_day", dt.datetime.fromisoformat(task[key]).day)
                    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31:
                        raise ValueError("Invalid monthly recurrence anchor")
                    clean_rec[key + "_day"] = day
    result = {"estimate": estimate, "subtasks": clean_subtasks, "recurrence": clean_rec}
    if task.get("next_id"):
        result["next_id"] = identifier(task["next_id"])
    return result


def advance_date(value, recurrence, count, key):
    value = dt.datetime.fromisoformat(value)
    amount = recurrence["every"] * count
    if recurrence["unit"] == "month":
        month_index = value.year * 12 + value.month - 1 + amount
        year, month = divmod(month_index, 12)
        day = min(recurrence.get(key + "_day", value.day), calendar.monthrange(year, month + 1)[1])
        return value.replace(year=year, month=month + 1, day=day)
    return value + dt.timedelta(days=amount * (7 if recurrence["unit"] == "week" else 1))


def complete_backlog(data, tid, done, now_local):
    """Desired completion state, with at most one successor for each occurrence."""
    doc = normalize_backlog(data)
    task = next((t for t in doc["tasks"] if t["id"] == tid), None)
    if task is None:
        raise LookupError("Task not found")
    if not isinstance(done, bool):
        raise ValueError("Done must be a boolean")
    if doc["archived"]:
        raise ValueError("Restore the list before editing its contents")
    if done and not task["done"] and task["recurrence"] and not task.get("next_id"):
        recurrence = task["recurrence"]
        anchor_key = "deadline" if task["deadline"] else "start"
        anchor = task[anchor_key] or now_local.isoformat(timespec="minutes")
        origin = dt.datetime.fromisoformat(anchor)
        if recurrence["unit"] == "month":
            months = (now_local.year - origin.year) * 12 + now_local.month - origin.month
            count = max(1, months // recurrence["every"])
        else:
            days = recurrence["every"] * (7 if recurrence["unit"] == "week" else 1)
            count = max(1, (now_local - origin).days // days)
        while advance_date(anchor, recurrence, count, anchor_key) <= now_local:
            count += 1
        child = copy.deepcopy(task)
        child["id"], child["done"] = uuid.uuid4().hex, False
        child.pop("next_id", None)
        for key in ("start", "deadline"):
            if child[key]:
                child[key] = advance_date(child[key], recurrence, count, key).isoformat(timespec="minutes")
        if not child["start"] and not child["deadline"]:
            child["start"] = advance_date(anchor, recurrence, count, "start").isoformat(timespec="minutes")
            if recurrence["unit"] == "month":
                child["recurrence"]["start_day"] = origin.day
        for sub in child["subtasks"]:
            sub["done"] = False
        task["next_id"] = child["id"]
        doc["tasks"].append(child)
        doc["queue"].append(child["id"])
    task["done"] = done
    return normalize_backlog(doc)
