"""Optional semantic search over decisions and file changes (Chroma, `[vector]` extra).

Indexing is lazy: `cb search` adds any rows not yet embedded before querying, so
`cb log` and the git hook never pay Chroma's import or embedding cost. Everything
indexed is read from SQLite, where it is already sanitized.

Embeddings default to Chroma's built-in ONNX model, which is downloaded once
(about 80 MB) on first use.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from contextbridge.core.sanitizer import sanitize
from contextbridge.storage.db import Database, SearchHit

log = logging.getLogger("contextbridge")

COLLECTION = "contextbridge"
BATCH_SIZE = 100
MAX_DOCUMENT_CHARS = 2000


class VectorUnavailableError(Exception):
    """chromadb is not installed or the store cannot be opened."""


def _documents(db: Database) -> dict[str, tuple[str, dict[str, str]]]:
    """Every indexable row: id -> (text, metadata)."""
    docs: dict[str, tuple[str, dict[str, str]]] = {}
    for r in db.conn.execute("SELECT id, session_id, tag, content, created_at FROM decision_logs"):
        docs[r["id"]] = (
            f"[{r['tag']}] {r['content']}",
            {"kind": "decision", "session_id": r["session_id"], "timestamp": r["created_at"]},
        )
    for r in db.conn.execute(
        "SELECT id, session_id, file_path, symbols_modified, diff_snippet, timestamp "
        "FROM file_changes"
    ):
        text = f"{r['file_path']} {r['symbols_modified'] or ''}\n{r['diff_snippet'] or ''}"
        docs[r["id"]] = (
            text[:MAX_DOCUMENT_CHARS],
            {"kind": "file_change", "session_id": r["session_id"], "timestamp": r["timestamp"]},
        )
    return docs


class VectorStore:
    def __init__(self, path: Path, *, embedding_function: Any | None = None) -> None:
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError as exc:
            raise VectorUnavailableError(
                "chromadb is not installed (install contextbridge[vector])"
            ) from exc
        try:
            client = chromadb.PersistentClient(
                path=str(path), settings=Settings(anonymized_telemetry=False)
            )
            kwargs: dict[str, Any] = {"metadata": {"hnsw:space": "cosine"}}
            if embedding_function is not None:
                kwargs["embedding_function"] = embedding_function
            self.collection = client.get_or_create_collection(COLLECTION, **kwargs)
        except Exception as exc:
            raise VectorUnavailableError(f"cannot open vector store: {exc}") from exc

    def sync(self, db: Database) -> int:
        """Embed rows that are not indexed yet. Returns how many were added."""
        docs = _documents(db)
        existing = set(self.collection.get(include=[])["ids"])
        missing = [doc_id for doc_id in docs if doc_id not in existing]
        for start in range(0, len(missing), BATCH_SIZE):
            batch = missing[start : start + BATCH_SIZE]
            self.collection.upsert(
                ids=batch,
                documents=[docs[i][0] for i in batch],
                metadatas=[docs[i][1] for i in batch],
            )
        stale = existing - docs.keys()  # rows deleted from SQLite (e.g. `cb clear`)
        if stale:
            self.collection.delete(ids=sorted(stale))
        log.info("vector: indexed %d new row(s), removed %d", len(missing), len(stale))
        return len(missing)

    def search(self, query: str, *, limit: int = 10) -> list[SearchHit]:
        count = self.collection.count()
        if count == 0:
            return []
        result: Any = self.collection.query(
            query_texts=[sanitize(query)],
            n_results=min(limit, count),
            include=["documents", "metadatas", "distances"],
        )
        hits = []
        for doc_id, text, meta, distance in zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
            strict=True,
        ):
            hits.append(
                SearchHit(
                    kind=str(meta.get("kind", "")),
                    id=doc_id,
                    session_id=str(meta.get("session_id", "")),
                    text=text,
                    timestamp=str(meta.get("timestamp", "")),
                    score=1.0 - float(distance),
                )
            )
        return hits
