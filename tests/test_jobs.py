import io
import zipfile
from datetime import date

import pytest

from app.config import settings
from app.database import SessionLocal
from app.schemas import JobCreate
from app.services import certificate as certificate_service
from app.services import jobs as job_service
from tests.conftest import make_payload

GOOD = [
    {"name": "Monisha R", "email": "monisha@example.com"},
    {"name": "Arjun Mehta", "email": "arjun@example.com"},
    {"name": "Sara Khan", "email": "sara@example.com"},
]


# ---------- Creating a generation job ----------
def test_create_job_returns_202_with_job_info(client):
    r = client.post("/jobs", json=make_payload(GOOD))
    assert r.status_code == 202
    body = r.json()
    assert body["id"]
    assert body["total"] == 3
    assert body["status_url"] == f"/jobs/{body['id']}"


def test_job_completes_all_valid(client):
    job_id = client.post("/jobs", json=make_payload(GOOD)).json()["id"]
    body = client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "completed"
    assert (body["succeeded"], body["failed"], body["pending"]) == (3, 0, 0)
    assert body["progress_percent"] == 100.0


def test_issue_date_defaults_to_today(client):
    payload = make_payload(GOOD)
    del payload["issue_date"]
    body = client.post("/jobs", json=payload).json()
    assert body["issue_date"] == date.today().isoformat()


# ---------- Input validation ----------
@pytest.mark.parametrize("bad", [
    {"recipients": []},                                  # empty list
    {"event_name": ""},                                  # empty event
    {"issuer_name": ""},                                 # empty issuer
    {"issue_date": "not-a-date"},                        # bad date
])
def test_request_level_validation_rejected(client, bad):
    payload = make_payload(GOOD)
    payload.update(bad)
    assert client.post("/jobs", json=payload).status_code == 422


def test_missing_recipients_field_rejected(client):
    payload = make_payload(GOOD)
    del payload["recipients"]
    assert client.post("/jobs", json=payload).status_code == 422


def test_too_many_recipients_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "max_recipients", 2)
    assert client.post("/jobs", json=make_payload(GOOD)).status_code == 422


def test_invalid_recipients_recorded_and_valid_ones_still_generated(client):
    recipients = [
        {"name": "Good One", "email": "good@example.com"},
        {"name": "", "email": "noname@example.com"},        # empty name
        {"name": "Bad Email", "email": "not-an-email"},     # invalid email
        {"email": "missing-name@example.com"},              # missing name
        "just a string",                                    # wrong type
        {"name": "Also Good", "email": "also@example.com"},
    ]
    body = client.get(f"/jobs/{client.post('/jobs', json=make_payload(recipients)).json()['id']}").json()
    assert body["status"] == "completed_with_errors"
    assert (body["succeeded"], body["failed"]) == (2, 4)
    assert [f["row_index"] for f in body["failures"]] == [1, 2, 3, 4]
    assert all("Validation error" in f["error"] for f in body["failures"])


def test_all_invalid_job_is_failed_immediately(client):
    r = client.post("/jobs", json=make_payload([{"name": "x", "email": "bad"}]))
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "failed"
    assert body["completed_at"] is not None


# ---------- Certificate generation ----------
def test_pdf_is_generated_on_disk(client):
    job_id = client.post("/jobs", json=make_payload(GOOD)).json()["id"]
    items = client.get(f"/jobs/{job_id}/certificates").json()["items"]
    files = list((settings.storage_dir / job_id).glob("*.pdf"))
    assert len(files) == 3
    assert all(f.read_bytes().startswith(b"%PDF") for f in files)
    assert {i["id"] for i in items} == {f.stem for f in files}


def test_render_certificate_unit_handles_very_long_name(tmp_path):
    out = tmp_path / "c.pdf"
    certificate_service.render_certificate(
        path=out, recipient_name="A" * 100, event_name="E" * 200,
        issuer_name="Org", issue_date=date(2026, 1, 1), certificate_id="abc",
    )
    assert out.read_bytes().startswith(b"%PDF")
    assert not out.with_suffix(".tmp").exists()  # atomic write left no temp file


def test_certificate_contains_recipient_name(tmp_path):
    out = tmp_path / "c.pdf"
    certificate_service.render_certificate(
        path=out, recipient_name="Monisha", event_name="Evt",
        issuer_name="Org", issue_date=date(2026, 1, 1), certificate_id="abc",
    )
    # ReportLab compresses streams by default, so check via the PDF title metadata instead.
    assert b"Certificate - Monisha" in out.read_bytes()


# ---------- Job status / progress ----------
def test_unknown_job_returns_404(client):
    assert client.get("/jobs/does-not-exist").status_code == 404


def test_progress_before_processing_is_pending_and_zero(client):
    """Create the job via the service only (no worker) to observe the 'queued' state."""
    with SessionLocal() as db:
        job = job_service.create_job(db, JobCreate(**make_payload(GOOD)))
        job_id = job.id
    body = client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "pending"
    assert (body["pending"], body["succeeded"], body["progress_percent"]) == (3, 0, 0.0)

    job_service.process_job(job_id)  # now run the worker
    body = client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "completed"
    assert body["progress_percent"] == 100.0
    assert body["started_at"] and body["completed_at"]


# ---------- Individual certificate failure ----------
def test_one_generation_failure_does_not_block_others(client, monkeypatch):
    original = certificate_service.render_certificate

    def flaky(**kwargs):
        if kwargs["recipient_name"] == "Boom":
            raise RuntimeError("disk exploded")
        return original(**kwargs)

    monkeypatch.setattr(certificate_service, "render_certificate", flaky)
    recipients = [
        {"name": "First", "email": "a@example.com"},
        {"name": "Boom", "email": "b@example.com"},
        {"name": "Third", "email": "c@example.com"},
    ]
    job_id = client.post("/jobs", json=make_payload(recipients)).json()["id"]
    body = client.get(f"/jobs/{job_id}").json()

    assert body["status"] == "completed_with_errors"
    assert (body["succeeded"], body["failed"]) == (2, 1)
    failure = body["failures"][0]
    assert failure["recipient_name"] == "Boom"
    assert "disk exploded" in failure["error"]
    assert len(list((settings.storage_dir / job_id).glob("*.pdf"))) == 2


def test_all_generation_failures_mark_job_failed(client, monkeypatch):
    def always_fail(**kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr(certificate_service, "render_certificate", always_fail)
    job_id = client.post("/jobs", json=make_payload(GOOD)).json()["id"]
    body = client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "failed"
    assert body["failed"] == 3


# ---------- Retrieving generated certificates ----------
def test_list_certificates_with_filter_and_pagination(client):
    recipients = GOOD + [{"name": "", "email": "x@example.com"}]
    job_id = client.post("/jobs", json=make_payload(recipients)).json()["id"]

    page = client.get(f"/jobs/{job_id}/certificates").json()
    assert page["total"] == 4

    ok = client.get(f"/jobs/{job_id}/certificates", params={"status": "succeeded"}).json()
    assert ok["total"] == 3 and all(i["download_url"] for i in ok["items"])

    bad = client.get(f"/jobs/{job_id}/certificates", params={"status": "failed"}).json()
    assert bad["total"] == 1 and bad["items"][0]["download_url"] is None

    p = client.get(f"/jobs/{job_id}/certificates", params={"limit": 2, "offset": 2}).json()
    assert [i["row_index"] for i in p["items"]] == [2, 3]


def test_download_single_certificate(client):
    job_id = client.post("/jobs", json=make_payload(GOOD)).json()["id"]
    item = client.get(f"/jobs/{job_id}/certificates").json()["items"][0]
    r = client.get(item["download_url"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_download_failed_or_unknown_certificate(client):
    job_id = client.post("/jobs", json=make_payload([GOOD[0], {"name": "", "email": "x@example.com"}])).json()["id"]
    failed = client.get(f"/jobs/{job_id}/certificates", params={"status": "failed"}).json()["items"][0]
    assert client.get(f"/certificates/{failed['id']}/download").status_code == 409
    assert client.get("/certificates/unknown/download").status_code == 404


def test_download_zip_contains_only_successful_certificates(client):
    recipients = GOOD + [{"name": "Bad", "email": "bad"}]
    job_id = client.post("/jobs", json=make_payload(recipients)).json()["id"]
    r = client.get(f"/jobs/{job_id}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert len(zf.namelist()) == 3
        assert zf.namelist()[0].startswith("0001_")


def test_zip_conflict_while_job_unfinished_and_404_when_empty(client):
    with SessionLocal() as db:
        pending_id = job_service.create_job(db, JobCreate(**make_payload(GOOD))).id
    assert client.get(f"/jobs/{pending_id}/download").status_code == 409

    failed_id = client.post("/jobs", json=make_payload([{"name": "", "email": "x"}])).json()["id"]
    assert client.get(f"/jobs/{failed_id}/download").status_code == 404
    assert client.get("/jobs/nope/download").status_code == 404


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
