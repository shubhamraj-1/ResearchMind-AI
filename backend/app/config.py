"""Environment-driven configuration for ResearchMind AI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
# The template is at the repository root; the backend also accepts a local
# backend/.env override so either documented location works.
load_dotenv(PROJECT_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    secret_key: str
    database_path: Path
    storage_dir: Path
    max_upload_bytes: int
    llm_host: str
    llm_api_key: str
    llm_model: str
    embedding_host: str
    embedding_api_key: str
    embedding_model: str
    embedding_dim: int

    @property
    def ai_configured(self) -> bool:
        return bool(self.llm_api_key and self.embedding_api_key)


settings = Settings(
    secret_key=os.getenv("RESEARCHMIND_SECRET_KEY", ""),
    database_path=PROJECT_DIR / os.getenv("RESEARCHMIND_DB_PATH", "data/researchmind.db"),
    storage_dir=PROJECT_DIR / os.getenv("RESEARCHMIND_STORAGE_DIR", "data/storage"),
    max_upload_bytes=int(os.getenv("RESEARCHMIND_MAX_UPLOAD_MB", "10")) * 1024 * 1024,
    llm_host=os.getenv("LLM_BINDING_HOST", "https://api.openai.com/v1").rstrip("/"),
    llm_api_key=os.getenv("LLM_BINDING_API_KEY", ""),
    llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
    embedding_host=os.getenv("EMBEDDING_BINDING_HOST", "https://api.openai.com/v1").rstrip("/"),
    embedding_api_key=os.getenv("EMBEDDING_BINDING_API_KEY", ""),
    embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
    embedding_dim=int(os.getenv("EMBEDDING_DIM", "1536")),
)
