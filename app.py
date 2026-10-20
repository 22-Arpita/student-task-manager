"""Student Task Manager - Flask + SQLite (single file, beginner-friendly)."""
import hmac
import os
import secrets
import sqlite3
from datetime import date, datetime

from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)

PRIORITIES = ("Low", "Medium", "High")
STATUSES = ("Pending", "In Progress", "Completed")
FILTER_STATUSES = STATUSES + ("Overdue",)
DEFAULT_FORM = {"title": "", "description": "", "due_date": "",
                "priority": "Medium", "status": "Pending"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    due_date    TEXT,
    priority    TEXT NOT NULL DEFAULT 'Medium'
                CHECK (priority IN ('Low', 'Medium', 'High')),
    status      TEXT NOT NULL DEFAULT 'Pending'
                CHECK (status IN ('Pending', 'In Progress', 'Completed')),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

ORDER_BY = (" ORDER BY (status = 'Completed'), (due_date IS NULL), due_date,"
            " CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END,"
            " id DESC")


def connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def validate_task(form):
    """Return (clean_values, errors) for a submitted task form."""
    values = {
        "title": form.get("title", "").strip(),
        "description": form.get("description", "").strip(),
        "due_date": form.get("due_date", "").strip(),
        "priority": form.get("priority", "Medium"),
        "status": form.get("status", "Pending"),
    }
    errors = []
    if not values["title"]:
        errors.append("Title is required.")
    elif len(values["title"]) > 100:
        errors.append("Title must be 100 characters or fewer.")
    if len(values["description"]) > 500:
        errors.append("Description must be 500 characters or fewer.")
    if values["due_date"]:
        try:
            values["due_date"] = datetime.strptime(
                values["due_date"], "%Y-%m-%d").date().isoformat()
        except ValueError:
            errors.append("Due date must be a valid date (YYYY-MM-DD).")
    if values["priority"] not in PRIORITIES:
        errors.append("Priority must be Low, Medium or High.")
    if values["status"] not in STATUSES:
        errors.append("Status must be Pending, In Progress or Completed.")
    return values, errors


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        DB_PATH=os.environ.get("DB_PATH",
                               os.path.join(app.root_path, "tasks.db")),
        APP_USER=os.environ.get("APP_USER", "student"),
        APP_PASSWORD=os.environ.get("APP_PASSWORD", ""),  # blank = no login
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)

    db_dir = os.path.dirname(app.config["DB_PATH"])
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    init_conn = connect(app.config["DB_PATH"])
    init_conn.executescript(SCHEMA)
    init_conn.close()

    def get_db():
        if "db" not in g:
            g.db = connect(app.config["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    # ---- security: optional HTTP Basic login + CSRF token on every POST ----
    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return session["csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def require_login():
        password = app.config["APP_PASSWORD"]
        if not password or request.endpoint in ("health", "static"):
            return None
        auth = request.authorization
        if (auth
                and hmac.compare_digest((auth.username or "").encode(),
                                        app.config["APP_USER"].encode())
                and hmac.compare_digest((auth.password or "").encode(),
                                        password.encode())):
            return None
        return Response("Login required", 401,
                        {"WWW-Authenticate": 'Basic realm="Task Manager"'})

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            sent = request.form.get("csrf_token", "").encode()
            expected = session.get("csrf", "").encode()
            if not expected or not hmac.compare_digest(sent, expected):
                abort(400, "Invalid or missing CSRF token.")

    # ---- page rendering ----
    def page(form=None, edit_id=None, errors=(), code=200):
        db = get_db()
        edit_task = None
        if edit_id is not None:
            edit_task = db.execute("SELECT * FROM tasks WHERE id = ?",
                                   (edit_id,)).fetchone()
        if form is None:
            form = dict(edit_task) if edit_task else DEFAULT_FORM

        today = date.today().isoformat()
        q = request.args.get("q", "").strip()
        f_status = request.args.get("status", "All")
        f_priority = request.args.get("priority", "All")

        where, params = [], []
        if q:
            esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
            params += ["%" + esc + "%"] * 2
        if f_status == "Overdue":
            where.append("status != 'Completed' AND due_date IS NOT NULL "
                         "AND due_date < ?")
            params.append(today)
        elif f_status in STATUSES:
            where.append("status = ?")
            params.append(f_status)
        if f_priority in PRIORITIES:
            where.append("priority = ?")
            params.append(f_priority)
        sql = "SELECT * FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        tasks = db.execute(sql + ORDER_BY, params).fetchall()

        summary = db.execute(
            "SELECT COUNT(*) AS total,"
            " COALESCE(SUM(status = 'Completed'), 0) AS completed,"
            " COALESCE(SUM(status != 'Completed'), 0) AS pending,"
            " COALESCE(SUM(status != 'Completed' AND due_date IS NOT NULL"
            "              AND due_date < ?), 0) AS overdue"
            " FROM tasks", (today,)).fetchone()

        html = render_template(
            "index.html", tasks=tasks, summary=summary, form=form,
            edit_task=edit_task, errors=errors, today=today,
            filters={"q": q, "status": f_status, "priority": f_priority},
            priorities=PRIORITIES, statuses=STATUSES,
            filter_statuses=FILTER_STATUSES)
        return html, code

    # ---- routes ----
    @app.get("/")
    def index():
        return page()

    @app.post("/tasks")
    def create_task():
        values, errors = validate_task(request.form)
        if errors:
            return page(form=values, errors=errors, code=400)
        db = get_db()
        db.execute("INSERT INTO tasks (title, description, due_date, priority,"
                   " status) VALUES (:title, :description, :due_date,"
                   " :priority, :status)", {**values,
                                            "due_date": values["due_date"] or None})
        db.commit()
        flash("Task added.")
        return redirect(url_for("index"))

    @app.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
    def edit_task(task_id):
        db = get_db()
        if db.execute("SELECT 1 FROM tasks WHERE id = ?",
                      (task_id,)).fetchone() is None:
            abort(404)
        if request.method == "GET":
            return page(edit_id=task_id)
        values, errors = validate_task(request.form)
        if errors:
            return page(form=values, edit_id=task_id, errors=errors, code=400)
        db.execute("UPDATE tasks SET title = :title, description = :description,"
                   " due_date = :due_date, priority = :priority,"
                   " status = :status WHERE id = :id",
                   {**values, "due_date": values["due_date"] or None,
                    "id": task_id})
        db.commit()
        flash("Task updated.")
        return redirect(url_for("index"))

    @app.post("/tasks/<int:task_id>/toggle")
    def toggle_task(task_id):
        db = get_db()
        cur = db.execute(
            "UPDATE tasks SET status = CASE WHEN status = 'Completed'"
            " THEN 'Pending' ELSE 'Completed' END WHERE id = ?", (task_id,))
        db.commit()
        if cur.rowcount == 0:
            abort(404)
        return redirect(url_for("index"))

    @app.post("/tasks/<int:task_id>/delete")
    def delete_task(task_id):
        db = get_db()
        cur = db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        db.commit()
        if cur.rowcount == 0:
            abort(404)
        flash("Task deleted.")
        return redirect(url_for("index"))

    @app.get("/health")
    def health():
        try:
            get_db().execute("SELECT 1").fetchone()
        except sqlite3.Error:
            return jsonify(status="error"), 503
        return jsonify(status="ok")

    return app


if __name__ == "__main__":
    create_app().run(host=os.environ.get("HOST", "127.0.0.1"),
                     port=int(os.environ.get("PORT", "5000")),
                     debug=os.environ.get("FLASK_DEBUG") == "1")
