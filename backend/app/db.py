"""Small SQLite persistence layer for users, workspaces, documents and chats."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import settings

SCHEMA = """
create table if not exists users (
  id text primary key,
  email text not null unique,
  password_hash text not null,
  created_at text not null
);
create table if not exists workspaces (
  id text primary key,
  user_id text not null references users(id) on delete cascade,
  name text not null,
  created_at text not null
);
create table if not exists documents (
  id text primary key,
  user_id text not null references users(id) on delete cascade,
  workspace_id text not null references workspaces(id) on delete cascade,
  filename text not null,
  file_type text not null,
  size_bytes integer not null,
  page_count integer not null default 0,
  status text not null default 'uploaded',
  error text,
  storage_path text not null,
  created_at text not null,
  updated_at text not null
);
create index if not exists documents_workspace_idx
  on documents (workspace_id, created_at desc);
create table if not exists conversations (
  id text primary key,
  user_id text not null references users(id) on delete cascade,
  workspace_id text not null references workspaces(id) on delete cascade,
  title text not null,
  created_at text not null
);
create table if not exists messages (
  id integer primary key autoincrement,
  conversation_id text not null references conversations(id) on delete cascade,
  role text not null,
  content text not null,
  citations text,
  created_at text not null
);
create index if not exists messages_conversation_idx
  on messages (conversation_id, id);
"""


def connect() -> sqlite3.Connection:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.database_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with database() as conn:
        conn.executescript(SCHEMA)
