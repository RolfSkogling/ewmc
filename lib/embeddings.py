"""
embeddings.py — Generate embeddings via local Ollama.

Uses Ollama's REST endpoint at /api/embeddings. The model must be pulled first:
    ollama pull nomic-embed-text

Returns numpy float32 arrays suitable for cosine similarity. Everything runs
locally — no text ever leaves the machine.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import List, Optional

import numpy as np


class EmbeddingError(RuntimeError):
    pass


def embed(text: str, model: str = "nomic-embed-text:latest",
          ollama_url: str = "http://localhost:11434") -> np.ndarray:
    """Embed a single text via Ollama. Returns float32 ndarray."""
    if not text or not text.strip():
        raise EmbeddingError("Cannot embed empty text")

    payload = json.dumps({"model": model, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(
        f"{ollama_url}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise EmbeddingError(f"Failed to reach Ollama at {ollama_url}: {e}") from e

    vec = data.get("embedding")
    if not vec:
        raise EmbeddingError(f"No embedding returned from Ollama (response: {data})")
    return np.asarray(vec, dtype=np.float32)


def embed_batch(texts: List[str], model: str = "nomic-embed-text:latest",
                ollama_url: str = "http://localhost:11434") -> List[np.ndarray]:
    """Embed multiple texts sequentially. Ollama doesn't batch via /api/embeddings,
    so we just loop — but this isolates that detail behind one function."""
    return [embed(t, model=model, ollama_url=ollama_url) for t in texts]


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def cosine_topk(query: np.ndarray, candidates: List[np.ndarray], k: int = 5) -> List[tuple[int, float]]:
    """Return [(index, similarity), ...] for top-k most similar candidates."""
    if not candidates:
        return []
    qn = np.linalg.norm(query)
    if qn == 0:
        return []
    scores = []
    for i, c in enumerate(candidates):
        if c is None:
            continue
        cn = np.linalg.norm(c)
        if cn == 0:
            continue
        scores.append((i, float(np.dot(query, c) / (qn * cn))))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:k]
