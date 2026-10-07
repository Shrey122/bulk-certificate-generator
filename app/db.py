import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_conn():
    """New connection per call, so it is safe to use from worker threads."""
    conn = sqlite3.connect(os.environ.get("CERTGEN_DB", "certgen.db"), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name   TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending',
            total        INTEGER NOT NULL,
            created_at   TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS certificates (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id         INTEGER NOT NULL REFERENCES jobs(id),
            recipient_name TEXT,
            email          TEXT,
            status         TEXT NOT NULL DEFAULT 'pending',
            file_path      TEXT,
            error          TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_cert_job ON certificates(job_id);
        """)


def create_job(event_name, rows):
    """rows: [{'name','email','error'}]. Rows with an 'error' are stored as failed."""
    with get_conn() as c:
        job_id = c.execute(
            "INSERT INTO jobs (event_name, total, created_at) VALUES (?,?,?)",
            (event_name, len(rows), _now()),
        ).lastrowid
        c.executemany(
            "INSERT INTO certificates (job_id, recipient_name, email, status, error) "
            "VALUES (?,?,?,?,?)",
            [
                (job_id, r.get("name"), r.get("email"),
                 "failed" if r.get("error") else "pending", r.get("error"))
                for r in rows
            ],
        )
    return job_id


def get_job(job_id):
    with get_conn() as c:
        row = c.execute("""
            SELECT j.*,
              COALESCE(SUM(c.status='success'),0) AS succeeded,
              COALESCE(SUM(c.status='failed'),0)  AS failed,
              COALESCE(SUM(c.status='pending'),0) AS pending
            FROM jobs j LEFT JOIN certificates c ON c.job_id = j.id
            WHERE j.id = ? GROUP BY j.id""", (job_id,)).fetchone()
    return dict(row) if row else None


def get_certificates(job_id, status=None):
    sql, args = "SELECT * FROM certificates WHERE job_id = ?", [job_id]
    if status:
        sql, args = sql + " AND status = ?", args + [status]
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY id", args)]


def get_certificate(cert_id):
    with get_conn() as c:
        row = c.execute("SELECT * FROM certificates WHERE id = ?", (cert_id,)).fetchone()
    return dict(row) if row else None


def mark_success(cert_id, file_path):
    with get_conn() as c:
        c.execute("UPDATE certificates SET status='success', file_path=? WHERE id=?",
                  (file_path, cert_id))


def mark_failed(cert_id, error):
    with get_conn() as c:
        c.execute("UPDATE certificates SET status='failed', error=? WHERE id=?",
                  (error, cert_id))


def set_job_status(job_id, status):
    with get_conn() as c:
        c.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))


def finish_job(job_id):
    """completed if no failures, else completed_with_errors."""
    job = get_job(job_id)
    status = "completed" if job["failed"] == 0 else "completed_with_errors"
    with get_conn() as c:
        c.execute("UPDATE jobs SET status=?, completed_at=? WHERE id=?",
                  (status, _now(), job_id))