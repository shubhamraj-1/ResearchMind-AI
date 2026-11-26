"""Isolated test fixtures. The fake LLM/embed functions exist only in tests."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Iterator

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import auth, config, db, documents, main, rag
from app.config import settings


@pytest.fixture
def isolated(tmp_path, monkeypatch: pytest.MonkeyPatch):
    configured = replace(
        settings,
        secret_key="local-test-secret-not-for-production",
        database_path=tmp_path / "researchmind.db",
        storage_dir=tmp_path / "storage",
        max_upload_bytes=2_000_000,
    )
    for module in (config, auth, db, documents, rag, main):
        monkeypatch.setattr(module, "settings", configured)

    async def fake_llm(
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list[dict[str, str]] | None = None,
        **kwargs: object,
    ) -> str:
        lowered = prompt.lower()
        if "keyword" in lowered and "json" in lowered:
            return '{"high_level_keywords": ["capacity", "research"], "low_level_keywords": ["treatment", "findings"]}'
        if "contradict" in lowered:
            return "AI finding: the sources disagree on the reported capacity. [Page 1]"
        if "gap" in lowered:
            return "AI finding: long-term effects are not reported. [Page 1]"
        return "The supplied source reports a capacity increase. [Page 1]"

    async def fake_embed(texts: list[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), configured.embedding_dim), dtype=np.float32)
        for index, text in enumerate(texts):
            vectors[index, 0] = (len(text) % 97) + 1
        return vectors

    monkeypatch.setattr(rag, "llm_model_func", fake_llm)
    monkeypatch.setattr(rag, "embedding_func", fake_embed)
    rag._ENGINES.clear()
    rag._INITIALIZED.clear()
    rag._DIRS.clear()
    db.init_db()
    yield configured
    rag._ENGINES.clear()
    rag._INITIALIZED.clear()
    rag._DIRS.clear()


@pytest.fixture
def client(isolated) -> Iterator[TestClient]:
    with TestClient(main.app) as test_client:
        yield test_client


def register(client: TestClient, email: str, password: str = "strong-pass-123") -> dict:
    response = client.post("/api/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response.json()


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def wait_for_indexing(
    client: TestClient, workspace_id: str, headers: dict[str, str], count: int
) -> list[dict]:
    """Poll the real status endpoint until background indexing has finished."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        documents = client.get(f"/api/workspaces/{workspace_id}/documents", headers=headers).json()
        if len(documents) == count and all(
            item["status"] in {"indexed", "failed"} for item in documents
        ):
            return documents
        time.sleep(0.1)
    raise AssertionError("Documents did not finish indexing within 30 seconds")


def make_docx(path, text: str) -> bytes:
    from docx import Document

    document = Document()
    document.add_paragraph(text)
    document.save(str(path))
    return path.read_bytes()


def make_pdf(path, text: str) -> bytes:
    """Build a minimal one-page PDF with extractable text for tests."""
    operations = f"BT /F1 18 Tf 72 700 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(operations)).encode() + b" >>\nstream\n"
        + operations + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(bytes(output))
    return bytes(output)
