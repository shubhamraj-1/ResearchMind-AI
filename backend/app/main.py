"""ResearchMind AI API: authenticated research workspaces backed by LightRAG."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, db, documents, rag
from .auth import current_user, new_id
from .config import settings
from .db import database, init_db

logger = logging.getLogger(__name__)
MAX_BATCH_FILES = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _index_and_update(
    user_id: str, workspace_id: str, document_id: str, text: str, storage_path: str
) -> None:
    try:
        await rag.index_document(user_id, workspace_id, document_id, text, storage_path)
        next_status, error = "indexed", None
    except Exception as exc:  # keep the document usable and report the failure
        logger.exception("LightRAG indexing failed for document %s", document_id)
        next_status, error = "failed", "Indexing failed. Check the AI endpoint configuration and server logs."
    with database() as conn:
        conn.execute(
            "update documents set status = ?, error = ?, updated_at = ? where id = ? and user_id = ?",
            (next_status, error, _now(), document_id, user_id),
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    if len(settings.secret_key) < 32 or settings.secret_key == "replace-with-a-long-random-secret":
        raise RuntimeError("Set RESEARCHMIND_SECRET_KEY to a random value of at least 32 characters.")
    if settings.max_upload_bytes < 1 or settings.embedding_dim < 1:
        raise RuntimeError("Upload size and embedding dimension must be positive.")
    init_db()
    yield


app = FastAPI(title="ResearchMind AI API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Any, call_next: Any) -> Any:
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class AuthRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)


class WorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    mode: str = Field(default="mix", pattern="^(local|global|hybrid|naive|mix)$")
    conversation_id: str | None = None


class InsightRequest(BaseModel):
    kind: str = Field(pattern="^(contradictions|gaps)$")


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    limit: int = Field(default=8, ge=1, le=20)


class CompareRequest(BaseModel):
    document_ids: list[str] = Field(min_length=2, max_length=4)
    question: str = Field(min_length=2, max_length=2000)


def _workspace(wid: str, user_id: str) -> Any:
    with database() as conn:
        row = conn.execute(
            "select id, name from workspaces where id = ? and user_id = ?", (wid, user_id)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return row


def _document(doc_id: str, wid: str, user_id: str) -> Any:
    with database() as conn:
        row = conn.execute(
            "select * from documents where id = ? and workspace_id = ? and user_id = ?",
            (doc_id, wid, user_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return row


def _doc_json(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "filename": row["filename"],
        "file_type": row["file_type"],
        "size_bytes": row["size_bytes"],
        "page_count": row["page_count"],
        "status": row["status"],
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _mapped_citations(
    items: list[dict[str, Any]], workspace_id: str, user_id: str
) -> list[dict[str, Any]]:
    citations = []
    with database() as conn:
        for item in items:
            reference = str(item.get("file_path", ""))
            row = None
            if reference.startswith("doc:"):
                row = conn.execute(
                    "select id, filename from documents where id = ? and workspace_id = ? and user_id = ?",
                    (reference[4:], workspace_id, user_id),
                ).fetchone()
            else:
                # Older indexes may contain real paths; verify a normalised
                # match instead of trusting an arbitrary string from retrieval.
                for candidate in conn.execute(
                    "select id, filename, storage_path from documents "
                    "where workspace_id = ? and user_id = ?",
                    (workspace_id, user_id),
                ).fetchall():
                    if os.path.normcase(os.path.abspath(candidate["storage_path"])) == (
                        os.path.normcase(os.path.abspath(reference))
                    ):
                        row = candidate
                        break
            if row is not None:
                citations.append(
                    {
                        "document_id": row["id"],
                        "filename": row["filename"],
                        "page": item.get("page"),
                        "reference_id": str(item.get("reference_id", "")),
                    }
                )
    return citations


async def _read_limited_upload(file: UploadFile) -> bytes:
    parts: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > settings.max_upload_bytes:
            raise ValueError("The file exceeds the upload size limit.")
        parts.append(chunk)
    return b"".join(parts)


async def _save_document(
    wid: str,
    user_id: str,
    file: UploadFile,
    background: BackgroundTasks,
) -> dict[str, Any]:
    try:
        data = await _read_limited_upload(file)
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        suffix = documents.validate_upload(filename, data)
        if not filename or filename in {".", ".."}:
            raise ValueError("A valid filename is required.")
    except ValueError:
        raise
    except Exception as exc:
        logger.error("Could not read uploaded document (%s)", type(exc).__name__)
        raise HTTPException(status_code=400, detail="Could not read the uploaded file.") from exc

    doc_id, now = new_id(), _now()
    directory = settings.storage_dir / "documents" / user_id / wid / doc_id
    # LightRAG deduplicates by stored filename, so each document needs a unique
    # file name even when two papers have the same original filename.
    path = directory / f"{doc_id}{suffix}"
    try:
        directory.mkdir(parents=True, exist_ok=False)
        path.write_bytes(data)
        text, pages = await asyncio.to_thread(documents.extract_text, path, suffix)
        if not text.strip():
            raise ValueError("No readable text was found. Scanned documents need OCR before upload.")
        (directory / "extracted.txt").write_text(text, encoding="utf-8")
        with database() as conn:
            conn.execute(
                """insert into documents
                   (id, user_id, workspace_id, filename, file_type, size_bytes, page_count,
                    status, storage_path, created_at, updated_at)
                   values (?, ?, ?, ?, ?, ?, ?, 'uploaded', ?, ?, ?)""",
                (doc_id, user_id, wid, filename, suffix, len(data), pages, str(path), now, now),
            )
    except ValueError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except (OSError, sqlite3.Error) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        logger.error("Could not store uploaded document (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Could not safely store the uploaded document.") from exc
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        logger.error("Could not extract uploaded document (%s)", type(exc).__name__)
        raise HTTPException(status_code=422, detail="Could not extract text from the uploaded document.") from exc

    # Indexing runs after the response so large uploads do not hold the request.
    # The returned status is "uploaded" with processing=true until the worker
    # updates it to "indexed" or "failed" in the database.
    background.add_task(_index_and_update, user_id, wid, doc_id, text, str(path))
    result = _doc_json(_document(doc_id, wid, user_id))
    result["processing"] = True
    return result


@app.get("/api/health")
def health() -> dict[str, Any]:
    with database() as conn:
        users = conn.execute("select count(*) from users").fetchone()[0]
        docs = conn.execute("select count(*) from documents").fetchone()[0]
    return {
        "status": "ok",
        "service": "researchmind-ai",
        "ai_configured": settings.ai_configured,
        "users": users,
        "documents": docs,
    }


@app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
def register(body: AuthRequest) -> dict[str, Any]:
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=422, detail="A valid email is required.")
    uid, wid, now = new_id(), new_id(), _now()
    try:
        with database() as conn:
            if conn.execute("select 1 from users where email = ?", (email,)).fetchone():
                raise HTTPException(status_code=409, detail="Email already registered.")
            conn.execute(
                "insert into users (id, email, password_hash, created_at) values (?, ?, ?, ?)",
                (uid, email, auth.hash_password(body.password), now),
            )
            conn.execute(
                "insert into workspaces (id, user_id, name, created_at) values (?, ?, ?, ?)",
                (wid, uid, "My research", now),
            )
    except HTTPException:
        raise
    return {"token": auth.create_token(uid), "user_id": uid, "email": email, "workspace_id": wid}


@app.post("/api/auth/login")
def login(body: AuthRequest) -> dict[str, Any]:
    with database() as conn:
        row = conn.execute("select * from users where email = ?", (body.email.strip().lower(),)).fetchone()
    if row is None or not auth.verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    with database() as conn:
        workspace = conn.execute(
            "select id from workspaces where user_id = ? order by created_at limit 1", (row["id"],)
        ).fetchone()
    return {
        "token": auth.create_token(row["id"]),
        "user_id": row["id"],
        "email": row["email"],
        "workspace_id": workspace["id"] if workspace else None,
    }


@app.get("/api/me")
def me(user_id: str = Depends(current_user)) -> dict[str, Any]:
    with database() as conn:
        rows = conn.execute("select id, email from users where id = ?", (user_id,)).fetchall()
    if not rows:
        raise HTTPException(status_code=401, detail="Account not found.")
    return {"user_id": rows[0]["id"], "email": rows[0]["email"]}


@app.post("/api/workspaces", status_code=status.HTTP_201_CREATED)
def create_workspace(body: WorkspaceRequest, user_id: str = Depends(current_user)) -> dict[str, Any]:
    wid, now = new_id(), _now()
    with database() as conn:
        conn.execute(
            "insert into workspaces (id, user_id, name, created_at) values (?, ?, ?, ?)",
            (wid, user_id, body.name.strip(), now),
        )
    return {"id": wid, "name": body.name.strip(), "created_at": now}


@app.get("/api/workspaces")
def list_workspaces(user_id: str = Depends(current_user)) -> list[dict[str, Any]]:
    with database() as conn:
        rows = conn.execute(
            "select id, name, created_at from workspaces where user_id = ? order by created_at",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/workspaces/{wid}/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    wid: str,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: str = Depends(current_user),
) -> dict[str, Any]:
    _workspace(wid, user_id)
    try:
        return await _save_document(wid, user_id, file, background)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()


@app.post("/api/workspaces/{wid}/documents/batch", status_code=status.HTTP_201_CREATED)
async def upload_documents(
    wid: str,
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    user_id: str = Depends(current_user),
) -> dict[str, Any]:
    _workspace(wid, user_id)
    if not files or len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"Upload between 1 and {MAX_BATCH_FILES} files at a time.",
        )
    uploaded: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for file in files:
        try:
            uploaded.append(await _save_document(wid, user_id, file, background))
        except ValueError as exc:
            errors.append({"filename": file.filename or "unknown file", "error": str(exc)})
        finally:
            await file.close()
    return {"uploaded": uploaded, "errors": errors}


@app.post("/api/workspaces/{wid}/documents/{doc_id}/reindex")
async def reindex_document(
    wid: str, doc_id: str, user_id: str = Depends(current_user)
) -> dict[str, Any]:
    row = _document(doc_id, wid, user_id)
    text = (Path(row["storage_path"]).parent / "extracted.txt").read_text(encoding="utf-8")
    await _index_and_update(user_id, wid, doc_id, text, row["storage_path"])
    return _doc_json(_document(doc_id, wid, user_id))


@app.get("/api/workspaces/{wid}/documents")
def list_documents(
    wid: str,
    search: str = "",
    status_filter: str = "",
    user_id: str = Depends(current_user),
) -> list[dict[str, Any]]:
    _workspace(wid, user_id)
    sql = "select * from documents where workspace_id = ? and user_id = ?"
    params: list[Any] = [wid, user_id]
    if search:
        sql += " and lower(filename) like ?"
        params.append(f"%{search.lower()}%")
    if status_filter:
        sql += " and status = ?"
        params.append(status_filter)
    sql += " order by created_at desc"
    with database() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_doc_json(row) for row in rows]


@app.delete("/api/workspaces/{wid}/documents/{doc_id}")
async def remove_document(
    wid: str, doc_id: str, user_id: str = Depends(current_user)
) -> dict[str, str]:
    row = _document(doc_id, wid, user_id)
    try:
        await rag.delete_document(user_id, wid, doc_id)
    except Exception as exc:
        logger.error("LightRAG deletion failed for document %s (%s)", doc_id, type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Could not remove the document from the research index. The document was not deleted.",
        ) from exc
    with database() as conn:
        conn.execute("delete from documents where id = ? and user_id = ?", (doc_id, user_id))
    path = Path(row["storage_path"]).parent
    for child in path.iterdir():
        child.unlink(missing_ok=True)
    path.rmdir()
    return {"status": "deleted"}


@app.get("/api/workspaces/{wid}/stats")
def workspace_stats(wid: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
    _workspace(wid, user_id)
    with database() as conn:
        rows = conn.execute(
            "select status, file_type, size_bytes, page_count from documents "
            "where workspace_id = ? and user_id = ?",
            (wid, user_id),
        ).fetchall()
        conversations = conn.execute(
            "select count(*) from conversations where workspace_id = ? and user_id = ?",
            (wid, user_id),
        ).fetchone()[0]
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
        by_type[row["file_type"]] = by_type.get(row["file_type"], 0) + 1
    return {
        "documents": len(rows),
        "indexed": by_status.get("indexed", 0),
        "failed": by_status.get("failed", 0),
        "pages": sum(row["page_count"] or 0 for row in rows),
        "bytes": sum(row["size_bytes"] or 0 for row in rows),
        "by_type": by_type,
        "conversations": conversations,
    }


@app.post("/api/workspaces/{wid}/ask")
async def ask_question(
    wid: str, body: AskRequest, user_id: str = Depends(current_user)
) -> dict[str, Any]:
    _workspace(wid, user_id)
    with database() as conn:
        indexed = conn.execute(
            "select count(*) from documents where workspace_id = ? and user_id = ? and status = 'indexed'",
            (wid, user_id),
        ).fetchone()[0]
        if indexed == 0:
            raise HTTPException(status_code=409, detail="Index at least one document first.")

        conversation_id = body.conversation_id
        if conversation_id:
            conversation = conn.execute(
                "select id from conversations where id = ? and workspace_id = ? and user_id = ?",
                (conversation_id, wid, user_id),
            ).fetchone()
            if conversation is None:
                raise HTTPException(status_code=404, detail="Conversation not found.")
            history = [
                {"role": row["role"], "content": row["content"]}
                for row in conn.execute(
                    "select role, content from messages where conversation_id = ? order by id",
                    (conversation_id,),
                ).fetchall()
            ]
        else:
            conversation_id = new_id()
            conn.execute(
                "insert into conversations (id, user_id, workspace_id, title, created_at) values (?, ?, ?, ?, ?)",
                (conversation_id, user_id, wid, body.question[:80], _now()),
            )
            history = []

    try:
        result = await rag.ask(user_id, wid, body.question, body.mode, history)
    except Exception as exc:
        logger.error("LightRAG query failed in workspace %s (%s)", wid, type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="The research engine failed. Check the AI endpoint configuration and server logs.",
        ) from exc

    citations = _mapped_citations(result["citations"], wid, user_id)
    with database() as conn:
        now = _now()
        conn.execute(
            "insert into messages (conversation_id, role, content, citations, created_at) values (?, 'user', ?, null, ?)",
            (conversation_id, body.question, now),
        )
        conn.execute(
            "insert into messages (conversation_id, role, content, citations, created_at) values (?, 'assistant', ?, ?, ?)",
            (conversation_id, result["answer"], json.dumps(citations), _now()),
        )
    result["conversation_id"] = conversation_id
    result["citations"] = citations
    return result


@app.post("/api/workspaces/{wid}/search")
async def search_documents(
    wid: str, body: SearchRequest, user_id: str = Depends(current_user)
) -> dict[str, Any]:
    _workspace(wid, user_id)
    with database() as conn:
        indexed = conn.execute(
            "select count(*) from documents where workspace_id = ? and user_id = ? and status = 'indexed'",
            (wid, user_id),
        ).fetchone()[0]
    if indexed == 0:
        raise HTTPException(status_code=409, detail="Index at least one document first.")
    try:
        results = await rag.semantic_search(user_id, wid, body.query, body.limit)
    except Exception as exc:
        logger.error("LightRAG semantic search failed in workspace %s (%s)", wid, type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Semantic search failed. Check the AI endpoint configuration and server logs.",
        ) from exc
    citations = _mapped_citations(results, wid, user_id)
    by_reference = {(item["reference_id"], item["page"]): item for item in citations}
    mapped = []
    for item in results:
        citation = by_reference.get((item["reference_id"], item["page"]))
        if citation:
            mapped.append({"citation": citation, "excerpt": item["excerpt"]})
    return {"query": body.query, "results": mapped}


@app.post("/api/workspaces/{wid}/compare")
async def compare_papers(
    wid: str, body: CompareRequest, user_id: str = Depends(current_user)
) -> dict[str, Any]:
    _workspace(wid, user_id)
    if len(set(body.document_ids)) != len(body.document_ids):
        raise HTTPException(status_code=422, detail="Select each paper only once.")
    placeholders = ",".join("?" for _ in body.document_ids)
    with database() as conn:
        rows = conn.execute(
            f"select id, filename, storage_path from documents "
            f"where workspace_id = ? and user_id = ? and status = 'indexed' "
            f"and id in ({placeholders})",
            [wid, user_id, *body.document_ids],
        ).fetchall()
    if len(rows) != len(body.document_ids):
        raise HTTPException(
            status_code=409,
            detail="Every selected paper must be indexed in this workspace.",
        )
    try:
        result = await rag.compare_documents(
            user_id,
            wid,
            body.question,
            [dict(row) for row in rows],
        )
    except Exception as exc:
        logger.error("LightRAG paper comparison failed in workspace %s (%s)", wid, type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Paper comparison failed. Check the AI endpoint configuration and server logs.",
        ) from exc
    result["citations"] = _mapped_citations(result["citations"], wid, user_id)
    return result


@app.post("/api/workspaces/{wid}/insights")
async def create_insight(
    wid: str, body: InsightRequest, user_id: str = Depends(current_user)
) -> dict[str, Any]:
    _workspace(wid, user_id)
    with database() as conn:
        indexed = conn.execute(
            "select count(*) from documents where workspace_id = ? and user_id = ? and status = 'indexed'",
            (wid, user_id),
        ).fetchone()[0]
    if indexed < 2:
        raise HTTPException(status_code=409, detail="Index at least two documents first.")
    focus = (
        "Identify claims that conflict across these documents. Quote both sides briefly."
        if body.kind == "contradictions"
        else "Identify unresolved questions and missing evidence across these documents."
    )
    try:
        result = await rag.ask(user_id, wid, focus, mode="mix")
    except Exception as exc:
        logger.error("LightRAG insight generation failed in workspace %s (%s)", wid, type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Research analysis failed. Check the AI endpoint configuration and server logs.",
        ) from exc
    return {
        "kind": body.kind,
        "finding": result["answer"],
        "ai_generated": True,
        "verification_required": True,
        "citations": _mapped_citations(result["citations"], wid, user_id),
    }


@app.get("/api/workspaces/{wid}/graph")
async def knowledge_graph(
    wid: str,
    limit: int = Query(default=120, ge=1, le=250),
    user_id: str = Depends(current_user),
) -> dict[str, Any]:
    _workspace(wid, user_id)
    return await rag.graph_summary(user_id, wid, limit)


@app.get("/api/workspaces/{wid}/conversations")
def list_conversations(wid: str, user_id: str = Depends(current_user)) -> list[dict[str, Any]]:
    _workspace(wid, user_id)
    with database() as conn:
        rows = conn.execute(
            "select id, title, created_at from conversations where workspace_id = ? and user_id = ? order by created_at desc",
            (wid, user_id),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/workspaces/{wid}/conversations/{cid}")
def get_conversation(
    wid: str, cid: str, user_id: str = Depends(current_user)
) -> list[dict[str, Any]]:
    with database() as conn:
        conversation = conn.execute(
            "select id from conversations where id = ? and workspace_id = ? and user_id = ?",
            (cid, wid, user_id),
        ).fetchone()
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        rows = conn.execute(
            "select role, content, citations, created_at from messages where conversation_id = ? order by id",
            (cid,),
        ).fetchall()
    return [dict(row) for row in rows]


_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")
