"""Memory Vector DB helpers (Phase 6).

Provides embedding generation (Ollama if a model is configured, deterministic
hashing fallback otherwise) and cosine-similarity retrieval over the
``memories`` table populated by the cataloger after each scene.
"""
from __future__ import annotations

import array
import hashlib
import math
import os
import re
from typing import Iterable

import httpx

from .database import get_memories, add_memory, get_connection

OLLAMA_BASE = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EMBED_URL = f"{OLLAMA_BASE.rstrip('/')}/api/embeddings"

# ── Embedding ────────────────────────────────────────────────────────────────

_HASH_DIM = 256  # fallback embedding dimension


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^\wäöüÄÖÜß]+", (text or "").lower()) if t]


def _hash_embed(text: str) -> list[float]:
    """Deterministic char-bigram + word-hash bag-of-features embedding.
    Cosine-comparable, 256-d, no model needed."""
    vec = [0.0] * _HASH_DIM
    toks = _tokenize(text)
    for w in toks:
        h = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16)
        idx = h % _HASH_DIM
        sign = 1.0 if (h >> 16) & 1 else -1.0
        vec[idx] += sign
        # bigrams for partial matches
        for i in range(len(w) - 1):
            bg = w[i:i + 2]
            hh = int(hashlib.md5(bg.encode("utf-8")).hexdigest(), 16)
            vec[hh % _HASH_DIM] += 0.3 * (1.0 if (hh >> 8) & 1 else -1.0)
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / n for v in vec]


async def embed_text(text: str, model: str = "") -> tuple[list[float], str]:
    """Return (embedding, used_model). Empty model → hashing fallback."""
    text = (text or "").strip()
    if not text:
        return [], ""
    if model:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(EMBED_URL, json={"model": model, "prompt": text})
                resp.raise_for_status()
                data = resp.json()
                emb = data.get("embedding") or []
                if isinstance(emb, list) and emb:
                    n = math.sqrt(sum(float(v) * float(v) for v in emb)) or 1.0
                    return [float(v) / n for v in emb], model
        except Exception:
            pass
    return _hash_embed(text), "hash:v1"


# ── BLOB serialization ───────────────────────────────────────────────────────

def vec_to_blob(vec: list[float]) -> bytes:
    return array.array("f", vec).tobytes()


def blob_to_vec(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    a = array.array("f")
    a.frombytes(blob)
    return list(a)


# ── Persistence ──────────────────────────────────────────────────────────────

async def remember(story_id: int, text: str, scene_number: int = 0,
                   kind: str = "recap", model: str = "") -> int:
    text = (text or "").strip()
    if not text:
        return 0
    vec, used = await embed_text(text, model=model)
    blob = vec_to_blob(vec) if vec else None
    return add_memory(story_id, text, scene_number=scene_number,
                      kind=kind, embedding=blob, embed_model=used)


# ── Retrieval ────────────────────────────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    s = 0.0
    for x, y in zip(a, b):
        s += x * y
    return s  # both already L2-normalized


async def recall(story_id: int, query: str, top_k: int = 3,
                 model: str = "", exclude_recent: int = 1) -> list[dict]:
    """Return top-k memories most similar to ``query``. ``exclude_recent``
    drops the N newest memories so the model isn't fed back its own latest
    recap as a "memory"."""
    query = (query or "").strip()
    if not query or top_k <= 0:
        return []
    rows = get_memories(story_id, limit=500)
    if not rows:
        return []
    if exclude_recent > 0:
        rows = rows[exclude_recent:]
    if not rows:
        return []

    qvec, _ = await embed_text(query, model=model)

    # Re-embed any rows that are missing an embedding or were stored under a
    # different model than the current query.
    missing: list[dict] = []
    for r in rows:
        if not r.get("embedding"):
            missing.append(r)
        # Cross-model comparison only safe within same family. For mixed cases
        # we fall back to hash for a uniform space.
    if missing:
        await _backfill_embeddings(missing, model=model)
        # Reload
        rows = get_memories(story_id, limit=500)
        if exclude_recent > 0:
            rows = rows[exclude_recent:]

    scored: list[tuple[float, dict]] = []
    hq_cache: list[float] | None = None
    for r in rows:
        v = blob_to_vec(r.get("embedding"))
        if v and len(v) == len(qvec):
            score = _cosine(qvec, v)
        else:
            # Length mismatch (different embedding family) → compare in hash space
            if hq_cache is None:
                hq_cache = _hash_embed(query)
            hv = _hash_embed(r.get("text", ""))
            score = _cosine(hq_cache, hv)
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for s, r in scored[:top_k] if s > 0]


async def _backfill_embeddings(rows: Iterable[dict], model: str = "") -> None:
    conn = get_connection()
    try:
        for r in rows:
            vec, used = await embed_text(r.get("text", ""), model=model)
            if not vec:
                continue
            conn.execute(
                "UPDATE memories SET embedding=?, embed_model=? WHERE id=?",
                (vec_to_blob(vec), used, r["id"]),
            )
        conn.commit()
    finally:
        conn.close()
