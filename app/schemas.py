from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import CertificateStatus, JobStatus


# ---------- Requests ----------
class RecipientIn(BaseModel):
    """Validated per recipient (not at the request level) so one bad row doesn't sink the job."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr


class JobCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event_name: str = Field(min_length=1, max_length=200)
    issuer_name: str = Field(min_length=1, max_length=200)
    issue_date: date = Field(default_factory=date.today)
    # Items are validated individually later; see services/jobs.py
    recipients: list[Any] = Field(min_length=1)


# ---------- Responses ----------
class FailureOut(BaseModel):
    row_index: int
    recipient_name: str | None
    recipient_email: str | None
    error: str | None


class JobOut(BaseModel):
    id: str
    status: JobStatus
    event_name: str
    issuer_name: str
    issue_date: date
    total: int
    succeeded: int
    failed: int
    pending: int
    progress_percent: float
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failures: list[FailureOut]
    status_url: str
    certificates_url: str
    download_all_url: str


class CertificateOut(BaseModel):
    id: str
    row_index: int
    recipient_name: str | None
    recipient_email: str | None
    status: CertificateStatus
    error: str | None
    download_url: str | None


class CertificatePage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]
