"""Exercise the actual record handlers against an isolated in-memory database."""
import ast
from pathlib import Path
import re
import unittest
try:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.backlog import normalize_backlog
    from fastapi import HTTPException
    from sqlalchemy import JSON, String, create_engine, select
    from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
except ImportError:
    raise unittest.SkipTest("Run inside the API image: FastAPI and SQLAlchemy are required")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    prefs: Mapped[dict] = mapped_column(JSON, default=dict)


class Record(Base):
    __tablename__ = "records"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON)


source = ast.parse((Path(__file__).resolve().parents[1] / "app/main.py").read_text(encoding="utf-8"))
names = {"normalize_record_data", "list_records", "upsert_record", "delete_record", "kind_or_404", "save_backlog_batch"}
nodes = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names]
for node in nodes:
    node.decorator_list = []
    node.args.defaults = []
scope = dict(User=User, Record=Record, Session=Session, select=select, HTTPException=HTTPException,
             normalize_backlog=normalize_backlog, KINDS={"backlogs"}, ID_RE=re.compile(r"^[A-Za-z0-9_-]{4,40}$"))
exec(compile(ast.Module(body=nodes, type_ignores=[]), "main.py handlers", "exec"), scope)


class BacklogStorage(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.s = Session(self.engine)
        self.a, self.b = User(id=1, prefs={}), User(id=2, prefs={"lang": "fa"})
        self.s.add_all([self.a, self.b])
        self.s.commit()

    def tearDown(self):
        self.s.close()
        self.engine.dispose()

    def lists(self, user):
        return scope["list_records"]("backlogs", user, self.s)

    def test_defaults_once_and_no_recreation_after_delete(self):
        lists = self.lists(self.a)
        self.assertEqual(len(lists), 4)
        self.assertEqual(lists[0]["title"], "First-priority project")
        self.assertEqual(len(self.lists(self.a)), 4)
        for item in lists:
            scope["delete_record"]("backlogs", item["id"], self.a, self.s)
        self.assertEqual(self.lists(self.a), [])

    def test_users_isolated_even_with_matching_ids(self):
        self.lists(self.a)
        other = self.lists(self.b)
        self.assertEqual(other[0]["title"], "پروژه با اولویت اول")
        rid = other[0]["id"]
        scope["upsert_record"]("backlogs", rid, {"title": " Renamed ", "archived": True, "user_id": 2}, self.a, self.s)
        self.assertEqual(self.lists(self.a)[0]["title"], "Renamed")
        self.assertTrue(self.lists(self.a)[0]["archived"])
        self.assertEqual(self.lists(self.b), other)
        scope["delete_record"]("backlogs", rid, self.a, self.s)
        self.assertEqual(self.lists(self.b), other)

    def test_validation_and_owner_fields(self):
        normalize = scope["normalize_record_data"]
        for data in [{"title": " "}, {"title": "x" * 201}, {"title": 123}, {"title": "OK", "archived": "false"}]:
            with self.assertRaises(HTTPException):
                normalize("backlogs", data)
        self.assertEqual(normalize("backlogs", {"title": " OK ", "id": "fake", "user_id": 2}),
                         {"title": "OK", "archived": False, "categories": [], "tasks": [], "queue": [], "custom_order": False})


    def test_nested_data_default_and_mixed_queue(self):
        doc = {"title": "Project", "categories": [{"id": "cat1", "title": "A"}, {"id": "cat2", "title": "B"}],
               "tasks": [{"id": tid, "title": tid, "category": cid} for tid, cid in
                         [("task1", "cat1"), ("task2", "cat2"), ("task3", "cat2"), ("task4", "cat1")]]}
        clean = normalize_backlog(doc)
        self.assertEqual(clean["queue"], ["task1", "task4", "task2", "task3"])
        clean.update(custom_order=True, queue=["task1", "task2", "task3", "task4"])
        self.assertEqual(normalize_backlog(clean)["queue"], clean["queue"])
        for patch in [{"queue": ["task1"]}, {"queue": ["task1"] * 4},
                      {"tasks": [{"id": "task5", "title": "X", "category": "foreign"}]}]:
            with self.assertRaises(ValueError):
                normalize_backlog({**clean, **patch})

    def test_atomic_batch_rejects_foreign_and_archived_lists(self):
        a, b = self.lists(self.a), self.lists(self.b)
        first = {**a[0], "title": "Changed", "tasks": [{"id": "task1", "title": "Work"}]}
        with self.assertRaises(HTTPException):
            scope["save_backlog_batch"]({"items": [first, {"id": "foreign-list", "title": "Foreign"}]}, self.a, self.s)
        self.assertNotEqual(self.lists(self.a)[0]["title"], "Changed")
        result = scope["save_backlog_batch"]({"items": [first]}, self.a, self.s)
        self.assertEqual(result[0]["tasks"][0]["title"], "Work")
        self.assertEqual(self.lists(self.b), b)
        scope["upsert_record"]("backlogs", a[1]["id"], {**a[1], "archived": True}, self.a, self.s)
        with self.assertRaises(HTTPException):
            scope["save_backlog_batch"]({"items": [first, a[1]]}, self.a, self.s)

    def test_cross_list_batch_moves_task_and_preserves_queue(self):
        lists = self.lists(self.a)
        source, target = lists[:2]
        source["tasks"] = [{"id": "task1", "title": "Move me", "description": "Keep me"}]
        scope["save_backlog_batch"]({"items": [source]}, self.a, self.s)
        source["tasks"] = []
        target.update(tasks=[{"id": "task1", "title": "Move me", "description": "Keep me"}], custom_order=True, queue=["task1"])
        scope["save_backlog_batch"]({"items": [source, target]}, self.a, self.s)
        saved = self.lists(self.a)
        self.assertEqual(saved[0]["tasks"], [])
        self.assertEqual(saved[1]["queue"], ["task1"])
        self.assertEqual(saved[1]["tasks"][0]["description"], "Keep me")
