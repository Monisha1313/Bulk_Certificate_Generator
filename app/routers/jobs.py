import os
import re
import tempfile
import zipfile

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.config import settings
from app.database import get_db
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import CertificateOut, CertificatePage, JobCreate, JobOut
from app.services import jobs as job_service

router = APIRouter()


def _get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


def _cert_out(c: Certificate) -> CertificateOut:
    return CertificateOut(
        id=c.id,
        row_index=c.row_index,
        recipient_name=c.recipient_name,
        recipient_email=c.recipient_email,
        status=c.status,
        error=c.error,
        download_url=f"/certificates/{c.id}/download" if c.status == CertificateStatus.SUCCEEDED else None,
    )


@router.post("/jobs", response_model=JobOut, status_code=202, summary="Submit a bulk generation job")
def create_job(payload: JobCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if len(payload.recipients) > settings.max_recipients:
        raise HTTPException(422, f"Too many recipients (max {settings.max_recipients} per request)")
    job = job_service.create_job(db, payload)
    if job.status == JobStatus.PENDING:  # at least one valid recipient
        background_tasks.add_task(job_service.process_job, job.id)
    return job_service.build_job_status(db, job)


@router.get("/jobs/{job_id}", response_model=JobOut, summary="Job status / progress")
def get_job(job_id: str, db: Session = Depends(get_db)):
    return job_service.build_job_status(db, _get_job_or_404(db, job_id))


@router.get("/jobs/{job_id}/certificates", response_model=CertificatePage, summary="List certificates of a job")
def list_certificates(
    job_id: str,
    status: CertificateStatus | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _get_job_or_404(db, job_id)
    query = select(Certificate).where(Certificate.job_id == job_id)
    if status:
        query = query.where(Certificate.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Certificate.row_index).limit(limit).offset(offset)).all()
    return CertificatePage(total=total, limit=limit, offset=offset, items=[_cert_out(c) for c in rows])


@router.get("/certificates/{certificate_id}/download", summary="Download one certificate (PDF)")
def download_certificate(certificate_id: str, db: Session = Depends(get_db)):
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(404, "Certificate not found")
    if cert.status != CertificateStatus.SUCCEEDED or not cert.file_path:
        raise HTTPException(409, f"Certificate is not available (status: {cert.status.value})")
    path = settings.storage_dir / cert.file_path
    if not path.is_file():
        raise HTTPException(404, "Certificate file missing from storage")
    return FileResponse(path, media_type="application/pdf", filename=f"certificate_{cert.row_index + 1}.pdf")


@router.get("/jobs/{job_id}/download", summary="Download all successful certificates as a ZIP")
def download_job_zip(job_id: str, db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    if job.status in (JobStatus.PENDING, JobStatus.PROCESSING):
        raise HTTPException(409, "Job is still running; check /jobs/{id} for progress")
    certs = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.SUCCEEDED)
        .order_by(Certificate.row_index)
    ).all()
    if not certs:
        raise HTTPException(404, "No certificates were generated for this job")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in certs:
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", c.recipient_name or "recipient")[:50]
            zf.write(settings.storage_dir / c.file_path, arcname=f"{c.row_index + 1:04d}_{safe}.pdf")
    return FileResponse(
        tmp.name,
        media_type="application/zip",
        filename=f"certificates_{job_id}.zip",
        background=BackgroundTask(os.unlink, tmp.name),  # clean up temp file after sending
    )
