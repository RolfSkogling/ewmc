# EWMC — Emotionally-Weighted Memory Consolidation

A small, local-first reference implementation of a memory store for AI
assistants that keeps **what matters** instead of just **what was said**.

Most "AI memory" is a flat log: everything is stored, everything is equal, and
retrieval is recency- or similarity-only. EWMC instead gives every memory a
*weight* that changes over time — memories that proved consequential persist,
trivia fades, and a nightly consolidation pass reorganizes the store the way a
night's sleep reorganizes a day's experiences.

Everything runs on your own machine. There is no cloud, no account, and no
network call except to a local [Ollama](https://ollama.com) instance for
embeddings and (optionally) summarization. Your memories never leave the box.

---

## The four mechanisms

EWMC operationalizes four ideas. Each maps to a function in
[`lib/ewmc.py`](lib/ewmc.py).

**1. Affect scoring at encoding.** When a memory is added it gets an *affect
score* in `[0, 1]` estimating how much it seems to matter — high for decisions,
breakthroughs, and stakes; low for cosmetic details. Items below a configurable
floor (`min_affect_to_store`) are dropped before they ever hit the database, so
the store doesn't fill up with noise. Scoring can use a local LLM; a transparent
keyword heuristic is the fallback.

**2. Consequence reinforcement.** Every time a memory is retrieved, its
`consequence` and `current_weight` tick upward and the access is logged. A fact
you keep coming back to rises on its own — usefulness is treated as evidence
that the memory was worth keeping. This is the long-run signal that separates a
genuinely important memory from one that merely *looked* important when first
written.

**3. Adaptive decay with emotional floors.** Weights decay exponentially toward
a floor: `weight(t) = floor + (weight - floor) · e^(−rate·days)`. Three twists
make the decay *adaptive* rather than uniform:

- **Tier-dependent rate** — core memories don't decay; active ones decay
  slowly; ephemeral ones decay fast.
- **Affect dampening** — higher-affect memories decay more slowly.
- **Emotional floors** — a high-affect memory can never fall below a minimum
  weight, so it stays retrievable indefinitely even if rarely accessed.
- **Negative-valence asymmetry** — negative memories decay faster and floor
  lower. This is deliberate: it keeps useful lessons without letting the store
  dwell on or amplify negative material (no "rumination loops").

**4. Background consolidation.** A nightly job ([`bin/consolidate`](bin/consolidate))
applies decay, flags high-consequence items as core candidates, demotes faded
active memories to an archive tier (optionally compressing them to a one-line
summary via a local LLM), and deletes expired ephemeral memories. It is the
maintenance pass that keeps the store small and sharp over months.

### Tiers

| Tier | Meaning   | Decay     | Typical use                                   |
|------|-----------|-----------|-----------------------------------------------|
| 1    | Core      | none      | Identity, durable preferences (human-curated) |
| 2    | Active    | slow      | Current projects and decisions                |
| 3    | Archived  | faster    | Demoted/compressed older context              |
| 4    | Ephemeral | fast      | Scratch; auto-expires                         |

Tier 1 is special: promotion *into* it is never automatic. The consolidator can
only **flag** candidates; you decide what becomes core by editing
`tier1/core_memories.yaml` (start from
[`tier1/core_memories.template.yaml`](tier1/core_memories.template.yaml)).

---

## Honest framing: what's new and what isn't

This is **a synthesis and a clean local-first reference implementation**, not a
claim of new fundamental research. The individual ingredients are well-known:

- **Memory consolidation** as a metaphor for moving information from volatile to
  durable storage is old, both in cognitive science and in systems design.
- **Recency/frequency weighting** (think LRU/LFU caches, or page-rank-style
  reinforcement) is standard.
- **Spaced-repetition / forgetting curves** (SM-2, Leitner, Ebbinghaus) already
  model decay and reinforcement of items over time.
- **Vector memory / RAG** over embeddings is now the default approach to giving
  LLMs recall.

What EWMC contributes is the **combination**: affect-at-encoding *and*
consequence-on-access *and* affect-modulated adaptive decay with hard emotional
floors *and* a tiered nightly consolidation cycle — wired together into one
small SQLite + local-embeddings tool that any agent can drive through a single
CLI. The negative-valence asymmetry and the never-auto-promote-to-core rule are
opinionated design choices that fall out of taking the "emotional weighting"
metaphor seriously. Treat the framing as engineering opinion backed by prior
art, not as a validated cognitive model.

If you want to cite the conceptual write-up that this implementation pairs with,
see the companion paper referenced by the project owner. This repository is the
runnable artifact.

---

## Quickstart

Requirements: Python 3.10+, and [Ollama](https://ollama.com) running locally.

```bash
# 1. Create a virtualenv and install deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Pull a local embedding model (≈270 MB, 768-dim)
ollama pull nomic-embed-text
# (optional) a small chat model for summarization during consolidation
ollama pull llama3.1:8b

# 3. Initialize the store
bin/mem init

# 4. Use it
bin/mem add "Decided to use SQLite + local embeddings for the memory store." \
    --domain project-a --tier 2 --source cli
bin/mem search "what database did we choose" --top 5
bin/mem recall "memory store decisions" --top 5     # context block for an LLM
bin/mem status
```

Embeddings and summarization are **best-effort**: if Ollama isn't reachable,
`add` still stores the memory (without a vector) and `search` falls back to a
case-insensitive text search. Nothing in the system requires the network.

### Curating core (tier 1)

```bash
cp tier1/core_memories.template.yaml tier1/core_memories.yaml
$EDITOR tier1/core_memories.yaml          # fill in your durable facts
bin/mem tier1                             # prints core (+ promoted DB items) as YAML
```

Inject the output of `mem tier1` into the system prompt of whatever assistant
you use, so every session starts with the same durable context.

### Nightly consolidation

Run [`bin/consolidate`](bin/consolidate) on a schedule. A macOS launchd
template is in [`launchd/`](launchd/); on Linux use cron. Point the scheduler at
your venv's python so the dependencies resolve:

```
15 3 * * *  /ABSOLUTE/PATH/TO/ewmc/.venv/bin/python /ABSOLUTE/PATH/TO/ewmc/bin/consolidate
```

---

## Using it from an AI agent

The whole point of a single CLI with `--json` is that *any* tool that can run a
shell command can share the same memory: an MCP-enabled chat client, a coding
agent's bash tool, or you at the terminal. A minimal protocol:

- **At session start:** run `mem tier1` to load durable context.
- **Before answering "what did we decide about X":** run `mem recall "X"`.
- **When something worth keeping emerges:** run `mem add "..." --domain ...`.

Read-only commands (`status`, `show`, `list`, `search`, `recall`, `tier1`) open
the database in immutable mode, so they work even from sandboxes that can't take
a write lock. (`search`/`recall` will simply skip the reinforcement write in
that case.)

For a multi-machine, multi-agent pattern built *on top of* this store — where
several assistants contribute to and consume a shared context through git — see
the companion **local-ai-memory-os** project.

---

## Privacy & data handling

- **Local only.** The store is a single SQLite file. Embeddings come from a
  local Ollama model. No data is sent anywhere.
- **`.gitignore` excludes your data.** `*.db`, dumps, logs, your real
  `tier1/core_memories.yaml`, and any `*.token`/secret files are ignored by
  default. Commit the *code*, never your memories.
- **You own the floor.** If you don't want negative material to persist at all,
  set `floor_low/medium/high_affect` and the negative-valence multipliers to
  taste in `config.yaml`.

## Layout

```
ewmc/
├── bin/
│   ├── mem            # the CLI: add / search / recall / promote / status / ...
│   └── consolidate    # nightly background consolidation job
├── lib/
│   ├── db.py          # SQLite wrapper (read-only/immutable mode for sandboxes)
│   ├── embeddings.py  # local Ollama embeddings + cosine similarity
│   └── ewmc.py        # the four mechanisms
├── tier1/
│   └── core_memories.template.yaml   # copy -> core_memories.yaml, fill in
├── launchd/
│   └── com.example.memory.consolidate.plist  # scheduler template
├── schema.sql
├── config.yaml        # thresholds, decay rates, model choices (defaults only)
├── requirements.txt
└── LICENSE            # MIT (change if you prefer)
```

## License

MIT — see [LICENSE](LICENSE). Replace the copyright holder before publishing.
