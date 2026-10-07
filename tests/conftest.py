import os
import shutil
import tempfile

# Must be set BEFORE the app is imported so it uses an isolated DB + storage dir.
_TMP = tempfile.mkdtemp(prefix="certgen_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["STORAGE_DIR"] = f"{_TMP}/storage"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    shutil.rmtree(settings.storage_dir, ignore_errors=True)
    yield


@pytest.fixture
def client():
    # TestClient runs BackgroundTasks before returning the response,
    # so after POST /jobs the job is already finished in tests.
    with TestClient(app) as c:
        yield c


def make_payload(recipients=None, **overrides):
    payload = {
        "event_name": "AI Bootcamp 2026",
        "issuer_name": "AIVerse Club",
        "issue_date": "2026-10-01",
        "recipients": recipients
        if recipients is not None
        else [{"name": "Monisha R", "email": "monisha@example.com"}],
    }
    payload.update(overrides)
    return payload
