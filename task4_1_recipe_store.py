"""
Task 4.1 — SQLite Task Memory & Recipe Store
=============================================

Priority 4 from the TaskForge roadmap.

The recipe store gives the agent memory across sessions:
  - Every successful goal → saved as a recipe (goal text + tool sequence used)
  - Next time a similar goal arrives → recipe is retrieved and reused instantly
  - Similar goals matched via TF-IDF cosine similarity (no internet, no embeddings model needed)
  - Parameters (filenames, URLs, values) are adapted automatically in reused recipes

How it works:
  1. After agent_loop() completes successfully, call save_recipe(goal, steps)
  2. Before agent_loop() starts, call find_recipe(goal) — returns adapted steps or None
  3. If a recipe is found with similarity >= SIMILARITY_THRESHOLD, replay it directly
     (no LLM reasoning needed for familiar tasks)

Database: TaskForge/taskforge_memory.db (SQLite, no server needed)

Usage:
  from task4_1_recipe_store import RecipeStore
  store = RecipeStore()
  store.save("open chrome and search for python", [{"tool": "open_browser", ...}, ...])
  recipe = store.find("open browser search python tutorials")
  if recipe:
      for step in recipe.steps:
          execute(step)
"""

import os
import re
import json
import math
import sqlite3
import time
from typing import Optional
from dataclasses import dataclass, field


# ────────────────────────────────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────────────────────────────────
DB_PATH             = os.path.join(os.path.dirname(os.path.abspath(__file__)), "taskforge_memory.db")
SIMILARITY_THRESHOLD = 0.55   # minimum cosine similarity to reuse a recipe
MAX_RECIPES          = 500    # prune oldest when exceeded


# ────────────────────────────────────────────────────────────────────────────
# Data classes
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class Recipe:
    id: int
    goal: str
    steps: list            # list of {"tool": str, "params": dict}
    success_count: int = 1
    last_used: float = field(default_factory=time.time)
    similarity: float = 0.0   # filled in by find()


# ────────────────────────────────────────────────────────────────────────────
# TF-IDF cosine similarity (no external packages)
# ────────────────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Lowercases and splits into word tokens, removing punctuation."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """Builds a TF-IDF weighted term vector from a token list."""
    tf: dict[str, float] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    n = len(tokens) or 1
    return {t: (count / n) * idf.get(t, 1.0) for t, count in tf.items()}


def _cosine(a: dict, b: dict) -> float:
    """Cosine similarity between two sparse TF-IDF vectors."""
    dot = sum(a.get(t, 0) * b.get(t, 0) for t in b)
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _build_idf(all_goals: list[str]) -> dict[str, float]:
    """Computes inverse document frequency across all stored goal strings."""
    N = len(all_goals) or 1
    df: dict[str, int] = {}
    for goal in all_goals:
        for token in set(_tokenize(goal)):
            df[token] = df.get(token, 0) + 1
    return {t: math.log((N + 1) / (freq + 1)) + 1 for t, freq in df.items()}


# ────────────────────────────────────────────────────────────────────────────
# Parameter adaptation
# Replaces values from the saved recipe with values extracted from the new goal.
# ────────────────────────────────────────────────────────────────────────────

def _extract_values(text: str) -> list[str]:
    """
    Extracts 'interesting' values from a goal string:
    quoted strings, URLs, numbers, filenames, emails.
    These are the parts likely to differ between similar goals.
    """
    values = []
    values += re.findall(r'"([^"]+)"', text)
    values += re.findall(r"'([^']+)'", text)
    values += re.findall(r"https?://\S+", text)
    values += re.findall(r"\b[\w.-]+@[\w.-]+\.\w+\b", text)  # emails
    values += re.findall(r"\b[\w.-]+\.\w{2,4}\b", text)      # filenames/domains
    values += re.findall(r"\b\d+(?:\.\d+)?\b", text)          # numbers
    return [v.strip() for v in values if v.strip()]


def adapt_steps(saved_steps: list, saved_goal: str, new_goal: str) -> list:
    """
    Takes the saved recipe steps and substitutes parameter values from the
    new goal. Replaces values that appeared in the saved goal with
    corresponding values from the new goal (positional mapping).

    For example:
      saved_goal: "search for python tutorials on youtube"
      saved_step: fill_form(field="search", value="python tutorials")
      new_goal:   "search for machine learning on youtube"
      adapted:    fill_form(field="search", value="machine learning")
    """
    old_values = _extract_values(saved_goal)
    new_values = _extract_values(new_goal)

    # Build a replacement map: old_value -> new_value (by position)
    replacements = {}
    for i, old in enumerate(old_values):
        if i < len(new_values) and old != new_values[i]:
            replacements[old] = new_values[i]

    if not replacements:
        return saved_steps  # nothing to adapt

    adapted = []
    for step in saved_steps:
        new_step = json.loads(json.dumps(step))  # deep copy
        params_str = json.dumps(new_step.get("params", {}))
        for old_val, new_val in replacements.items():
            params_str = params_str.replace(old_val, new_val)
        try:
            new_step["params"] = json.loads(params_str)
        except json.JSONDecodeError:
            pass  # keep original if adaptation broke JSON
        adapted.append(new_step)
    return adapted


# ────────────────────────────────────────────────────────────────────────────
# Recipe Store
# ────────────────────────────────────────────────────────────────────────────

class RecipeStore:
    """
    SQLite-backed persistent memory for TaskForge agent.
    Stores successful goal→tool-sequence recipes and retrieves similar ones.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Creates the recipes table if it doesn't exist."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS recipes (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    goal          TEXT    NOT NULL,
                    steps_json    TEXT    NOT NULL,
                    success_count INTEGER NOT NULL DEFAULT 1,
                    last_used     REAL    NOT NULL,
                    created_at    REAL    NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_last_used ON recipes(last_used)")
            conn.commit()

    def save(self, goal: str, steps: list) -> int:
        """
        Saves a successful goal + step sequence as a recipe.
        If an identical or near-identical goal already exists (similarity >= 0.9),
        increments its success_count instead of creating a duplicate.
        Returns the recipe id.
        """
        existing = self.find(goal, threshold=0.9)
        now = time.time()
        with self._connect() as conn:
            if existing:
                conn.execute(
                    "UPDATE recipes SET success_count = success_count + 1, last_used = ?, steps_json = ? WHERE id = ?",
                    (now, json.dumps(steps), existing.id)
                )
                conn.commit()
                print(f"  [Memory] Updated recipe #{existing.id} (used {existing.success_count + 1}x): {goal[:60]}")
                return existing.id
            else:
                cursor = conn.execute(
                    "INSERT INTO recipes (goal, steps_json, success_count, last_used, created_at) VALUES (?, ?, 1, ?, ?)",
                    (goal, json.dumps(steps), now, now)
                )
                conn.commit()
                rid = cursor.lastrowid
                print(f"  [Memory] Saved new recipe #{rid}: {goal[:60]}")
                self._prune()
                return rid

    def find(self, goal: str, threshold: float = SIMILARITY_THRESHOLD) -> Optional[Recipe]:
        """
        Finds the most similar saved recipe using TF-IDF cosine similarity.
        Returns the Recipe (with adapted steps) if similarity >= threshold, else None.
        """
        with self._connect() as conn:
            rows = conn.execute("SELECT id, goal, steps_json, success_count, last_used FROM recipes").fetchall()

        if not rows:
            return None

        all_goals = [r["goal"] for r in rows]
        idf = _build_idf(all_goals + [goal])
        query_vec = _tfidf_vector(_tokenize(goal), idf)

        best_row = None
        best_sim  = -1.0
        for row in rows:
            doc_vec = _tfidf_vector(_tokenize(row["goal"]), idf)
            sim = _cosine(query_vec, doc_vec)
            if sim > best_sim:
                best_sim, best_row = sim, row

        if best_sim < threshold or best_row is None:
            return None

        saved_steps = json.loads(best_row["steps_json"])
        adapted     = adapt_steps(saved_steps, best_row["goal"], goal)

        print(f"  [Memory] Found recipe #{best_row['id']} (similarity={best_sim:.2f}): {best_row['goal'][:60]}")
        return Recipe(
            id=best_row["id"],
            goal=best_row["goal"],
            steps=adapted,
            success_count=best_row["success_count"],
            last_used=best_row["last_used"],
            similarity=best_sim,
        )

    def list_all(self) -> list[Recipe]:
        """Returns all stored recipes sorted by most recently used."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, goal, steps_json, success_count, last_used FROM recipes ORDER BY last_used DESC"
            ).fetchall()
        return [
            Recipe(
                id=r["id"], goal=r["goal"],
                steps=json.loads(r["steps_json"]),
                success_count=r["success_count"],
                last_used=r["last_used"],
            )
            for r in rows
        ]

    def delete(self, recipe_id: int):
        """Deletes a recipe by ID."""
        with self._connect() as conn:
            conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
            conn.commit()

    def clear(self):
        """Deletes all recipes (useful for testing)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM recipes")
            conn.commit()
        print("  [Memory] All recipes cleared.")

    def stats(self) -> dict:
        """Returns store statistics."""
        with self._connect() as conn:
            count   = conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
            top     = conn.execute(
                "SELECT goal, success_count FROM recipes ORDER BY success_count DESC LIMIT 5"
            ).fetchall()
        return {
            "total_recipes": count,
            "db_path": self.db_path,
            "top_recipes": [{"goal": r["goal"][:60], "used": r["success_count"]} for r in top],
        }

    def _prune(self):
        """Removes oldest recipes when store exceeds MAX_RECIPES."""
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
            if count > MAX_RECIPES:
                excess = count - MAX_RECIPES
                conn.execute(
                    "DELETE FROM recipes WHERE id IN "
                    "(SELECT id FROM recipes ORDER BY last_used ASC LIMIT ?)", (excess,)
                )
                conn.commit()
                print(f"  [Memory] Pruned {excess} oldest recipes (limit={MAX_RECIPES}).")


# ────────────────────────────────────────────────────────────────────────────
# Standalone demo / CLI
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  TaskForge Task 4.1 — SQLite Recipe Memory Demo")
    print("=" * 60)

    store = RecipeStore()

    # Save a recipe
    store.save(
        "open chrome and search for python tutorials on youtube",
        [
            {"tool": "open_browser", "params": {"url": "youtube.com"}},
            {"tool": "fill_form",    "params": {"field": "search", "value": "python tutorials", "submit": "true"}},
        ]
    )

    store.save(
        "set volume to 70",
        [{"tool": "system_settings", "params": {"command": "set volume to 70"}}]
    )

    store.save(
        "move notes.txt from downloads to documents",
        [{"tool": "move_file", "params": {"filename": "notes.txt", "source_folder": "downloads", "dest_folder": "documents"}}]
    )

    print("\n--- Testing retrieval ---")
    tests = [
        "search for machine learning videos on youtube",
        "set volume to 40",
        "move report.pdf from downloads to documents",
        "open notepad and write a poem",      # should NOT match
    ]
    for t in tests:
        r = store.find(t)
        if r:
            print(f"\nGoal: {t!r}")
            print(f"  Matched: {r.goal!r} (sim={r.similarity:.2f}, used={r.success_count}x)")
            print(f"  Adapted steps: {json.dumps(r.steps, indent=2)}")
        else:
            print(f"\nGoal: {t!r} -> No recipe found (below threshold)")

    print("\n--- Store stats ---")
    print(json.dumps(store.stats(), indent=2))
