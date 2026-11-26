"""LightRAG integration: per-workspace engine, indexing, querying and graph access.

Third-party engine: LightRAG (MIT), copyright (c) 2025 LightRAG Team.
ResearchMind's own contribution is the workspace isolation, document lifecycle,
citation mapping and API layer in this module.
"""

from __future__ import annotations

import asyncio
import glob
import hashlib
import re
from pathlib import Path
from typing import Any

import aiohttp
import networkx as nx
import numpy as np
from lightrag import LightRAG, QueryParam
from lightrag.utils import EmbeddingFunc

from .config import settings

# Tests replace these two callables with deterministic local implementations.
# Production never uses a mock: both require configured endpoints and keys.
llm_model_func = None
embedding_func = None

_ENGINES: dict[tuple[str, str], LightRAG] = {}
_INITIALIZED: set[tuple[str, str]] = set()
_DIRS: dict[tuple[str, str], Path] = {}
_LOCK = asyncio.Lock()


async def _production_llm(
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list[dict[str, str]] | None = None,
    **kwargs: Any,
) -> str:
    if not settings.llm_api_key:
        raise RuntimeError("No LLM API key is configured (LLM_BINDING_API_KEY).")
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.extend(history_messages or [])
    messages.append({"role": "user", "content": prompt})
    payload = {"model": settings.llm_model, "messages": messages, "temperature": 0.2}
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{settings.llm_host}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        ) as response:
            if response.status >= 400:
                raise RuntimeError(f"LLM request failed with status {response.status}")
            body = await response.json()
    return str(body["choices"][0]["message"]["content"] or "")


async def _production_embedding(texts: list[str]) -> np.ndarray:
    if not settings.embedding_api_key:
        raise RuntimeError("No embedding API key is configured (EMBEDDING_BINDING_API_KEY).")
    payload = {"model": settings.embedding_model, "input": texts}
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{settings.embedding_host}/embeddings",
            headers={
                "Authorization": f"Bearer {settings.embedding_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        ) as response:
            if response.status >= 400:
                raise RuntimeError(f"Embedding request failed with status {response.status}")
            body = await response.json()
    vectors = [item["embedding"] for item in body["data"]]
    return np.asarray(vectors, dtype=np.float32)


async def get_engine(user_id: str, workspace_id: str) -> LightRAG:
    key = (user_id, workspace_id)
    async with _LOCK:
        if key not in _ENGINES:
            # LightRAG adds another directory named after `workspace`. Keeping
            # both segments short avoids Windows MAX_PATH errors in temp dirs
            # while still isolating every user/workspace by a unique hash.
            short_key = hashlib.sha256(
                f"{user_id}:{workspace_id}".encode("utf-8")
            ).hexdigest()[:16]
            working = settings.storage_dir / "rag" / short_key
            working.mkdir(parents=True, exist_ok=True)
            engine = LightRAG(
                working_dir=str(working),
                workspace=short_key,
                llm_model_func=llm_model_func or _production_llm,
                embedding_func=EmbeddingFunc(
                    embedding_dim=settings.embedding_dim,
                    func=embedding_func or _production_embedding,
                    model_name=settings.embedding_model,
                ),
            )
            _ENGINES[key] = engine
            _DIRS[key] = working
        engine = _ENGINES[key]
        if key not in _INITIALIZED:
            await engine.initialize_storages()
            _INITIALIZED.add(key)
    return engine


async def index_document(
    user_id: str, workspace_id: str, document_id: str, text: str, storage_path: str
) -> None:
    engine = await get_engine(user_id, workspace_id)
    # A stable citation reference avoids depending on local absolute paths,
    # Windows path casing or LightRAG's internal path normalisation.
    await engine.ainsert(
        text,
        ids=[document_id],
        file_paths=[f"doc:{document_id}"],
    )


async def delete_document(user_id: str, workspace_id: str, document_id: str) -> None:
    engine = await get_engine(user_id, workspace_id)
    await engine.adelete_by_doc_id(document_id)


_PAGE = re.compile(r"\[Page (\d+)\]")


async def ask(
    user_id: str,
    workspace_id: str,
    question: str,
    mode: str = "mix",
    history: list[dict[str, str]] | None = None,
    user_prompt: str | None = None,
) -> dict[str, Any]:
    engine = await get_engine(user_id, workspace_id)
    param = QueryParam(
        mode=mode,
        conversation_history=history or [],
        user_prompt=user_prompt,
        include_references=True,
    )
    evidence = await engine.aquery_data(question, param)
    if evidence.get("status") == "failure":
        raise RuntimeError(str(evidence.get("message") or "LightRAG retrieval failed."))
    answer = await engine.aquery(question, param)
    payload = evidence.get("data") or {}
    references = payload.get("references") or []
    chunks = payload.get("chunks") or []
    pages: dict[str, int] = {}
    for chunk in chunks:
        match = _PAGE.search(str(chunk.get("content", "")))
        ref = str(chunk.get("reference_id", ""))
        if match and ref and ref not in pages:
            pages[ref] = int(match.group(1))
    citations = [
        {
            "reference_id": str(ref.get("reference_id", "")),
            "file_path": str(ref.get("file_path", "")),
            "page": pages.get(str(ref.get("reference_id", ""))),
        }
        for ref in references
    ]
    return {
        "answer": str(answer),
        "citations": citations,
        "entities": len(payload.get("entities") or []),
        "relationships": len(payload.get("relationships") or []),
        "chunks": len(chunks),
        "mode": mode,
        "ai_generated": True,
    }


async def semantic_search(
    user_id: str,
    workspace_id: str,
    query: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    engine = await get_engine(user_id, workspace_id)
    evidence = await engine.aquery_data(
        query,
        QueryParam(mode="naive", chunk_top_k=limit, top_k=limit),
    )
    if evidence.get("status") == "failure":
        raise RuntimeError(str(evidence.get("message") or "LightRAG retrieval failed."))
    payload = evidence.get("data") or {}
    return [
        {
            "file_path": str(chunk.get("file_path", "")),
            "reference_id": str(chunk.get("reference_id", "")),
            "page": _page_number(str(chunk.get("content", ""))),
            "excerpt": str(chunk.get("content", ""))[:4000],
        }
        for chunk in payload.get("chunks") or []
    ]


async def compare_documents(
    user_id: str,
    workspace_id: str,
    question: str,
    selected_documents: list[dict[str, str]],
) -> dict[str, Any]:
    engine = await get_engine(user_id, workspace_id)
    selected_refs = {f"doc:{item['id']}" for item in selected_documents}
    retrieved: dict[str, dict[str, Any]] = {}
    for document in selected_documents:
        evidence = await engine.aquery_data(
            f"{question}\nFocus on the paper titled {document['filename']}.",
            QueryParam(mode="naive", chunk_top_k=4, top_k=4),
        )
        if evidence.get("status") == "failure":
            raise RuntimeError(str(evidence.get("message") or "LightRAG retrieval failed."))
        for chunk in (evidence.get("data") or {}).get("chunks") or []:
            reference = str(chunk.get("file_path", ""))
            if reference in selected_refs:
                retrieved.setdefault(str(chunk.get("chunk_id", reference)), chunk)

    filename_by_ref = {
        f"doc:{item['id']}": item["filename"] for item in selected_documents
    }
    relevant = [
        chunk
        for chunk in retrieved.values()
        if str(chunk.get("file_path", "")) in selected_refs
    ]
    citations = [
        {
            "file_path": str(chunk.get("file_path", "")),
            "reference_id": str(chunk.get("reference_id", "")),
            "page": _page_number(str(chunk.get("content", ""))),
        }
        for chunk in relevant
    ]
    if not relevant:
        return {
            "answer": "LightRAG did not retrieve evidence from the selected papers for this comparison. Try a more specific comparison question.",
            "citations": [],
            "ai_generated": False,
        }

    source_parts: list[str] = []
    for chunk in relevant:
        reference = str(chunk["file_path"])
        page = _page_number(str(chunk.get("content", "")))
        page_label = f" · page {page}" if page else ""
        source_parts.append(
            f"[{filename_by_ref[reference]}{page_label}]\n"
            f"{str(chunk.get('content', ''))[:4000]}"
        )
    sources = "\n\n".join(source_parts)
    prompt = (
        "Compare the selected research papers using only the retrieved source excerpts below. "
        "Distinguish agreements, differences in methods or findings, and limitations. "
        "Do not infer facts not present in the excerpts. Cite each substantive claim inline "
        "using the exact bracketed paper label shown in the evidence. If the excerpts do not "
        "support a comparison, say so.\n\n"
        f"Comparison question: {question}\n\nEvidence:\n{sources}"
    )
    llm = llm_model_func or _production_llm
    answer = await llm(
        prompt,
        system_prompt="You are a careful academic research assistant. Ground every claim in the supplied excerpts.",
    )
    return {"answer": answer, "citations": citations, "ai_generated": True}


def _page_number(content: str) -> int | None:
    match = _PAGE.search(content)
    return int(match.group(1)) if match else None


async def graph_summary(user_id: str, workspace_id: str, limit: int = 200) -> dict[str, Any]:
    """Read the GraphML file LightRAG persists for this workspace."""
    await get_engine(user_id, workspace_id)
    key = (user_id, workspace_id)
    directory = _DIRS.get(key)
    if directory is None:
        return {"nodes": [], "edges": []}
    files = sorted(glob.glob(str(directory / "**" / "graph_*.graphml"), recursive=True))
    if not files:
        return {"nodes": [], "edges": []}
    graph = nx.read_graphml(files[-1])
    nodes = [
        {"id": str(node), "label": str(graph.nodes[node].get("entity_name") or node)}
        for node in list(graph.nodes())[:limit]
    ]
    edges = [
        {
            "source": str(source),
            "target": str(target),
            "label": str(graph.edges[source, target].get("description") or ""),
        }
        for source, target in list(graph.edges())[:limit]
    ]
    return {"nodes": nodes, "edges": edges}
