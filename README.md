# OpenCode External Memory Tool

Persistent, cross-session memory for the [OpenCode](https://opencode.ai) AI agent — with fuzzy semantic search powered by embeddings.

The LLM can save important decisions, architectural notes, debug findings, and configuration tricks into a local SQLite database, then retrieve them in future sessions via full-text search (FTS5) **and** vector similarity search (`sqlite-vec`). A single `external_memory_search` call covers all three modes: keyword, semantic, and hybrid.

## Features

- **Full CRUD** — save, search, read, update, and delete memory entries
- **Three search modes** in one tool — `text` (FTS5, always fast), `semantic` (vec0 embeddings), `hybrid` (merges both)
- **Auto-embedding** — content is embedded via any OpenAI-compatible API on save; vectors are regenerated on update
- **Graceful degradation** — if the embedding API is slow or unreachable, text search keeps working
- **Lazy vec0 initialization** — vector dimension is auto-detected from the first API response; no manual configuration needed
- **Tag support** — entries can be tagged for categorization (`external_memory_tags` lists all tags)

## Use Cases

| Use Case | Example |
|----------|---------|
| **Cross-session context** | Agent remembers project architecture decisions between sessions |
| **Debug log** | Save investigation findings for future reference |
| **Learning repository** | Store "how to do X" recipes the agent discovers |
| **Project knowledge base** | Accumulate conventions, gotchas, and solutions |

## Installation

```bash
git clone https://github.com/andrewkopylev/opencode_external_memory_tool.git
cd opencode_external_memory_tool
bash install.sh
```

The installer will:

1. Detect system Python 3
2. Create or reuse a venv at `~/.config/opencode/tools/venv/`
3. Install `sqlite-vec` and `openai` into the venv
4. Copy `external_memory.ts`, `external_memory.py` to `~/.config/opencode/tools/`
5. **Interactively** ask for embedding API credentials:
   - Base URL (OpenAI-compatible endpoint)
   - API Key (hidden input)
   - Model ID (recommended: `google/gemini-embedding-2`)
6. Write `external_memory_config.json`

All files land in `~/.config/opencode/tools/` and become available to OpenCode on the next launch.

## Uninstall

```bash
bash uninstall.sh
```

Removes tool files. Asks before deleting the database (user data) and the venv (may be shared with other tools).

## Available Tools

All tools use the `external_memory_` prefix:

| Tool | Purpose |
|------|---------|
| `external_memory_save` | Create a new memory entry with topic, summary, content, and optional tags |
| `external_memory_search` | Search entries: `text`, `semantic`, or `hybrid` (default) |
| `external_memory_get` | Retrieve full entry by ID |
| `external_memory_update` | Modify an existing entry (partial update); regenerates embedding if content changed |
| `external_memory_delete` | Permanently delete an entry and its embedding |
| `external_memory_list` | List all entries, newest first (supports pagination) |
| `external_memory_tags` | List all unique tags across all entries |
| `external_memory_stats` | Show statistics: total entries, embedding dimension, database path |

## Example Usage in OpenCode

```
> Save architecture notes about the payment module

[AI calls external_memory_save with topic="architecture",
 summary="Payment module uses Stripe adapter with idempotency keys",
 content="...full notes...",
 tags=["architecture", "payments", "stripe"]]

→ { id: 1, topic: "architecture", ... }

> What did we decide about payments?

[AI calls external_memory_search with query="payment module idempotency",
 search_type="hybrid", limit=3]

→ [{ id: 1, topic: "architecture", score: 0.87, match_type: "text+semantic" }]

> Show me the full entry

[AI calls external_memory_get with id=1]

→ { id: 1, content: "...full notes...", ... }
```

## Configuration

File `~/.config/opencode/tools/external_memory_config.json`:

```json
{
  "db_path": "~/.config/opencode/tools/external_memory.db",
  "embedding": {
    "base_url": "https://<base_url>/api/v1",
    "api_key": "YOUR_API_KEY",
    "model": "google/gemini-embedding-2",
    "timeout_sec": 10
  },
  "search": {
    "default_limit": 10,
    "hybrid_text_weight": 0.3,
    "hybrid_semantic_weight": 0.7
  }
}
```

| Parameter | Description |
|-----------|-------------|
| `db_path` | SQLite database path (supports `~` expansion) |
| `embedding.base_url` | OpenAI-compatible API base URL |
| `embedding.api_key` | API key for the embedding service |
| `embedding.model` | Embedding model ID (recommended: `google/gemini-embedding-2`) |
| `embedding.timeout_sec` | HTTP request timeout (default: 10s) |
| `search.default_limit` | Default max results per search (default: 10) |
| `search.hybrid_text_weight` | Text score weight in hybrid mode (default: 0.3) |
| `search.hybrid_semantic_weight` | Semantic score weight in hybrid mode (default: 0.7) |

### Changing the embedding model

If you need to switch to a different embedding model:

```bash
# 1. Delete the old database (or use --rebuild if available)
rm ~/.config/opencode/tools/external_memory.db

# 2. Update the model in the config
# Edit ~/.config/opencode/tools/external_memory_config.json → embedding.model
```

The vec0 table will be recreated with the new model's vector dimension on the next `external_memory_save`.

## Embedding Model Tester

Included script `embeddings_model_tester.py` benchmarks one or more models before you commit to one:

```bash
python3 embeddings_model_tester.py
```

It prompts for base URL, API key, and model IDs, then runs 15 iterations per model and prints a table:

```
Model                      │ Dim  │ Tests │ OK │ TO │ Err │ Min(s) │ Avg(s) │ Max(s)
google/gemini-embedding-2  │ 768  │ 15    │ 15 │ 0  │ 0   │ 0.120  │ 0.185  │ 0.310
qwen/qwen3-embedding-8b    │ 4096 │ 15    │ 5  │ 9  │ 1   │ 0.523  │ 4.210  │ 8.031
```

## Architecture

```
OpenCode Agent
     │  external_memory_save, _search, _get, ...
     ▼
┌──────────────────────────────────────┐
│  external_memory.ts   (TypeScript)   │  ~/.config/opencode/tools/
│  Thin wrapper: Zod schemas, calls    │
│  Python via Bun.spawn + JSON stdin   │
└──────────────┬───────────────────────┘
               │ JSON over stdin/stdout
┌──────────────▼───────────────────────┐
│  external_memory.py   (Python)       │  ~/.config/opencode/tools/
│                                       │
│  SQLite + FTS5 ──── text search      │
│  sqlite-vec (vec0) ─ semantic search │
│  openai client ───── embedding API   │
└──────────────┬───────────────────────┘
               │
    ┌──────────┴──────────┐
    ▼                     ▼
  external_memory.db   OpenAI-compatible
  (~/.config/opencode/ Embedding API
   tools/)
```

## Dependencies

- Python 3.8+
- `sqlite-vec` — SQLite vector search extension with Python bindings
- `openai` — OpenAI-compatible API client (used for embeddings)
- `httpx` — HTTP client (installed as openai dependency)

All Python packages are installed into `~/.config/opencode/tools/venv/` by `install.sh`.

## License

MIT
