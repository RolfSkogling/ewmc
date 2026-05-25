"""
ewmc.py — The four EWMC mechanisms operationalized for the local memory store.

Mechanism 1: Affect Scoring at Encoding         → score_affect()
Mechanism 2: Consequence Reinforcement          → reinforce_on_access()
Mechanism 3: Adaptive Decay with Emotional Floors → apply_decay()
Mechanism 4: Background Consolidation Cycles    → run_consolidation()

All functions are pure where possible; database side-effects are explicit.
"""
from __future__ import annotations

import math
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Mechanism 1: Affect scoring
# ---------------------------------------------------------------------------

# Heuristic word lists for fallback scoring when no LLM is available.
# Deliberately conservative — the LLM-based scorer is the primary path.
# These lists are illustrative and meant to be extended for your own language
# and domain. They are matched as plain substrings, case-insensitively.
HIGH_AFFECT_MARKERS = [
    # Explicit importance
    "important", "critical", "key decision", "remember this", "don't forget",
    # Consequence language
    "this changed", "breakthrough", "decided to", "we agreed", "final",
    # Professional stakes
    "client", "contract", "deliverable", "deadline",
]

LOW_AFFECT_MARKERS = [
    "font", "color", "use blue", "use red", "rename to", "move to folder",
]

NEGATIVE_VALENCE_MARKERS = [
    "frustrated", "angry", "failed", "broken", "crisis", "wrong",
]

POSITIVE_VALENCE_MARKERS = [
    "worked", "great", "success", "love it", "perfect", "delighted",
]


def score_affect_heuristic(text: str) -> tuple[float, str]:
    """Fallback affect scorer using keyword matching.
    Returns (affect_score 0–1, valence in {'positive','negative','neutral'}).
    """
    t = text.lower()

    high = sum(1 for m in HIGH_AFFECT_MARKERS if m in t)
    low = sum(1 for m in LOW_AFFECT_MARKERS if m in t)
    neg = sum(1 for m in NEGATIVE_VALENCE_MARKERS if m in t)
    pos = sum(1 for m in POSITIVE_VALENCE_MARKERS if m in t)

    base = 0.5
    base += 0.15 * min(high, 3)        # cap effect
    base -= 0.20 * min(low, 2)
    base += 0.05 * (pos + neg)         # emotional charge raises affect regardless of valence

    # Length signal — longer deliberations tend to matter more
    word_count = len(text.split())
    if word_count > 80:
        base += 0.10
    elif word_count < 8:
        base -= 0.10

    affect = max(0.0, min(1.0, base))

    if neg > pos and neg > 0:
        valence = "negative"
    elif pos > neg and pos > 0:
        valence = "positive"
    else:
        valence = "neutral"

    return affect, valence


def compute_floor(affect: float, valence: str, config: dict) -> float:
    """Compute the decay floor (minimum persistent weight) for a memory."""
    ewmc = config["ewmc"]
    if affect >= 0.7:
        floor = ewmc["floor_high_affect"]
    elif affect >= 0.4:
        floor = ewmc["floor_medium_affect"]
    else:
        floor = ewmc["floor_low_affect"]

    # Negative valence asymmetry — prevents trauma loops
    if valence == "negative":
        floor *= ewmc["negative_valence_floor_multiplier"]
    return floor


# ---------------------------------------------------------------------------
# Mechanism 2: Consequence reinforcement
# ---------------------------------------------------------------------------

def reinforce_on_access(conn: sqlite3.Connection, memory_id: int,
                        source: str, query: str, config: dict) -> None:
    """Called whenever a memory is retrieved. Updates recurrence, consequence,
    and last_accessed; logs the access."""
    boost = config["ewmc"]["recurrence_boost"]
    conn.execute(
        """UPDATE memories
           SET recurrence = recurrence + 1,
               consequence = MIN(1.0, consequence + ?),
               last_accessed = CURRENT_TIMESTAMP,
               current_weight = MIN(1.0, current_weight + ?)
           WHERE id = ?""",
        (boost, boost, memory_id),
    )
    conn.execute(
        "INSERT INTO access_log (memory_id, source, query) VALUES (?, ?, ?)",
        (memory_id, source, query),
    )


# ---------------------------------------------------------------------------
# Mechanism 3: Adaptive decay
# ---------------------------------------------------------------------------

def decay_step(current_weight: float, decay_rate: float, days_elapsed: float,
               floor: float, affect: float) -> float:
    """Exponential decay toward the floor.

    weight(t) = floor + (current - floor) * exp(-decay_rate * days)

    Affect amplifies persistence: high-affect memories get a slower effective decay.
    """
    if decay_rate <= 0:
        return current_weight
    effective_rate = decay_rate * (1.0 - 0.5 * affect)   # affect dampens decay
    decayed = floor + (current_weight - floor) * math.exp(-effective_rate * days_elapsed)
    return max(floor, decayed)


def tier_decay_rate(tier: int, config: dict) -> float:
    ewmc = config["ewmc"]
    return {
        1: ewmc["decay_rate_tier1"],
        2: ewmc["decay_rate_tier2"],
        3: ewmc["decay_rate_tier3"],
        4: ewmc["decay_rate_tier4"],
    }.get(tier, ewmc["decay_rate_default"])


def apply_decay(conn: sqlite3.Connection, config: dict) -> int:
    """Apply decay to all memories based on time since last_accessed.
    Returns count of memories updated."""
    rows = conn.execute(
        """SELECT id, tier, current_weight, decay_rate, floor_weight, affect_score, valence,
                  julianday('now') - julianday(last_accessed) AS days_elapsed
           FROM memories"""
    ).fetchall()

    updated = 0
    for r in rows:
        tier_rate = tier_decay_rate(r["tier"], config)
        # Negative valence asymmetry — decays faster
        if r["valence"] == "negative":
            tier_rate *= config["ewmc"]["negative_valence_decay_multiplier"]

        new_weight = decay_step(
            current_weight=r["current_weight"],
            decay_rate=tier_rate,
            days_elapsed=max(0.0, r["days_elapsed"] or 0.0),
            floor=r["floor_weight"],
            affect=r["affect_score"],
        )
        if abs(new_weight - r["current_weight"]) > 1e-4:
            conn.execute(
                "UPDATE memories SET current_weight = ? WHERE id = ?",
                (new_weight, r["id"]),
            )
            updated += 1
    return updated


# ---------------------------------------------------------------------------
# Mechanism 4: Background consolidation
# ---------------------------------------------------------------------------

def run_consolidation(conn: sqlite3.Connection, config: dict,
                      summarizer=None, dry_run: bool = False) -> dict:
    """Run one consolidation cycle. Returns stats dict.

    Steps:
      1. Apply decay
      2. Promote tier-2 memories with very high consequence to tier-1 candidate
      3. Demote low-weight tier-2 memories to tier-3 (compress)
      4. Remove tier-4 memories older than threshold
      5. Log the run

    `summarizer` is an optional callable(text) -> str used when demoting to tier 3.
    """
    stats = {
        "decayed": 0,
        "promoted_to_tier1": 0,
        "demoted_to_tier3": 0,
        "removed": 0,
        "merged": 0,
    }

    if not dry_run:
        stats["decayed"] = apply_decay(conn, config)

    ewmc = config["ewmc"]

    # Promote: tier 2 with very high consequence → mark as tier-1 candidate
    # We don't auto-promote to tier-1 — that requires human review via core_memories.yaml.
    # Instead, surface candidates by setting current_weight to 1.0 and tagging in notes.
    candidates = conn.execute(
        """SELECT id, content FROM memories
           WHERE tier = 2 AND consequence >= ? AND current_weight < 0.95""",
        (ewmc["tier1_promote_at_consequence"],),
    ).fetchall()

    if not dry_run:
        for c in candidates:
            conn.execute(
                "UPDATE memories SET current_weight = 1.0 WHERE id = ?", (c["id"],)
            )
    stats["promoted_to_tier1"] = len(candidates)

    # Demote: tier 2 with low weight → tier 3, compress
    to_demote = conn.execute(
        """SELECT id, content FROM memories
           WHERE tier = 2 AND current_weight < ?""",
        (ewmc["tier3_demote_at_weight"],),
    ).fetchall()

    if not dry_run:
        for d in to_demote:
            summary = None
            if summarizer is not None and config["consolidation"]["summarize_at_tier_demotion"]:
                try:
                    summary = summarizer(d["content"])
                except Exception:
                    summary = None
            conn.execute(
                """UPDATE memories
                   SET tier = 3, summary = COALESCE(?, summary),
                       last_consolidated = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (summary, d["id"]),
            )
    stats["demoted_to_tier3"] = len(to_demote)

    # Remove: tier 4 older than threshold
    age_days = ewmc["tier4_remove_at_age_days"]
    to_remove = conn.execute(
        """SELECT id FROM memories
           WHERE tier = 4 AND julianday('now') - julianday(created_at) > ?""",
        (age_days,),
    ).fetchall()

    if not dry_run:
        for r in to_remove:
            conn.execute("DELETE FROM memories WHERE id = ?", (r["id"],))
    stats["removed"] = len(to_remove)

    # Log the run
    if not dry_run:
        conn.execute(
            """INSERT INTO consolidation_log
               (memories_decayed, memories_promoted, memories_demoted, memories_removed, memories_merged, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (stats["decayed"], stats["promoted_to_tier1"], stats["demoted_to_tier3"],
             stats["removed"], stats["merged"],
             f"dry_run={dry_run}; timestamp={datetime.now(timezone.utc).isoformat()}"),
        )

    return stats
