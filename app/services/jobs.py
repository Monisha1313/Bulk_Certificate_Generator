"""Business logic: creating jobs, validating recipients, processing, building status."""
import logging
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import database
from app.config import settings
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import JobCreate, RecipientIn
from app.services import certificate as certificate_service

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _format_validation_error(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'recipient'}: {e['msg']}" for e in exc.errors()
    )


def create_job(db: Session, payload: JobCreate) -> Job:
    """Persist the job and one Certificate row per submitted recipient.

    Invalid recipients are stored immediately as FAILED (with the reason) so the
    client sees every row accounted for; valid ones are PENDING for the worker.
    """
    job = Job(
        event_name=payload.event_name,
        issuer_name=payload.issuer_name,
        issue_date=payload.issue_date,
        total_count=len(payload.recipients),
    )
    db.add(job)
    db.flush()  # get job.id

    valid_count = 0
    for idx, raw in enumerate(payload.recipients):
        cert = Certificate(job_id=job.id, row_index=idx, raw_data=raw if isinstance(raw, dict) else {"value": str(raw)})
        try:
            recipient = RecipientIn.model_validate(raw)
            cert.recipient_name = recipient.name
            cert.recipient_email = str(recipient.email)
            valid_count += 1
        except ValidationError as exc:
            cert.status = CertificateStatus.FAILED
            cert.error = f"Validation error - {_format_validation_error(exc)}"
            if isinstance(raw, dict):
                cert.recipient_name = str(raw.get("name"))[:200] if raw.get("name") is not None else None
                cert.recipient_email = str(raw.get("email"))[:320] if raw.get("email") is not None else None
        db.add(cert)

    if valid_count == 0:  # nothing to process
        job.status = JobStatus.FAILED
        job.completed_at = _now()

    db.commit()
    return job


def process_job(job_id: str) -> None:
    """Generate all pending certificates of a job. Runs in the background.

    Each certificate is isolated in its own try/except and committed individually,
    so (a) one failure never blocks the others and (b) progress is visible live.
    """
    db = database.SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = JobStatus.PROCESSING
        job.started_at = _now()
        db.commit()

        pending_ids = db.scalars(
            select(Certificate.id)
            .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING)
            .order_by(Certificate.row_index)
        ).all()

        for cert_id in pending_ids:
            cert = db.get(Certificate, cert_id)
            try:
                rel_path = f"{job_id}/{cert.id}.pdf"
                certificate_service.render_certificate(
                    path=settings.storage_dir / rel_path,
                    recipient_name=cert.recipient_name,
                    event_name=job.event_name,
                    issuer_name=job.issuer_name,
                    issue_date=job.issue_date,
                    certificate_id=cert.id,
                )
                cert.status = CertificateStatus.SUCCEEDED
                cert.file_path = rel_path
                cert.generated_at = _now()
            except Exception as exc:  # noqa: BLE001 - isolate any per-certificate failure
                logger.exception("Certificate %s failed", cert_id)
                cert.status = CertificateStatus.FAILED
                cert.error = f"Generation error - {type(exc).__name__}: {exc}"
            db.commit()

        _finalize(db, job)
    except Exception:  # noqa: BLE001 - unexpected job-level failure
        logger.exception("Job %s crashed", job_id)
        db.rollback()
        job = db.get(Job, job_id)
        if job is not None:
            job.status = JobStatus.FAILED
            job.completed_at = _now()
            db.commit()
    finally:
        db.close()


def _finalize(db: Session, job: Job) -> None:
    counts = _status_counts(db, job.id)
    succeeded = counts.get(CertificateStatus.SUCCEEDED, 0)
    if succeeded == job.total_count:
        job.status = JobStatus.COMPLETED
    elif succeeded == 0:
        job.status = JobStatus.FAILED
    else:
        job.status = JobStatus.COMPLETED_WITH_ERRORS
    job.completed_at = _now()
    db.commit()


def _status_counts(db: Session, job_id: str) -> dict:
    rows = db.execute(
        select(Certificate.status, func.count()).where(Certificate.job_id == job_id).group_by(Certificate.status)
    ).all()
    return {status: count for status, count in rows}


def build_job_status(db: Session, job: Job) -> dict:
    counts = _status_counts(db, job.id)
    succeeded = counts.get(CertificateStatus.SUCCEEDED, 0)
    failed = counts.get(CertificateStatus.FAILED, 0)
    pending = counts.get(CertificateStatus.PENDING, 0)
    failures = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.FAILED)
        .order_by(Certificate.row_index)
    ).all()
    return {
        "id": job.id,
        "status": job.status,
        "event_name": job.event_name,
        "issuer_name": job.issuer_name,
        "issue_date": job.issue_date,
        "total": job.total_count,
        "succeeded": succeeded,
        "failed": failed,
        "pending": pending,
        "progress_percent": round((succeeded + failed) / job.total_count * 100, 2) if job.total_count else 100.0,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "failures": [
            {"row_index": c.row_index, "recipient_name": c.recipient_name,
             "recipient_email": c.recipient_email, "error": c.error}
            for c in failures
        ],
        "status_url": f"/jobs/{job.id}",
        "certificates_url": f"/jobs/{job.id}/certificates",
        "download_all_url": f"/jobs/{job.id}/download",
    }
