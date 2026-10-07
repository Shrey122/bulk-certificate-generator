import logging
from concurrent.futures import ThreadPoolExecutor

from app import db
from app.generator import generate_certificate

log = logging.getLogger("certgen")
_executor = ThreadPoolExecutor(max_workers=4)  # up to 4 jobs run in parallel


def process_job(job_id):
    """Generate every pending certificate in a job. One failure never stops the rest."""
    job = db.get_job(job_id)
    db.set_job_status(job_id, "processing")
    try:
        for cert in db.get_certificates(job_id, status="pending"):
            try:
                path = generate_certificate(cert["id"], cert["recipient_name"], job["event_name"])
                db.mark_success(cert["id"], path)
            except Exception as e:  # isolate per-certificate failures
                log.exception("Certificate %s failed", cert["id"])
                db.mark_failed(cert["id"], f"generation failed: {e}")
    finally:
        db.finish_job(job_id)  # job always reaches a final state


def submit_job(job_id):
    """Queue a job and return immediately."""
    _executor.submit(process_job, job_id)