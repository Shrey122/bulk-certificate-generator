from app import db, worker

db.init_db()
job_id = db.create_job("Python Bootcamp 2026", [
    {"name": "Amit Sharma", "email": "amit@example.com"},
    {"name": "Priya Singh", "email": "priya@example.com"},
    {"name": "", "email": "bad", "error": "name is required"},
])
worker.process_job(job_id)
print(db.get_job(job_id))
for c in db.get_certificates(job_id):
    print(c["id"], c["recipient_name"], c["status"], c["error"])