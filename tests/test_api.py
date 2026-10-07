import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import main, worker

GOOD = [
    {"name": "Amit Sharma", "email": "amit@example.com"},
    {"name": "Priya Singh", "email": "priya@example.com"},
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh temp DB + output folder per test; the job runs synchronously."""
    monkeypatch.setenv("CERTGEN_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("CERTGEN_OUTPUT_DIR", str(tmp_path / "certs"))
    monkeypatch.setattr(worker, "submit_job", worker.process_job)
    with TestClient(main.app) as c:  # runs lifespan -> init_db()
        yield c


def create(client, recipients, event="Python Bootcamp"):
    return client.post("/jobs", json={"event_name": event, "recipients": recipients})


# ---------- 1. Creating a job ----------
def test_create_job_returns_202_and_job_id(client):
    r = create(client, GOOD)
    assert r.status_code == 202
    body = r.json()
    assert body["total"] == 2 and body["invalid"] == 0
    assert body["status_url"] == f"/jobs/{body['job_id']}"


def test_job_is_pending_before_worker_runs(client, monkeypatch):
    monkeypatch.setattr(worker, "submit_job", lambda job_id: None)  # worker never runs
    job_id = create(client, GOOD).json()["job_id"]
    s = client.get(f"/jobs/{job_id}").json()
    assert s["status"] == "pending" and s["pending"] == 2 and s["progress_percent"] == 0


# ---------- 2. Input validation ----------
@pytest.mark.parametrize("payload", [
    {"event_name": "X", "recipients": []},          # empty list
    {"recipients": GOOD},                           # missing event name
    {"event_name": "", "recipients": GOOD},         # blank event name
    {"event_name": "X"},                            # missing recipients
])
def test_bad_request_is_rejected(client, payload):
    assert client.post("/jobs", json=payload).status_code == 422


def test_invalid_recipients_are_reported_not_fatal(client):
    recipients = GOOD + [
        {"name": "", "email": "a@b.com"},                  # no name
        {"name": "Bad Email", "email": "not-an-email"},    # bad email
        {"name": "Dup", "email": "amit@example.com"},      # duplicate
        "not an object",                                   # wrong type
    ]
    r = create(client, recipients).json()
    assert r["total"] == 6 and r["invalid"] == 4
    s = client.get(f"/jobs/{r['job_id']}").json()
    assert s["succeeded"] == 2 and s["failed"] == 4
    errors = {f["error"] for f in s["failures"]}
    assert "name is required" in errors and "valid email is required" in errors
    assert "duplicate email in request" in errors


# ---------- 3. Certificate generation ----------
def test_certificate_pdf_is_created(client):
    job_id = create(client, GOOD).json()["job_id"]
    certs = client.get(f"/jobs/{job_id}/certificates").json()
    assert len(certs) == 2 and all(c["status"] == "success" for c in certs)
    pdf = client.get(certs[0]["download_url"])
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")


# ---------- 4. Job status / progress ----------
def test_status_and_progress_after_completion(client):
    job_id = create(client, GOOD).json()["job_id"]
    s = client.get(f"/jobs/{job_id}").json()
    assert s["status"] == "completed"
    assert s["progress_percent"] == 100.0
    assert s["succeeded"] == 2 and s["failed"] == 0 and s["failures"] == []


def test_unknown_job_returns_404(client):
    assert client.get("/jobs/9999").status_code == 404


# ---------- 5. Individual certificate failure ----------
def test_one_failure_does_not_stop_the_others(client, monkeypatch):
    real = worker.generate_certificate

    def flaky(cert_id, name, event_name):
        if name == "Boom":
            raise RuntimeError("disk full")
        return real(cert_id, name, event_name)

    monkeypatch.setattr(worker, "generate_certificate", flaky)
    recipients = GOOD + [{"name": "Boom", "email": "boom@example.com"}]
    job_id = create(client, recipients).json()["job_id"]

    s = client.get(f"/jobs/{job_id}").json()
    assert s["status"] == "completed_with_errors"
    assert s["succeeded"] == 2 and s["failed"] == 1
    assert "disk full" in s["failures"][0]["error"]
    assert s["failures"][0]["email"] == "boom@example.com"


# ---------- 6. Retrieving certificates ----------
def test_failed_certificate_cannot_be_downloaded(client):
    job_id = create(client, [{"name": "", "email": "x@y.com"}] + GOOD).json()["job_id"]
    certs = client.get(f"/jobs/{job_id}/certificates").json()
    failed = next(c for c in certs if c["status"] == "failed")
    assert failed["download_url"] is None
    assert client.get(f"/certificates/{failed['certificate_id']}/download").status_code == 409


def test_unknown_certificate_returns_404(client):
    assert client.get("/certificates/9999/download").status_code == 404


def test_download_all_as_zip(client):
    job_id = create(client, GOOD).json()["job_id"]
    r = client.get(f"/jobs/{job_id}/download")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert len(names) == 2 and all(n.endswith(".pdf") for n in names)