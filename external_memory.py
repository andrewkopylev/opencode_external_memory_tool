#!/usr/bin/env python3
"""External Memory tool for OpenCode — SQLite + FTS5 + vec0 + OpenAI embeddings."""

import sys
import json
import sqlite3
import os
import time
from pathlib import Path

# --- Optional imports (installed by install.sh into venv) ---
try:
    import sqlite_vec
    HAVE_VEC = True
except ImportError:
    sqlite_vec = None
    HAVE_VEC = False

try:
    from openai import OpenAI
    HAVE_OPENAI = True
except ImportError:
    OpenAI = None
    HAVE_OPENAI = False

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "external_memory_config.json"

DEFAULT_CONFIG = {
    "db_path": str(BASE_DIR / "external_memory.db"),
    "embedding": {
        "base_url": "https://<base_url>/api/v1",
        "api_key": "",
        "model": "google/gemini-embedding-2",
        "timeout_sec": 10,
    },
    "search": {
        "default_limit": 10,
        "hybrid_text_weight": 0.3,
        "hybrid_semantic_weight": 0.7,
    },
}


# ============================================================================
# Config
# ============================================================================

def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_FILE.exists():
        try:
            file_cfg = json.loads(CONFIG_FILE.read_text())
            _deep_merge(cfg, file_cfg)
        except (json.JSONDecodeError, IOError):
            pass
    # Expand ~ in db_path
    cfg["db_path"] = os.path.expanduser(cfg["db_path"])
    return cfg


def _deep_merge(base, override):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# ============================================================================
# Database
# ============================================================================

def open_db(db_path):
    """Open SQLite database with WAL mode and vec0 extension loaded."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")

    if HAVE_VEC:
        db.enable_load_extension(True)
        try:
            sqlite_vec.load(db)
        except Exception:
            # Fallback: vec not available on this platform
            pass

    return db


def init_schema(db):
    """Create tables if they don't exist."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memory_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memory_entries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            topic       TEXT    NOT NULL,
            summary     TEXT    NOT NULL,
            content     TEXT    NOT NULL,
            tags        TEXT    NOT NULL DEFAULT '[]',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            topic, summary, content,
            content = 'memory_entries',
            content_rowid = 'id'
        );

        -- Triggers to keep FTS in sync with memory_entries
        CREATE TRIGGER IF NOT EXISTS memory_fts_ai AFTER INSERT ON memory_entries BEGIN
            INSERT INTO memory_fts(rowid, topic, summary, content)
            VALUES (new.id, new.topic, new.summary, new.content);
        END;

        CREATE TRIGGER IF NOT EXISTS memory_fts_ad AFTER DELETE ON memory_entries BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, topic, summary, content)
            VALUES ('delete', old.id, old.topic, old.summary, old.content);
        END;

        CREATE TRIGGER IF NOT EXISTS memory_fts_au AFTER UPDATE ON memory_entries BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, topic, summary, content)
            VALUES ('delete', old.id, old.topic, old.summary, old.content);
            INSERT INTO memory_fts(rowid, topic, summary, content)
            VALUES (new.id, new.topic, new.summary, new.content);
        END;
    """)
    db.commit()


def get_meta(db, key, default=None):
    row = db.execute("SELECT value FROM memory_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(db, key, value):
    db.execute(
        "INSERT OR REPLACE INTO memory_meta(key, value) VALUES (?, ?)",
        (key, str(value)),
    )
    db.commit()


def vec_table_exists(db):
    """Check if memory_embeddings vec0 table exists."""
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_embeddings'"
    ).fetchone()
    return row is not None


def ensure_vec_table(db, vector_dim):
    """Create vec0 table with given dimension if it doesn't exist.
    
    Uses rowid as the natural link to memory_entries.id.
    """
    existing_dim = get_meta(db, "embedding_dim")
    if existing_dim is not None:
        existing_dim = int(existing_dim)
        if existing_dim != vector_dim:
            raise RuntimeError(
                f"Embedding dimension mismatch: stored={existing_dim}, "
                f"new={vector_dim}. The embedding model has likely changed. "
                f"Delete the database and re-index, or use the same model."
            )

    if vec_table_exists(db):
        return

    # Create vec0 virtual table — embedding is the only column, rowid links to memory_entries
    sql = f"CREATE VIRTUAL TABLE memory_embeddings USING vec0(embedding FLOAT[{vector_dim}])"
    db.execute(sql)
    set_meta(db, "embedding_dim", vector_dim)
    db.commit()


def row_to_dict(row):
    """Convert sqlite3.Row to dict."""
    if row is None:
        return None
    d = dict(row)
    # Parse tags JSON
    if "tags" in d and isinstance(d["tags"], str):
        try:
            d["tags"] = json.loads(d["tags"])
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
    return d


def rows_to_list(rows):
    return [row_to_dict(r) for r in rows]


# ============================================================================
# Embedding API (OpenAI-compatible)
# ============================================================================

_embedding_client = None
_embedding_config_hash = None


def _get_embedding_client(cfg):
    """Get or create OpenAI client. Re-created if config changed."""
    global _embedding_client, _embedding_config_hash

    emb = cfg["embedding"]
    config_key = (emb["base_url"], emb["api_key"], emb["model"])
    config_hash = hash(config_key)

    if _embedding_client is None or _embedding_config_hash != config_hash:
        if not HAVE_OPENAI:
            raise RuntimeError("openai package not installed. Run install.sh first.")

        # Build explicit httpx.Timeout for fine-grained control.
        # connect=5s prevents DNS/connection hangs, read=timeout_sec handles slow bodies.
        timeout_sec = float(emb.get("timeout_sec", 10))
        try:
            import httpx
            http_timeout = httpx.Timeout(connect=5.0, read=timeout_sec, write=10.0, pool=5.0)
        except ImportError:
            http_timeout = timeout_sec

        _embedding_client = OpenAI(
            api_key=emb["api_key"],
            base_url=emb["base_url"],
            timeout=http_timeout,
            max_retries=0,
        )
        _embedding_config_hash = config_hash

    return _embedding_client


def get_embedding(text, cfg):
    """Get embedding vector for text."""
    client = _get_embedding_client(cfg)
    emb = cfg["embedding"]
    response = client.embeddings.create(
        model=emb["model"],
        input=text,
        encoding_format="float",
    )
    return response.data[0].embedding


# ============================================================================
# Command handlers
# ============================================================================

def _ok(data):
    """Format success response."""
    return json.dumps(data, ensure_ascii=False, default=str)


def _err(msg):
    """Format error response."""
    return json.dumps({"error": str(msg)}, ensure_ascii=False)


def cmd_save(args, cfg):
    """Save a new memory entry."""
    topic = args.get("topic", "").strip()
    summary = args.get("summary", "").strip()
    content = args.get("content", "").strip()
    tags = args.get("tags", [])

    if not topic:
        return _err("topic is required")
    if not summary:
        return _err("summary is required")
    if not content:
        return _err("content is required")

    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not isinstance(tags, list):
        tags = []

    tags_json = json.dumps(tags, ensure_ascii=False)

    db = open_db(cfg["db_path"])
    init_schema(db)

    try:
        cur = db.execute(
            """INSERT INTO memory_entries (topic, summary, content, tags)
               VALUES (?, ?, ?, ?)""",
            (topic, summary, content, tags_json),
        )
        entry_id = cur.lastrowid

        # Try to create embedding
        embedding_ok = False
        embedding_dim = None
        try:
            vec = get_embedding(content, cfg)
            embedding_dim = len(vec)
            ensure_vec_table(db, embedding_dim)
            vec_json = json.dumps(vec)
            db.execute(
                "INSERT INTO memory_embeddings(rowid, embedding) VALUES (?, ?)",
                (entry_id, vec_json),
            )
            db.commit()
            embedding_ok = True
        except Exception as e:
            # Embedding failed — entry is still saved with text search support
            db.commit()
            embedding_ok = str(e)

        row = db.execute(
            "SELECT * FROM memory_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        result = row_to_dict(row)
        if embedding_ok is not True:
            result["_embedding_warning"] = f"Embedding not stored: {embedding_ok}"

        return _ok(result)
    except Exception as e:
        return _err(str(e))
    finally:
        db.close()


def cmd_search(args, cfg):
    """Search entries by text, semantic, or hybrid."""
    query = args.get("query", "").strip()
    if not query:
        return _err("query is required")

    search_type = args.get("search_type", "hybrid")
    limit = int(args.get("limit", cfg["search"]["default_limit"]))
    limit = max(1, min(limit, 100))

    if search_type == "text":
        return _search_text(cfg, query, limit)
    elif search_type == "semantic":
        return _search_semantic(cfg, query, limit)
    elif search_type == "hybrid":
        return _search_hybrid(args, cfg, query, limit)
    else:
        return _err(f"Unknown search_type: {search_type}. Use 'text', 'semantic', or 'hybrid'.")


def _search_text(cfg, query, limit):
    """FTS5 full-text search."""
    db = open_db(cfg["db_path"])
    init_schema(db)
    try:
        # Escape FTS5 special characters, use prefix search
        safe_query = _sanitize_fts_query(query)
        if not safe_query:
            # Fallback to LIKE if FTS query is empty after sanitizing
            like_pattern = f"%{query}%"
            rows = db.execute(
                """SELECT id, topic, summary, tags, updated_at
                   FROM memory_entries
                   WHERE topic LIKE ? OR summary LIKE ? OR content LIKE ?
                   ORDER BY updated_at DESC
                   LIMIT ?""",
                (like_pattern, like_pattern, like_pattern, limit),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT e.id, e.topic, e.summary, e.tags, e.updated_at,
                          f.rank AS score
                   FROM memory_fts f
                   JOIN memory_entries e ON f.rowid = e.id
                   WHERE memory_fts MATCH ?
                   ORDER BY f.rank
                   LIMIT ?""",
                (safe_query, limit),
            ).fetchall()

        results = []
        for r in rows:
            d = row_to_dict(r)
            d["match_type"] = "text"
            results.append(d)
        return _ok(results)
    except Exception as e:
        return _err(str(e))
    finally:
        db.close()


def _sanitize_fts_query(query):
    """Sanitize user query for FTS5, return a valid FTS5 MATCH expression or None."""
    # Remove FTS5 special characters, keep words
    import re
    words = re.findall(r'[\w\-]+', query)
    if not words:
        return None
    # Escape double-quote and join with AND for tokens in same record
    # Use prefix matching: append * to each word
    return " OR ".join(f'"{w}"*' for w in words)


def _search_semantic(cfg, query, limit):
    """Vector similarity search."""
    db = open_db(cfg["db_path"])
    init_schema(db)

    if not vec_table_exists(db):
        db.close()
        return _err("Semantic search unavailable: no embeddings have been stored yet. "
                     "Save an entry first to initialize the embedding index.")

    try:
        query_vec = get_embedding(query, cfg)
        query_vec_json = json.dumps(query_vec)

        # vec0 KNN requires LIMIT on the vec0 table directly; JOIN breaks KNN planning.
        # Use a subquery to isolate the KNN, then JOIN in the outer query.
        rows = db.execute(
            f"""SELECT e.id, e.topic, e.summary, e.tags, e.updated_at, inner.distance
               FROM (
                   SELECT v.rowid, v.distance
                   FROM memory_embeddings v
                   WHERE v.embedding MATCH ?
                   ORDER BY v.distance
                   LIMIT {int(limit)}
               ) AS inner
               JOIN memory_entries e ON inner.rowid = e.id
               ORDER BY inner.distance""",
            (query_vec_json,),
        ).fetchall()

        results = []
        for r in rows:
            d = row_to_dict(r)
            # vec0 default metric is L2 (Euclidean). For normalized vectors:
            # cosine_similarity = 1 - (l2_dist^2 / 2)
            l2_dist = float(d["distance"])
            d["score"] = round(1.0 - (l2_dist ** 2) / 2.0, 6)
            d["match_type"] = "semantic"
            results.append(d)
        return _ok(results)
    except Exception as e:
        return _err(f"Semantic search failed: {e}")
    finally:
        db.close()


def _search_hybrid(args, cfg, query, limit):
    """Merge text and semantic search results."""
    # Get text results
    try:
        text_json = _search_text(cfg, query, limit * 2)  # get more for merging
        text_results = json.loads(text_json)
        if isinstance(text_results, dict) and "error" in text_results:
            text_results = []
    except Exception:
        text_results = []

    # Get semantic results
    try:
        sem_json = _search_semantic(cfg, query, limit * 2)
        sem_results = json.loads(sem_json)
        if isinstance(sem_results, dict) and "error" in sem_results:
            sem_results = []
    except Exception:
        sem_results = []

    if not text_results and not sem_results:
        return _ok([])

    # Normalize scores to [0, 1]
    def normalize(items, score_key="score"):
        if not items:
            return {}, items
        scores = [it.get(score_key, 0) for it in items]
        min_s, max_s = min(scores), max(scores)
        if max_s == min_s:
            return {it["id"]: 0.5 for it in items}, items
        normed = {}
        for it in items:
            normed[it["id"]] = (it.get(score_key, 0) - min_s) / (max_s - min_s)
        return normed, items

    # For FTS5 rank, lower is better (like distance), so normalize inversely
    def normalize_fts(items):
        if not items:
            return {}, items
        ranks = [it.get("score", 0) for it in items]
        min_r, max_r = min(ranks), max(ranks)
        if max_r == min_r:
            return {it["id"]: 0.5 for it in items}, items
        normed = {}
        for it in items:
            normed[it["id"]] = 1.0 - (it.get("score", 0) - min_r) / (max_r - min_r)
        return normed, items

    text_normed, _ = normalize_fts(text_results)
    sem_normed, _ = normalize(sem_results)
    # Semantic scores are already similarity (0..1), normalize anyway
    sem_raw = {}
    for it in sem_results:
        sem_raw[it["id"]] = it.get("score", 0)

    alpha = cfg["search"]["hybrid_text_weight"]
    beta = cfg["search"]["hybrid_semantic_weight"]

    # Merge
    all_ids = set(list(text_normed.keys()) + list(sem_raw.keys()))
    merged = []
    for eid in all_ids:
        text_score = text_normed.get(eid, 0.0)
        sem_score = sem_raw.get(eid, 0.0)
        combined = alpha * text_score + beta * sem_score

        # Find the original entry data
        entry = None
        for r in text_results + sem_results:
            if r["id"] == eid:
                entry = dict(r)
                break
        if entry is None:
            continue

        match_types = []
        if eid in text_normed:
            match_types.append("text")
        if eid in sem_raw:
            match_types.append("semantic")

        entry["score"] = round(combined, 4)
        entry["match_type"] = "+".join(match_types)
        merged.append(entry)

    merged.sort(key=lambda x: x["score"], reverse=True)
    return _ok(merged[:limit])


def cmd_get(args, cfg):
    """Get full entry by ID."""
    entry_id = args.get("id")
    if entry_id is None:
        return _err("id is required")

    db = open_db(cfg["db_path"])
    init_schema(db)
    try:
        row = db.execute(
            "SELECT * FROM memory_entries WHERE id = ?", (int(entry_id),)
        ).fetchone()
        if row is None:
            return _err(f"Entry with id={entry_id} not found")
        return _ok(row_to_dict(row))
    except Exception as e:
        return _err(str(e))
    finally:
        db.close()


def cmd_update(args, cfg):
    """Update an existing entry."""
    entry_id = args.get("id")
    if entry_id is None:
        return _err("id is required")

    db = open_db(cfg["db_path"])
    init_schema(db)
    try:
        existing = db.execute(
            "SELECT * FROM memory_entries WHERE id = ?", (int(entry_id),)
        ).fetchone()
        if existing is None:
            return _err(f"Entry with id={entry_id} not found")

        setters = []
        params = []
        content_changed = False

        for field in ("topic", "summary", "content"):
            if field in args and args[field] is not None:
                new_val = str(args[field]).strip()
                if new_val and new_val != existing[field]:
                    setters.append(f"{field} = ?")
                    params.append(new_val)
                    if field == "content":
                        content_changed = True

        if "tags" in args and args["tags"] is not None:
            tags = args["tags"]
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except json.JSONDecodeError:
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
            new_tags_json = json.dumps(tags, ensure_ascii=False)
            if new_tags_json != existing["tags"]:
                setters.append("tags = ?")
                params.append(new_tags_json)

        if not setters:
            # Nothing changed
            return _ok(row_to_dict(existing))

        setters.append("updated_at = datetime('now')")
        params.append(int(entry_id))
        sql = f"UPDATE memory_entries SET {', '.join(setters)} WHERE id = ?"

        db.execute(sql, params)

        # Regenerate embedding if content changed
        if content_changed and vec_table_exists(db):
            try:
                new_content = args.get("content", existing["content"])
                vec = get_embedding(new_content, cfg)
                vec_json = json.dumps(vec)

                # Delete old embedding, insert new
                db.execute(
                    "DELETE FROM memory_embeddings WHERE rowid = ?",
                    (int(entry_id),),
                )
                db.execute(
                    "INSERT INTO memory_embeddings(rowid, embedding) VALUES (?, ?)",
                    (int(entry_id), vec_json),
                )
            except Exception:
                pass  # Embedding update is best-effort

        db.commit()

        row = db.execute(
            "SELECT * FROM memory_entries WHERE id = ?", (int(entry_id),)
        ).fetchone()
        return _ok(row_to_dict(row))
    except Exception as e:
        return _err(str(e))
    finally:
        db.close()


def cmd_delete(args, cfg):
    """Delete an entry."""
    entry_id = args.get("id")
    if entry_id is None:
        return _err("id is required")

    db = open_db(cfg["db_path"])
    init_schema(db)
    try:
        existing = db.execute(
            "SELECT id FROM memory_entries WHERE id = ?", (int(entry_id),)
        ).fetchone()
        if existing is None:
            return _err(f"Entry with id={entry_id} not found")

        # Delete from embeddings first (if table exists)
        if vec_table_exists(db):
            db.execute(
                "DELETE FROM memory_embeddings WHERE rowid = ?",
                (int(entry_id),),
            )

        # FTS trigger handles FTS cleanup
        db.execute("DELETE FROM memory_entries WHERE id = ?", (int(entry_id),))
        db.commit()
        return _ok({"deleted": True, "id": int(entry_id)})
    except Exception as e:
        return _err(str(e))
    finally:
        db.close()


def cmd_list(args, cfg):
    """List all entries with pagination."""
    limit = int(args.get("limit", 50))
    offset = int(args.get("offset", 0))
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    db = open_db(cfg["db_path"])
    init_schema(db)
    try:
        rows = db.execute(
            """SELECT id, topic, summary, tags, created_at, updated_at
               FROM memory_entries
               ORDER BY updated_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return _ok(rows_to_list(rows))
    except Exception as e:
        return _err(str(e))
    finally:
        db.close()


def cmd_tags(args, cfg):
    """List all unique tags."""
    db = open_db(cfg["db_path"])
    init_schema(db)
    try:
        rows = db.execute("SELECT tags FROM memory_entries").fetchall()
        all_tags = set()
        for r in rows:
            try:
                tags = json.loads(r["tags"])
                if isinstance(tags, list):
                    for t in tags:
                        if t and isinstance(t, str):
                            all_tags.add(t.strip())
            except (json.JSONDecodeError, TypeError):
                pass
        return _ok(sorted(all_tags))
    except Exception as e:
        return _err(str(e))
    finally:
        db.close()


def cmd_stats(args, cfg):
    """Return memory statistics."""
    db = open_db(cfg["db_path"])
    init_schema(db)
    try:
        total = db.execute("SELECT COUNT(*) as cnt FROM memory_entries").fetchone()["cnt"]
        has_vec = vec_table_exists(db)
        dim = get_meta(db, "embedding_dim", "N/A")
        return _ok({
            "total_entries": total,
            "has_embeddings": has_vec,
            "embedding_dim": dim,
            "db_path": cfg["db_path"],
        })
    except Exception as e:
        return _err(str(e))
    finally:
        db.close()


# ============================================================================
# Command dispatch
# ============================================================================

COMMANDS = {
    "save": cmd_save,
    "search": cmd_search,
    "get": cmd_get,
    "update": cmd_update,
    "delete": cmd_delete,
    "list": cmd_list,
    "tags": cmd_tags,
    "stats": cmd_stats,
}


def main():
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            print(json.dumps({"error": "No input received"}))
            sys.exit(1)
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON: {e}"}))
        sys.exit(1)

    command = payload.get("command", "")
    if command not in COMMANDS:
        print(json.dumps({"error": f"Unknown command: {command}. "
                           f"Available: {', '.join(sorted(COMMANDS.keys()))}"}))
        sys.exit(1)

    cfg = load_config()
    try:
        result = COMMANDS[command](payload, cfg)
        print(result)
    except Exception as e:
        print(json.dumps({"error": f"Command '{command}' failed: {e}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
