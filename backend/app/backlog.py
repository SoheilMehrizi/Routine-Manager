"""Validation and default category ordering for backlog list documents."""
import re


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
        clean_cats.append({"id": cid, "title": title(category.get("title"), "Category title")})
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
                            "category": cid, "done": done, "description": description})
    if custom:
        queue = data.get("queue", [])
        if (not isinstance(queue, list) or any(not isinstance(t, str) for t in queue)
                or len(queue) != len(task_ids) or set(queue) != task_ids):
            raise ValueError("Custom queue must contain every task exactly once")
    else:
        queue = [task["id"] for cid in [c["id"] for c in clean_cats] + [None]
                 for task in clean_tasks if task["category"] == cid]
    return {"title": title(data.get("title"), "Backlog title"), "archived": archived,
            "categories": clean_cats, "tasks": clean_tasks, "queue": queue, "custom_order": custom}
