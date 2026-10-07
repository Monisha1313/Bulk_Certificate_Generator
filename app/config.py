import os
from pathlib import Path


class Settings:
    """Settings read from environment variables, with sensible defaults."""

    def __init__(self) -> None:
        self.database_url: str = os.getenv("DATABASE_URL", "sqlite:///./certificates.db")
        self.storage_dir: Path = Path(os.getenv("STORAGE_DIR", "./storage"))
        # Upper bound on recipients per request (protects the server).
        self.max_recipients: int = int(os.getenv("MAX_RECIPIENTS", "5000"))


settings = Settings()
