import base64
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402


def make_app(db_path, **extra):
    cfg = {"TESTING": True, "DB_PATH": str(db_path), "SECRET_KEY": "test",
           "APP_PASSWORD": ""}
    cfg.update(extra)
    return create_app(cfg)


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path / "test.db")


@pytest.fixture
def client(app):
    return app.test_client()


def post(client, url, data=None, headers=None):
    """POST with a valid CSRF token."""
    with client.session_transaction() as s:
        s["csrf"] = "tok"
    payload = {"csrf_token": "tok"}
    payload.update(data or {})
    return client.post(url, data=payload, headers=headers)


def add(client, **kw):
    data = {"title": "Task", "priority": "Medium", "status": "Pending"}
    data.update(kw)
    return post(client, "/tasks", data)


def stat(html, name):
    return int(re.search(r'id="stat-%s"><span>(\d+)</span>' % name, html).group(1))


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.get_json() == {"status": "ok"}


def test_create_and_view(client):
    r = add(client, title="Finish lab report", description="Chapter 3",
            due_date="2999-01-01", priority="High")
    assert r.status_code == 302
    html = client.get("/").get_data(as_text=True)
    assert "Finish lab report" in html and "Chapter 3" in html
    assert "2999-01-01" in html


@pytest.mark.parametrize("bad", [
    {"title": ""}, {"title": "   "}, {"title": "x" * 101},
    {"description": "x" * 501}, {"due_date": "not-a-date"},
    {"due_date": "2026-02-30"}, {"priority": "Urgent"}, {"status": "Done"},
])
def test_validation_rejects_bad_input(client, bad):
    assert add(client, **bad).status_code == 400
    assert "No tasks found." in client.get("/").get_data(as_text=True)


def test_edit_task(client):
    add(client, title="Old title")
    assert "Old title" in client.get("/tasks/1/edit").get_data(as_text=True)
    r = post(client, "/tasks/1/edit", {"title": "New title", "priority": "Low",
                                       "status": "In Progress"})
    assert r.status_code == 302
    html = client.get("/").get_data(as_text=True)
    assert "New title" in html and "Old title" not in html
    assert "In Progress" in html


def test_edit_invalid_keeps_original(client):
    add(client, title="Keep me")
    assert post(client, "/tasks/1/edit", {"title": ""}).status_code == 400
    assert "Keep me" in client.get("/").get_data(as_text=True)


def test_toggle_complete_and_incomplete(client):
    add(client)
    post(client, "/tasks/1/toggle")
    html = client.get("/").get_data(as_text=True)
    assert stat(html, "completed") == 1 and stat(html, "pending") == 0
    post(client, "/tasks/1/toggle")
    html = client.get("/").get_data(as_text=True)
    assert stat(html, "completed") == 0 and stat(html, "pending") == 1


def test_delete(client):
    add(client, title="Delete me")
    assert post(client, "/tasks/1/delete").status_code == 302
    assert "Delete me" not in client.get("/").get_data(as_text=True)
    assert post(client, "/tasks/1/delete").status_code == 404


def test_missing_task_404(client):
    assert client.get("/tasks/99/edit").status_code == 404
    assert post(client, "/tasks/99/toggle").status_code == 404


def test_search_and_filters(client):
    add(client, title="Write essay", priority="High")
    add(client, title="Buy milk", description="groceries", priority="Low")
    add(client, title="Read paper", status="Completed", priority="Low")
    html = client.get("/?q=essay").get_data(as_text=True)
    assert "Write essay" in html and "Buy milk" not in html
    assert "Buy milk" in client.get("/?q=GROCERIES").get_data(as_text=True)
    html = client.get("/?priority=High").get_data(as_text=True)
    assert "Write essay" in html and "Buy milk" not in html
    html = client.get("/?status=Completed").get_data(as_text=True)
    assert "Read paper" in html and "Write essay" not in html
    assert "No tasks found." in client.get("/?q=%25").get_data(as_text=True)


def test_summary_and_overdue(client):
    add(client, title="A", due_date="2000-01-01", status="Completed")  # done
    add(client, title="B", due_date="2000-01-01")                      # overdue
    add(client, title="C", due_date="2999-01-01")                      # future
    add(client, title="D", status="In Progress")                       # no date
    html = client.get("/").get_data(as_text=True)
    assert (stat(html, "total"), stat(html, "completed"),
            stat(html, "pending"), stat(html, "overdue")) == (4, 1, 3, 1)
    html = client.get("/?status=Overdue").get_data(as_text=True)
    assert "<h3>B</h3>" in html and "<h3>A</h3>" not in html
    assert "<h3>C</h3>" not in html


def test_html_is_escaped(client):
    add(client, title="<script>alert(1)</script>")
    html = client.get("/").get_data(as_text=True)
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


def test_data_persists_across_app_restart(tmp_path):
    db = tmp_path / "persist.db"
    add(make_app(db).test_client(), title="Survives restart")
    html = make_app(db).test_client().get("/").get_data(as_text=True)
    assert "Survives restart" in html


def test_csrf_required(client):
    assert client.post("/tasks", data={"title": "x"}).status_code == 400
    assert "No tasks found." in client.get("/").get_data(as_text=True)


def test_basic_auth_when_password_set(tmp_path):
    c = make_app(tmp_path / "auth.db", APP_PASSWORD="s3cret").test_client()
    assert c.get("/").status_code == 401
    assert c.get("/health").status_code == 200
    good = {"Authorization": "Basic " + base64.b64encode(b"student:s3cret").decode()}
    bad = {"Authorization": "Basic " + base64.b64encode(b"student:wrong").decode()}
    assert c.get("/", headers=good).status_code == 200
    assert c.get("/", headers=bad).status_code == 401
