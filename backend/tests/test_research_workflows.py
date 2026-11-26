from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import database
from conftest import auth_headers, make_docx, make_pdf, register, wait_for_indexing


def test_batch_upload_semantic_search_and_paper_comparison(
    client: TestClient, isolated, tmp_path
) -> None:
    account = register(client, "researcher@example.com")
    headers = auth_headers(account["token"])
    first = make_docx(tmp_path / "paper-one.docx", "Study A reports a capacity increase after treatment.")
    second = make_docx(tmp_path / "paper-two.docx", "Study B reports no capacity increase after treatment.")
    third = make_docx(tmp_path / "paper-three.docx", "Study C reports an unrelated security finding.")

    response = client.post(
        f"/api/workspaces/{account['workspace_id']}/documents/batch",
        headers=headers,
        files=[
            ("files", ("paper-one.docx", first, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
            ("files", ("paper-two.docx", second, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
            ("files", ("paper-three.docx", third, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
        ],
    )
    assert response.status_code == 201, response.text
    uploaded = response.json()["uploaded"]
    assert len(uploaded) == 3
    assert all(
        item["status"] == "uploaded" and item["processing"] is True
        for item in uploaded
    )
    finished = wait_for_indexing(
        client, account["workspace_id"], headers, count=3
    )
    assert all(item["status"] == "indexed" for item in finished)

    search = client.post(
        f"/api/workspaces/{account['workspace_id']}/search",
        headers=headers,
        json={"query": "capacity after treatment", "limit": 5},
    )
    assert search.status_code == 200, search.text
    assert search.json()["results"]
    uploaded_ids = {item["id"] for item in uploaded}
    assert all(
        "excerpt" in item and item["citation"]["filename"]
        and item["citation"]["document_id"] in uploaded_ids
        for item in search.json()["results"]
    )

    selected_ids = {document["id"] for document in uploaded[:2]}
    comparison = client.post(
        f"/api/workspaces/{account['workspace_id']}/compare",
        headers=headers,
        json={
            "document_ids": sorted(selected_ids),
            "question": "Compare the capacity findings.",
        },
    )
    assert comparison.status_code == 200, comparison.text
    payload = comparison.json()
    assert payload["ai_generated"] is True
    assert payload["citations"]
    assert all(item["document_id"] in selected_ids for item in payload["citations"])

    insight = client.post(
        f"/api/workspaces/{account['workspace_id']}/insights",
        headers=headers,
        json={"kind": "gaps"},
    )
    assert insight.status_code == 200, insight.text
    assert insight.json()["ai_generated"] is True
    assert insight.json()["verification_required"] is True


def test_pdf_upload_indexing_and_page_citation(
    client: TestClient, isolated, tmp_path
) -> None:
    account = register(client, "pdf@example.com")
    headers = auth_headers(account["token"])
    pdf = make_pdf(tmp_path / "paper.pdf", "Study D reports a PDF finding.")

    uploaded = client.post(
        f"/api/workspaces/{account['workspace_id']}/documents",
        headers=headers,
        files={"file": ("paper.pdf", pdf, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["status"] == "uploaded"
    assert uploaded.json()["processing"] is True
    assert uploaded.json()["page_count"] == 1
    finished = wait_for_indexing(
        client, account["workspace_id"], headers, count=1
    )
    assert finished[0]["status"] == "indexed"
    assert finished[0]["id"] == uploaded.json()["id"]

    search = client.post(
        f"/api/workspaces/{account['workspace_id']}/search",
        headers=headers,
        json={"query": "PDF finding", "limit": 5},
    )
    assert search.status_code == 200, search.text
    results = search.json()["results"]
    assert any(
        item["citation"]["filename"] == "paper.pdf"
        and item["citation"]["page"] == 1
        and item["citation"]["document_id"] == uploaded.json()["id"]
        for item in results
    )

    graph = client.get(
        f"/api/workspaces/{account['workspace_id']}/graph", headers=headers
    )
    assert graph.status_code == 200, graph.text
    assert {"nodes", "edges"}.issubset(graph.json().keys())

    deleted = client.delete(
        f"/api/workspaces/{account['workspace_id']}/documents/{uploaded.json()['id']}",
        headers=headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert client.get(
        f"/api/workspaces/{account['workspace_id']}/documents", headers=headers
    ).json() == []


def test_workspace_isolation_between_accounts(client: TestClient, isolated, tmp_path) -> None:
    owner = register(client, "owner@example.com")
    other = register(client, "other@example.com")
    owner_headers = auth_headers(owner["token"])
    other_headers = auth_headers(other["token"])
    document = make_docx(tmp_path / "private.docx", "Private research finding.")
    other_document = make_docx(tmp_path / "private-two.docx", "Another private finding.")
    uploaded = client.post(
        f"/api/workspaces/{owner['workspace_id']}/documents",
        headers=owner_headers,
        files={"file": ("private.docx", document, "application/octet-stream")},
    )
    assert uploaded.status_code == 201, uploaded.text
    uploaded_two = client.post(
        f"/api/workspaces/{owner['workspace_id']}/documents",
        headers=owner_headers,
        files={"file": ("private-two.docx", other_document, "application/octet-stream")},
    )
    assert uploaded_two.status_code == 201, uploaded_two.text
    document_ids = [uploaded.json()["id"], uploaded_two.json()["id"]]

    wid = owner["workspace_id"]
    assert client.get(f"/api/workspaces/{wid}/documents", headers=other_headers).status_code == 404
    assert client.post(
        f"/api/workspaces/{wid}/search",
        headers=other_headers,
        json={"query": "private", "limit": 5},
    ).status_code == 404
    assert client.post(
        f"/api/workspaces/{wid}/compare",
        headers=other_headers,
        json={"document_ids": document_ids, "question": "Compare."},
    ).status_code == 404
    assert client.delete(
        f"/api/workspaces/{wid}/documents/{document_ids[0]}", headers=other_headers
    ).status_code == 404


def test_insights_work_with_empty_graph_and_real_citations(
    client: TestClient, isolated, tmp_path
) -> None:
    """The offline extractor creates no entities; chunk evidence must still work."""
    account = register(client, "gaps@example.com")
    headers = auth_headers(account["token"])
    batch = client.post(
        f"/api/workspaces/{account['workspace_id']}/documents/batch",
        headers=headers,
        files=[
            ("files", ("gap-one.docx", make_docx(tmp_path / "gap-one.docx", "Study E reports a mild side effect."), "application/octet-stream")),
            ("files", ("gap-two.docx", make_docx(tmp_path / "gap-two.docx", "Study F reports no follow-up duration."), "application/octet-stream")),
        ],
    )
    assert batch.status_code == 201, batch.text
    document_ids = {item["id"] for item in batch.json()["uploaded"]}
    wait_for_indexing(client, account["workspace_id"], headers, count=2)

    graph = client.get(f"/api/workspaces/{account['workspace_id']}/graph", headers=headers)
    assert graph.status_code == 200, graph.text
    assert graph.json()["nodes"] == []
    assert graph.json()["edges"] == []

    insight = client.post(
        f"/api/workspaces/{account['workspace_id']}/insights",
        headers=headers,
        json={"kind": "gaps"},
    )
    assert insight.status_code == 200, insight.text
    payload = insight.json()
    assert payload["ai_generated"] is True
    assert payload["verification_required"] is True
    assert payload["finding"].strip()
    assert payload["citations"]
    assert all(item["document_id"] in document_ids for item in payload["citations"])


def test_batch_upload_reports_invalid_documents_and_sanitizes_filenames(
    client: TestClient, isolated, tmp_path
) -> None:
    account = register(client, "uploads@example.com")
    headers = auth_headers(account["token"])
    valid = make_docx(tmp_path / "valid.docx", "A valid document with extractable research findings.")
    response = client.post(
        f"/api/workspaces/{account['workspace_id']}/documents/batch",
        headers=headers,
        files=[
            ("files", ("invalid.docx", b"PK\x03\x04not-a-zip", "application/octet-stream")),
            ("files", ("..\\..\\valid.docx", valid, "application/octet-stream")),
        ],
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert len(payload["uploaded"]) == 1
    assert payload["uploaded"][0]["filename"] == "valid.docx"
    assert payload["errors"][0]["filename"] == "invalid.docx"

    with database() as conn:
        stored = conn.execute(
            "select storage_path from documents where id = ?",
            (payload["uploaded"][0]["id"],),
        ).fetchone()
    assert stored is not None
    assert isolated.storage_dir in __import__("pathlib").Path(stored["storage_path"]).parents


def test_research_endpoints_require_authentication(client: TestClient) -> None:
    response = client.post(
        "/api/workspaces/not-a-workspace/search",
        json={"query": "source evidence"},
    )
    assert response.status_code == 401
