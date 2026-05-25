-- EWMC Memory Store — Schema v1
-- A single local source of truth that any number of AI agents can share.
--
-- Core principle: each memory carries EWMC weight metadata so that retrieval,
-- decay, and consolidation can apply emotionally-weighted prioritization rather
-- than flat recency-based scoring.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ============================================================
-- memories: the central episodic memory table
-- ============================================================
CREATE TABLE IF NOT EXISTS memories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL,
    summary         TEXT,                    -- optional short form for tier-3 compression
    domain          TEXT,                    -- free-text tag, e.g. 'project-a', 'research', 'personal'
    tier            INTEGER NOT NULL DEFAULT 2,    -- 1=core, 2=active, 3=archived, 4=ephemeral

    -- EWMC weighting fields
    affect_score    REAL NOT NULL DEFAULT 0.5,     -- 0.0–1.0, initial emotional/importance weight at encoding
    consequence     REAL NOT NULL DEFAULT 0.0,     -- 0.0–1.0, accumulated evidence this mattered
    recurrence      INTEGER NOT NULL DEFAULT 0,    -- how many times referenced/retrieved
    valence         TEXT DEFAULT 'neutral',        -- 'positive', 'negative', 'neutral' — for asymmetric decay

    -- Decay control
    decay_rate      REAL NOT NULL DEFAULT 0.05,    -- per-day exponential decay coefficient
    floor_weight    REAL NOT NULL DEFAULT 0.0,     -- minimum weight after decay (emotional floor)
    current_weight  REAL NOT NULL DEFAULT 0.5,     -- dynamically updated effective weight

    -- Provenance
    source          TEXT,                    -- free label, e.g. 'agent', 'chat', 'manual', 'consolidator'
    session_id      TEXT,                    -- optional grouping
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_accessed   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_consolidated TIMESTAMP,

    -- Embedding stored as BLOB (binary float32 array)
    embedding       BLOB,
    embedding_model TEXT                     -- e.g. 'nomic-embed-text:latest'
);

CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
CREATE INDEX IF NOT EXISTS idx_memories_domain ON memories(domain);
CREATE INDEX IF NOT EXISTS idx_memories_weight ON memories(current_weight DESC);
CREATE INDEX IF NOT EXISTS idx_memories_accessed ON memories(last_accessed DESC);

-- ============================================================
-- links: relations between memories (consolidation, supersession)
-- ============================================================
CREATE TABLE IF NOT EXISTS links (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id         INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    to_id           INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    relation        TEXT NOT NULL,           -- 'supersedes', 'consolidates', 'related', 'contradicts'
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(from_id, to_id, relation)
);

CREATE INDEX IF NOT EXISTS idx_links_from ON links(from_id);
CREATE INDEX IF NOT EXISTS idx_links_to ON links(to_id);

-- ============================================================
-- access_log: every retrieval — feeds recurrence reinforcement
-- ============================================================
CREATE TABLE IF NOT EXISTS access_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id       INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    accessed_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source          TEXT,                    -- free label for which caller retrieved it
    query           TEXT                     -- the query that retrieved it
);

CREATE INDEX IF NOT EXISTS idx_access_memory ON access_log(memory_id);
CREATE INDEX IF NOT EXISTS idx_access_time ON access_log(accessed_at DESC);

-- ============================================================
-- consolidation_log: audit trail of background consolidation runs
-- ============================================================
CREATE TABLE IF NOT EXISTS consolidation_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    memories_decayed INTEGER DEFAULT 0,
    memories_promoted INTEGER DEFAULT 0,
    memories_demoted INTEGER DEFAULT 0,
    memories_removed INTEGER DEFAULT 0,
    memories_merged INTEGER DEFAULT 0,
    notes           TEXT
);

-- ============================================================
-- meta: key/value store for system state
-- ============================================================
CREATE TABLE IF NOT EXISTS meta (
    key             TEXT PRIMARY KEY,
    value           TEXT,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO meta (key, value) VALUES
    ('schema_version', '1'),
    ('embedding_model', 'nomic-embed-text:latest'),
    ('embedding_dim', '768');
