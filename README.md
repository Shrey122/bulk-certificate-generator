# Bulk Certificate Generator

A backend API that accepts a list of recipients, generates a PDF certificate for each
one from a predefined template, and lets the client track progress and download results.

**Stack:** Python, FastAPI, SQLite (raw SQL via `sqlite3`), ReportLab, pytest.

## Project structure

```
app/
  main.py       API endpoints, request validation
  db.py         SQLite schema and queries
  generator.py  PDF certificate template (ReportLab)
  worker.py     Background job processing
tests/
  test_api.py
requirements.txt
```

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux
python -m pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload
```

The API runs at http://127.0.0.1:8000. Interactive docs: http://127.0.0.1:8000/docs

The database (`certgen.db`) and the `certificates/` folder are created automatically.

## Run tests

```bash
python -m pytest -v
```

Tests use a temporary database and output folder, so they never touch real data.

## API

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/jobs` | Submit a bulk request (returns `202` + job id) |
| GET | `/jobs/{id}` | Status, progress, and failure details |
| GET | `/jobs/{id}/certificates` | List certificates in a job with download links |
| GET | `/certificates/{id}/download` | Download one PDF |
| GET | `/jobs/{id}/download` | Download all successful PDFs as a ZIP |

### Submit a request

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "event_name": "Python Bootcamp 2026",
    "recipients": [
      {"name": "Amit Sharma", "email": "amit@example.com"},
      {"name": "Priya Singh", "email": "priya@example.com"},
      {"name": "", "email": "bad"}
    ]
  }'
```

Response (`202 Accepted`):

```json
{"job_id": 1, "total": 3, "invalid": 1, "status_url": "/jobs/1"}
```

On Windows PowerShell use `curl.exe` instead of `curl`, or use the `/docs` page.

### Check progress

```bash
curl http://127.0.0.1:8000/jobs/1
```

```json
{
  "job_id": 1,
  "status": "completed_with_errors",
  "total": 3, "succeeded": 2, "failed": 1, "pending": 0,
  "progress_percent": 100.0,
  "failures": [
    {"certificate_id": 3, "name": null, "email": "bad", "error": "name is required"}
  ]
}
```

Job statuses: `pending` -> `processing` -> `completed` or `completed_with_errors`.

### Retrieve certificates

```bash
curl http://127.0.0.1:8000/jobs/1/certificates              # list + download links
curl -O -J http://127.0.0.1:8000/certificates/1/download    # one PDF
curl -O -J http://127.0.0.1:8000/jobs/1/download            # all as ZIP
```

## Design decisions

**Background processing, not synchronous.** A request may contain thousands of
recipients; generating them inside the request would risk client timeouts. `POST /jobs`
validates, stores the job, and returns `202` immediately. A `ThreadPoolExecutor`
generates the PDFs and the client polls `GET /jobs/{id}`. A thread pool keeps the
project dependency-free; Celery/Redis would be overkill at this scale.

**Per-recipient validation, per-request rejection.** Problems with the request as a whole
(missing event name, empty list, more than 5000 recipients) return `422`. Problems with
a single recipient (missing name, invalid email, duplicate email in the same request)
are stored as `failed` rows with a reason, so valid recipients are still processed.

**Failure isolation.** Each certificate is generated inside its own `try/except`. A
failure is recorded on that certificate and the loop continues. A `finally` block
guarantees the job always reaches a final state. Partial files from a failed generation
are deleted.

**Progress derived from the database.** Counts are computed with SQL aggregates over
the `certificates` table rather than a separate counter, so they cannot drift out of
sync.

**Raw SQL with SQLite.** Two small tables (`jobs`, `certificates`) with a foreign key
and an index on `job_id`. Each call opens its own connection, which keeps SQLite safe
across worker threads. SQLAlchemy and Postgres would be natural next steps.

**Safe filenames.** PDFs are named by certificate id, never by recipient name, which
prevents path tricks and name collisions.

**Testability.** Paths come from the `CERTGEN_DB` and `CERTGEN_OUTPUT_DIR` environment
variables. Tests swap the background submit for a synchronous call, so they are
deterministic and need no sleeps.

## Known limitations and future improvements

- Jobs interrupted by a server restart stay in `processing`; a persistent queue
  (Celery/RQ) or a startup recovery step would fix this.
- The ZIP download is built in memory; very large jobs should stream it or build it on disk.
- Built-in Helvetica supports Latin text only; other scripts need a registered TTF font.
- No authentication, rate limiting, or file cleanup/expiry.
- SQLite suits a single-node deployment; use Postgres for multiple instances.