import io
import os
import re
import zipfile
from contextlib import asynccontextmanager
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app import db, worker

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_RECIPIENTS = 5000


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    yield


app = FastAPI(title="Bulk Certificate Generator", lifespan=lifespan)
from fastapi.responses import RedirectResponse


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


class JobRequest(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "event_name": "Python Bootcamp 2026",
                "recipients": [
                    {"name": "Amit Sharma", "email": "amit@example.com"},
                    {"name": "Priya Singh", "email": "priya@example.com"},
                ],
            }
        },
    )
    event_name: str = Field(min_length=1, max_length=200)
    recipients: list[Any] = Field(min_length=1, max_length=MAX_RECIPIENTS)


def validate_recipient(r, seen):
    """Return {'name','email','error'}; error is None when valid."""
    if not isinstance(r, dict):
        return {"name": None, "email": None, "error": "recipient must be an object"}
    name = r.get("name").strip() if isinstance(r.get("name"), str) else ""
    email = r.get("email").strip() if isinstance(r.get("email"), str) else ""
    if not name:
        error = "name is required"
    elif len(name) > 100:
        error = "name must be at most 100 characters"
    elif not EMAIL_RE.match(email):
        error = "valid email is required"
    elif email.lower() in seen:
        error = "duplicate email in request"
    else:
        error = None
        seen.add(email.lower())
    return {"name": name or None, "email": email or None, "error": error}


@app.post("/jobs", status_code=202)
def create_job(req: JobRequest):
    seen = set()
    rows = [validate_recipient(r, seen) for r in req.recipients]
    job_id = db.create_job(req.event_name, rows)
    worker.submit_job(job_id)  # returns immediately; generation runs in background
    return {
        "job_id": job_id,
        "total": len(rows),
        "invalid": sum(1 for r in rows if r["error"]),
        "status_url": f"/jobs/{job_id}",
    }


@app.get("/jobs/{job_id}")
def job_status(job_id: int):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    done = job["succeeded"] + job["failed"]
    return {
        "job_id": job["id"],
        "event_name": job["event_name"],
        "status": job["status"],
        "total": job["total"],
        "succeeded": job["succeeded"],
        "failed": job["failed"],
        "pending": job["pending"],
        "progress_percent": round(done * 100 / job["total"], 1),
        "created_at": job["created_at"],
        "completed_at": job["completed_at"],
        "failures": [
            {"certificate_id": c["id"], "name": c["recipient_name"],
             "email": c["email"], "error": c["error"]}
            for c in db.get_certificates(job_id, "failed")
        ],
    }


@app.get("/jobs/{job_id}/certificates")
def list_certificates(job_id: int):
    if not db.get_job(job_id):
        raise HTTPException(404, "job not found")
    return [
        {"certificate_id": c["id"], "name": c["recipient_name"], "email": c["email"],
         "status": c["status"],
         "download_url": f"/certificates/{c['id']}/download" if c["status"] == "success" else None}
        for c in db.get_certificates(job_id)
    ]


@app.get("/certificates/{cert_id}/download")
def download_certificate(cert_id: int):
    cert = db.get_certificate(cert_id)
    if not cert:
        raise HTTPException(404, "certificate not found")
    if cert["status"] != "success" or not os.path.exists(cert["file_path"]):
        raise HTTPException(409, f"certificate not available (status: {cert['status']})")
    return FileResponse(cert["file_path"], media_type="application/pdf",
                        filename=f"certificate_{cert_id}.pdf")


@app.get("/jobs/{job_id}/download")
def download_job_zip(job_id: int):
    if not db.get_job(job_id):
        raise HTTPException(404, "job not found")
    certs = db.get_certificates(job_id, "success")
    if not certs:
        raise HTTPException(409, "no certificates generated yet")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for c in certs:
            z.write(c["file_path"], arcname=os.path.basename(c["file_path"]))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="job_{job_id}.zip"'})