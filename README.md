# Bulk Certificate Generator API

A backend API that accepts a **list of recipients in one request**, generates a PDF certificate for each
(from one predefined template), tracks progress, and lets the client retrieve the results.

**Stack:** Python 3.10+, FastAPI, SQLAlchemy 2.0, SQLite (any SQL DB via `DATABASE_URL`), ReportLab (PDF), pytest.

## Project structure

```
certgen/
├── app/
│   ├── main.py              # FastAPI app, startup (creates tables + storage dir)
│   ├── config.py            # env-based settings
│   ├── database.py          # engine, session, Base
│   ├── models.py            # Job, Certificate tables + status enums
│   ├── schemas.py           # Pydantic request/response models
│   ├── routers/jobs.py      # HTTP endpoints
│   └── services/
│       ├── certificate.py   # PDF rendering (the single template)
│       └── jobs.py          # create job, validate rows, background processing, status
├── tests/
│   ├── conftest.py          # isolated temp DB + storage per test
│   └── test_jobs.py
├── sample_request.json
├── requirements.txt
└── pytest.ini
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload
```
- API: http://127.0.0.1:8000  ·  Interactive docs (Swagger): http://127.0.0.1:8000/docs
- Tables are created automatically on startup. PDFs are written to `./storage/<job_id>/<certificate_id>.pdf`.

Optional env vars: `DATABASE_URL` (default `sqlite:///./certificates.db`, e.g. `postgresql+psycopg://user:pw@host/db`),
`STORAGE_DIR` (default `./storage`), `MAX_RECIPIENTS` (default `5000`).

## Run tests

```bash
pytest
```
Tests use a temporary database and storage directory, so they never touch your real data.

## API usage

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/jobs` | Submit a bulk job (returns `202` immediately) |
| GET | `/jobs/{job_id}` | Status, counts, progress %, list of failures |
| GET | `/jobs/{job_id}/certificates?status=&limit=&offset=` | Paginated per-certificate list |
| GET | `/certificates/{certificate_id}/download` | Download one PDF |
| GET | `/jobs/{job_id}/download` | Download all successful PDFs as a ZIP |
| GET | `/health` | Health check |

### 1. Submit a request

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```
Response (`202 Accepted`), abbreviated:
```json
{ "id": "3f2c…", "status": "pending", "total": 4, "succeeded": 0, "failed": 2, "pending": 2,
  "progress_percent": 50.0, "status_url": "/jobs/3f2c…", "download_all_url": "/jobs/3f2c…/download", "failures": [ … ] }
```

### 2. Check progress
```bash
curl http://127.0.0.1:8000/jobs/<job_id>
```
Job `status` values: `pending` → `processing` → `completed` | `completed_with_errors` | `failed`.
`failures` lists each failed row with its `row_index`, the submitted name/email, and the reason.

### 3. Retrieve certificates
```bash
curl "http://127.0.0.1:8000/jobs/<job_id>/certificates?status=succeeded&limit=50"   # list + download URLs
curl -o cert.pdf http://127.0.0.1:8000/certificates/<certificate_id>/download        # one PDF
curl -o all.zip  http://127.0.0.1:8000/jobs/<job_id>/download                        # everything (ZIP)
```

## Design decisions

**Asynchronous processing with FastAPI `BackgroundTasks`.** `POST /jobs` stores the job + one row per
recipient, returns `202` immediately, and generation runs after the response. A request with thousands of
recipients therefore never blocks or times out the HTTP call; the client polls `GET /jobs/{id}`.
*Trade-off:* background tasks live in the web process, so a server crash mid-job leaves it `processing`
and there is no retry. In production I'd move `process_job` to a real queue (Celery/RQ/Arq + Redis) — the
function is already self-contained (takes only a `job_id`, opens its own DB session), so this is a small change.

**Per-row validation, not all-or-nothing.** Request-level fields (event, issuer, date, non-empty list,
max size) are validated by Pydantic → `422`. Each recipient (`name` 1-100 chars, valid `email`) is
validated *individually*; invalid rows are saved as `failed` with a readable reason, and valid rows
still get certificates. Every submitted row is accounted for, which matches the "one failure must not block
others" requirement.

**Failure isolation + live progress.** Each certificate is generated inside its own `try/except` and
committed on its own. One failure is recorded on that row only, and progress counts update as work proceeds.
Final status: all OK → `completed`; some failed → `completed_with_errors`; none OK → `failed`.

**Storage.** PDFs go on disk (path stored in DB), not in the DB: cheap, easy to serve, easy to swap for S3
later. Files are written to a temp name then renamed, so a half-written PDF is never served.

**Single template in code (ReportLab).** Name, event, issuer, date and a unique certificate ID are drawn
on a landscape A4 layout; long names shrink to fit.

**Database.** SQLAlchemy 2.0 models; SQLite by default for zero setup, switchable by `DATABASE_URL`.
Index on `(job_id, status)` for fast counts/filters. Tables are created on startup (Alembic would be used in production).

**Limits.** `MAX_RECIPIENTS` caps request size; list endpoint is paginated (max 500).

## Known limitations / possible improvements
- Move to a proper task queue with retries and crash recovery.
- Authentication / per-client job ownership.
- Duplicate detection (same email in one job), idempotency keys for `POST /jobs`.
- Email delivery of certificates, QR-code verification endpoint, S3 storage.

## Note on AI usage

I used an AI assistant (Claude) to help build this project, as the assignment allows. I ran the application and the test suite (all 24 tests pass), tried the API end to end through the interactive docs, and went through the code to understand how it works. I'm happy to explain any part of it or modify it during the interview.
