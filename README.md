# ResearchMind AI

ResearchMind AI is a small research-document workspace. You upload PDF or DOCX papers, the backend extracts their text and indexes them with [LightRAG](https://github.com/HKUDS/LightRAG), and you can then ask questions, search passages, compare selected papers, explore the knowledge graph and inspect AI-generated research gaps. Every answer and insight is labelled as AI-generated, and citations are mapped back to the documents that were actually retrieved.

This is a student-scale project. It runs locally with SQLite, local files and an OpenAI-compatible LLM/embedding endpoint that you configure yourself. It does not include paid services, fake metrics or pre-trained models of its own.

## What is implemented

- User registration and login with local password hashing and signed bearer tokens.
- Independent workspaces per account; every query checks both user and workspace ownership.
- Batch upload of PDF and DOCX files with extension, magic-byte and size validation.
- Real text extraction (`pypdf` for PDF, `python-docx` for DOCX), with PDF page markers such as `[Page 1]`.
- LightRAG indexing into per-workspace folders, with document status tracking (`uploaded`, `indexed`, `failed`) and manual re-indexing.
- Semantic search, research question answering, selected-paper comparison, research-gap analysis and a knowledge-graph summary read from LightRAG's GraphML file.
- Source citations mapped from LightRAG retrieval results to the exact uploaded document and, where available, page number.
- Conversation history, document listing/filtering, statistics and deletion.
- A TypeScript frontend with real loading, empty and error states.

## Technology stack

| Layer | Technology |
|---|---|
| Frontend | TypeScript, plain DOM rendering, CSS |
| Backend | Python, FastAPI, SQLite (`sqlite3`), Pydantic |
| RAG engine | LightRAG 1.5.8 (`lightrag-hku`) |
| Document parsing | `pypdf`, `python-docx` |
| LLM/embeddings | Any OpenAI-compatible HTTP endpoint you configure |
| Auth | PBKDF2 password hashes + HS256 JWT (`PyJWT`) |

## Architecture

```text
frontend/src/app.ts  ──fetch /api/*──>  backend/app/main.py
                                          │
                                          ├─ auth.py        identity from Bearer token
                                          ├─ db.py          users, workspaces, documents, chats
                                          ├─ documents.py   upload validation + text extraction
                                          └─ rag.py         per-workspace LightRAG engine
                                                             │
                                                             └─ vendor/LightRAG (MIT, upstream)
```

The browser never sees API keys. LLM and embedding keys are read only by the backend from environment variables. LightRAG stores its KV, vector, graph and document-status data under `data/storage/rag/<user>/<workspace>/`.

## Folder structure

```text
backend/app/       configuration, auth, SQLite, parsing, LightRAG integration, API
backend/tests/     offline workflow tests with deterministic test doubles
frontend/src/      TypeScript UI
frontend/dist/     compiled JavaScript (generated, ignored by Git)
vendor/LightRAG/   upstream source copy, MIT licensed, original LICENSE preserved
.env.example       environment template with placeholders only
requirements.txt   Python dependencies
```

## Installation

Requirements: Python 3.11+, Node 20+.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd frontend
npm install
cd ..
```

Copy `.env.example` to `.env` in the project root and fill in your own values. The backend also reads `backend/.env` if you prefer to keep backend configuration there.

```text
RESEARCHMIND_SECRET_KEY=replace-with-a-long-random-secret
LLM_BINDING_HOST=https://api.openai.com/v1
LLM_BINDING_API_KEY=
LLM_MODEL=gpt-4o-mini
EMBEDDING_BINDING_HOST=https://api.openai.com/v1
EMBEDDING_BINDING_API_KEY=
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
```

Do not commit `.env` or any real key. A secret is never required for the offline tests; those tests replace the LLM and embedding functions locally.

## Running locally

Backend (port 8000):

```powershell
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Frontend: the TypeScript build is served by the FastAPI app from `frontend/dist`, so run:

```powershell
cd frontend
npm run build
cd ..
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000`. `/api/health` reports configuration booleans only, never keys.

## Indexing and citations

1. An uploaded file is validated, stored under `data/storage/documents/<user>/<workspace>/<doc-id>/`, and converted to plain text.
2. The text is inserted into LightRAG with the document ID and the real stored file path.
3. During retrieval, LightRAG returns chunks and references, including `file_path` and `reference_id`.
4. The backend maps those paths back to database rows, so a citation only appears if the retrieved evidence came from that uploaded document.
5. In selected-paper comparison, evidence is filtered to the chosen document IDs before the LLM prompt is built; citations from unrelated papers are discarded.

Answers, contradictions and research gaps are generated by the configured LLM. They are exploratory findings, not verified facts. The UI labels them accordingly.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/register`, `/api/auth/login` | Local account access |
| GET | `/api/me` | Current account |
| POST/GET | `/api/workspaces` | Create/list workspaces |
| POST | `/api/workspaces/{id}/documents` | Upload one file |
| POST | `/api/workspaces/{id}/documents/batch` | Upload several files |
| GET/DELETE | `/api/workspaces/{id}/documents[/…]` | List/delete documents |
| POST | `/api/workspaces/{id}/documents/{doc}/reindex` | Re-run indexing |
| POST | `/api/workspaces/{id}/search` | Semantic passage search |
| POST | `/api/workspaces/{id}/ask` | Evidence-backed answer |
| POST | `/api/workspaces/{id}/compare` | Compare selected papers |
| POST | `/api/workspaces/{id}/insights` | Contradictions or research gaps |
| GET | `/api/workspaces/{id}/graph` | Entity/relationship summary |
| GET | `/api/workspaces/{id}/conversations` | Chat history |
| GET | `/api/health` | Liveness and configuration booleans |

## Testing

Backend tests use deterministic local stand-ins for the LLM and embeddings, so no key or network access is needed:

```powershell
$base = Join-Path $env:TEMP ("rm-" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Path $base | Out-Null
Push-Location .\backend
& ..\.venv\Scripts\python.exe -m pytest --basetemp $base
Pop-Location
```

Frontend:

```powershell
cd frontend
npm install --no-package-lock
npm run typecheck
npm run build
```

See the final verification report in the project handover for actual pass/fail counts and which checks remain blocked without credentials.

## Known limitations

- Live LLM and embedding calls are unverified unless you configure a real provider; offline tests do not prove provider behaviour or cost.
- LightRAG's default local storage is intended for small-scale use. A production deployment should use a persistent database/graph backend.
- Scanned PDFs without a text layer need OCR before upload.
- Research gaps and contradictions are AI-generated suggestions and require human verification.
- Uploads are capped by `RESEARCHMIND_MAX_UPLOAD_MB`.

## Future improvements

- Add OCR for scanned papers.
- Add persistent PostgreSQL/Neo4j storage for larger workspaces.
- Add streaming answers and exportable comparison reports.
- Add a proper frontend build tool and component test suite.

## Open-source credits

ResearchMind AI uses LightRAG as its retrieval engine. LightRAG is MIT licensed, copyright (c) 2025 LightRAG Team. The upstream source is included under `vendor/LightRAG/` with its original `LICENSE` file. See `THIRD_PARTY_NOTICES.md` for the full notice. Research code written for this project is original; no upstream code is claimed as independently authored.
