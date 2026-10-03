"""
Task 4.2 — Embedding-Based Intent Matching (Upgrade for Recipe Store)
======================================================================

Upgrades the TF-IDF similarity in task4_1_recipe_store.py to use real
vector embeddings from Ollama (fully offline, no internet needed).

Strategy (with graceful degradation):
  1. Try Ollama /api/embeddings (nomic-embed-text or phi3:mini)
  2. If Ollama unavailable → fall back to TF-IDF (already in recipe store)

Embeddings are cached in the SQLite DB so each goal string is only
embedded once — subsequent lookups are instant.

The RecipeStoreV2 class extends RecipeStore, overriding only the
similarity method. Everything else (save, delete, prune) is inherited.
"""

import os
import re
import json
import math
import sqlite3
import time
import requests
from typing import Optional

# Import base store
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from task4_1_recipe_store import RecipeStore, Recipe, DB_PATH, SIMILARITY_THRESHOLD

# ────────────────────────────────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────────────────────────────────
OLLAMA_URL       = "http://localhost:11434"
EMBED_MODELS     = ["nomic-embed-text", "phi3:mini"]  # try in order
EMBED_CACHE_TABLE = "embedding_cache"


# ────────────────────────────────────────────────────────────────────────────
# Ollama embedding helper
# ────────────────────────────────────────────────────────────────────────────

def _get_embedding(text: str, model: str) -> Optional[list]:
    """
    Calls Ollama /api/embeddings to get a vector for `text`.
    Returns float list or None if unavailable.
    """
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=0.8,
        )
        if resp.status_code == 200:
            return resp.json().get("embedding")
    except Exception:
        pass
    return None


def get_embedding_offline(text: str) -> Optional[list]:
    """
    Tries each embedding model in EMBED_MODELS order.
    Returns the first successful embedding, or None.
    """
    for model in EMBED_MODELS:
        emb = _get_embedding(text, model)
        if emb:
            return emb
    return None


def cosine_similarity(a: list, b: list) -> float:
    """Cosine similarity between two dense vectors."""
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ────────────────────────────────────────────────────────────────────────────
# RecipeStoreV2 — embedding-aware subclass of RecipeStore
# ────────────────────────────────────────────────────────────────────────────

class RecipeStoreV2(RecipeStore):
    """
    Extends RecipeStore with Ollama vector embeddings for intent matching.
    Embeddings are cached in the same SQLite DB to avoid re-computing.
    Falls back to TF-IDF if Ollama is not running.
    """

    def __init__(self, db_path: str = DB_PATH):
        super().__init__(db_path)
        self._init_embed_cache()
        self._embed_available = self._check_embed_available()
        if self._embed_available:
            print(f"  [Memory v2] Embedding model available — using vector similarity.")
        else:
            print(f"  [Memory v2] Ollama embeddings not available — using TF-IDF fallback.")

    def _check_embed_available(self) -> bool:
        """Returns True if at least one embedding model responds."""
        return get_embedding_offline("test") is not None

    def _init_embed_cache(self):
        """Creates the embedding cache table if it doesn't exist."""
        with self._connect() as conn:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {EMBED_CACHE_TABLE} (
                    text_hash TEXT PRIMARY KEY,
                    text      TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.commit()

    def _text_hash(self, text: str) -> str:
        """Simple hash key for cache lookup."""
        import hashlib
        return hashlib.md5(text.lower().strip().encode()).hexdigest()

    def _get_cached_embedding(self, text: str) -> Optional[list]:
        """Returns cached embedding for text, or None if not cached."""
        key = self._text_hash(text)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT embedding FROM {EMBED_CACHE_TABLE} WHERE text_hash = ?", (key,)
            ).fetchone()
        return json.loads(row["embedding"]) if row else None

    def _cache_embedding(self, text: str, embedding: list):
        """Stores an embedding in the cache."""
        key = self._text_hash(text)
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {EMBED_CACHE_TABLE} (text_hash, text, embedding, created_at) VALUES (?,?,?,?)",
                (key, text, json.dumps(embedding), time.time())
            )
            conn.commit()

    def embed(self, text: str) -> Optional[list]:
        """
        Gets the embedding for text, using cache first.
        Returns None if embeddings are unavailable (falls back to TF-IDF).
        """
        if not self._embed_available:
            return None
        cached = self._get_cached_embedding(text)
        if cached:
            return cached
        emb = get_embedding_offline(text)
        if emb:
            self._cache_embedding(text, emb)
        return emb

    def find(self, goal: str, threshold: float = SIMILARITY_THRESHOLD) -> Optional[Recipe]:
        """
        Finds the most similar recipe using vector embeddings if available,
        otherwise falls back to parent TF-IDF implementation.
        """
        if not self._embed_available:
            # Fall back to TF-IDF from parent class
            return super().find(goal, threshold)

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, goal, steps_json, success_count, last_used FROM recipes"
            ).fetchall()

        if not rows:
            return None

        # Get embedding for the query
        query_emb = self.embed(goal)
        if query_emb is None:
            return super().find(goal, threshold)

        best_row  = None
        best_sim  = -1.0

        for row in rows:
            doc_emb = self.embed(row["goal"])
            if doc_emb is None:
                continue
            sim = cosine_similarity(query_emb, doc_emb)
            if sim > best_sim:
                best_sim, best_row = sim, row

        if best_sim < threshold or best_row is None:
            return None

        from task4_1_recipe_store import adapt_steps
        saved_steps = json.loads(best_row["steps_json"])
        adapted     = adapt_steps(saved_steps, best_row["goal"], goal)

        print(f"  [Memory v2] Found recipe #{best_row['id']} "
              f"(embedding sim={best_sim:.3f}): {best_row['goal'][:60]}")

        return Recipe(
            id=best_row["id"],
            goal=best_row["goal"],
            steps=adapted,
            success_count=best_row["success_count"],
            last_used=best_row["last_used"],
            similarity=best_sim,
        )

    def stats(self) -> dict:
        base = super().stats()
        with self._connect() as conn:
            cache_count = conn.execute(
                f"SELECT COUNT(*) FROM {EMBED_CACHE_TABLE}"
            ).fetchone()[0]
        base["embed_model_available"] = self._embed_available
        base["cached_embeddings"] = cache_count
        return base


# ────────────────────────────────────────────────────────────────────────────
# Standalone demo / test
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TaskForge Task 4.2 — Embedding-Based Recipe Matching")
    print("=" * 60)

    store = RecipeStoreV2()

    # Clear old test data and save fresh recipes
    store.clear()
    store.save(
        "open youtube and search for python tutorials",
        [
            {"tool": "open_browser", "params": {"url": "youtube.com"}},
            {"tool": "fill_form",    "params": {"field": "search", "value": "python tutorials", "submit": "true"}},
        ]
    )
    store.save(
        "move resume.pdf from downloads to documents",
        [{"tool": "move_file", "params": {"filename": "resume.pdf", "source_folder": "downloads", "dest_folder": "documents"}}]
    )
    store.save(
        "set volume to 70",
        [{"tool": "system_settings", "params": {"command": "set volume to 70"}}]
    )

    print("\n--- Testing embedding similarity ---")
    tests = [
        "search for machine learning on youtube",           # should match youtube recipe
        "move report.docx from downloads to documents",     # should match move recipe
        "volume at 30 percent",                             # should match volume recipe
        "open google and log in to gmail",                  # should NOT match
    ]

    for t in tests:
        r = store.find(t)
        if r:
            print(f"\n  Goal:    {t!r}")
            print(f"  Matched: {r.goal!r} (sim={r.similarity:.3f})")
            print(f"  Steps:   {json.dumps(r.steps)}")
        else:
            print(f"\n  Goal:    {t!r}")
            print(f"  -> No match found")

    print("\n--- Stats ---")
    print(json.dumps(store.stats(), indent=2))
