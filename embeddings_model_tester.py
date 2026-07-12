#!/usr/bin/env python3
"""
Embedding model performance tester.
Benchmarks OpenAI-compatible embedding models: response time, vector dimensions, timeout rate.
"""

import sys
import time
import json

try:
    import httpx
except ImportError:
    print("httpx package is required. Install: pip install httpx")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Prompt user for config
# ---------------------------------------------------------------------------

def prompt(label: str, default: str = "") -> str:
    if default:
        val = input(f"  {label} [{default}]: ").strip()
        return val if val else default
    else:
        val = ""
        while not val:
            val = input(f"  {label}: ").strip()
        return val


def read_secret(label: str) -> str:
    """Read API key without echoing. Falls back to plain input if getpass unavailable."""
    try:
        import getpass
        return getpass.getpass(f"  {label}: ").strip()
    except Exception:
        return input(f"  {label}: ").strip()


# ---------------------------------------------------------------------------
# Benchmark one model
# ---------------------------------------------------------------------------

TEST_PHRASE = (
    "The quick brown fox jumps over the lazy dog. "
    "Pack my box with five dozen liquor jugs."
)
ITERATIONS = 15
TIMEOUT_SEC = 10


def benchmark_model(base_url: str, api_key: str, model: str, phrase: str, count: int) -> dict:
    """Run `count` embedding requests and return timing stats."""
    client = httpx.Client(
        base_url=base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        timeout=httpx.Timeout(connect=5.0, read=TIMEOUT_SEC, write=10.0, pool=5.0),
    )

    times = []
    timeouts = 0
    errors = 0
    vector_dim = None
    last_error = None

    for i in range(count):
        t0 = time.monotonic()
        try:
            resp = client.post(
                "/embeddings",
                json={"model": model, "input": phrase, "encoding_format": "float"},
            )
            elapsed = time.monotonic() - t0

            if resp.status_code == 200:
                data = resp.json()
                emb = data["data"][0]["embedding"]
                times.append(elapsed)
                if vector_dim is None:
                    vector_dim = len(emb)
            else:
                errors += 1
                last_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
        except httpx.TimeoutException:
            elapsed = time.monotonic() - t0
            timeouts += 1
        except Exception as e:
            errors += 1
            last_error = str(e)[:100]

    client.close()

    result = {
        "model": model,
        "vector_dim": vector_dim,
        "total": count,
        "completed": len(times),
        "timeouts": timeouts,
        "errors": errors,
        "min_s": round(min(times), 3) if times else None,
        "max_s": round(max(times), 3) if times else None,
        "avg_s": round(sum(times) / len(times), 3) if times else None,
        "last_error": last_error,
    }
    return result


# ---------------------------------------------------------------------------
# Table formatter (no external deps)
# ---------------------------------------------------------------------------

def format_table(results: list[dict]) -> str:
    """Render results as a readable ASCII table."""
    if not results:
        return "(no results)"

    headers = ["Model", "Dim", "Tests", "OK", "TO", "Err", "Min(s)", "Avg(s)", "Max(s)"]
    rows = []
    for r in results:
        rows.append([
            r["model"],
            str(r["vector_dim"]) if r["vector_dim"] else "N/A",
            str(r["total"]),
            str(r["completed"]),
            str(r["timeouts"]),
            str(r["errors"]),
            f'{r["min_s"]:.3f}' if r["min_s"] is not None else "-",
            f'{r["avg_s"]:.3f}' if r["avg_s"] is not None else "-",
            f'{r["max_s"]:.3f}' if r["max_s"] is not None else "-",
        ])

    # Column widths
    col_widths = [
        max(len(h), *(len(row[i]) for row in rows))
        for i, h in enumerate(headers)
    ]

    def fmt_row(cells: list[str]) -> str:
        return " │ ".join(c.ljust(w) for c, w in zip(cells, col_widths))

    sep = "─┼─".join("─" * w for w in col_widths)

    lines = []
    lines.append(fmt_row(headers))
    lines.append(sep)
    for row in rows:
        lines.append(fmt_row(row))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print()
    print("=" * 60)
    print("  Embedding Model Performance Tester")
    print("=" * 60)
    print()
    print("  Tests each model with", ITERATIONS, "iterations")
    print("  Timeout per request:", TIMEOUT_SEC, "seconds")
    print("  Test phrase:", TEST_PHRASE[:60] + "...")
    print()

    base_url = prompt("Base URL", "https://<base_url>/api/v1")
    api_key = read_secret("API Key")
    models_raw = prompt("Model ID(s) (space or comma separated)", "model-a model-b")

    # Parse models
    models = []
    for part in models_raw.replace(",", " ").split():
        part = part.strip()
        if part:
            models.append(part)

    if not models:
        print("No models specified. Exiting.")
        sys.exit(1)

    print()
    print(f"  Base URL: {base_url}")
    print(f"  Models to test: {', '.join(models)}")
    print(f"  Iterations per model: {ITERATIONS}")
    print()
    print("─" * 60)

    results = []
    for model in models:
        print(f"\n  Benchmarking '{model}' ...")
        sys.stdout.flush()

        result = benchmark_model(base_url, api_key, model, TEST_PHRASE, ITERATIONS)
        results.append(result)

        # Live summary
        dim_str = f"dim={result['vector_dim']}" if result['vector_dim'] else "dim=N/A"
        if result["completed"] > 0:
            print(f"  ✓ {result['completed']}/{ITERATIONS} ok, "
                  f"{result['timeouts']} timeouts, {result['errors']} errors, "
                  f"min={result['min_s']}s, avg={result['avg_s']}s, max={result['max_s']}s, "
                  f"{dim_str}")
        else:
            print(f"  ✗ 0/{ITERATIONS} ok, all failed. last error: {result.get('last_error', '?')}")
        sys.stdout.flush()

    # Final table
    print()
    print("=" * 60)
    print(format_table(results))
    print()

    # Legend
    print("  Dim  = embedding vector dimension")
    print("  OK   = successful requests")
    print("  TO   = timeout (>{}s)".format(TIMEOUT_SEC))
    print("  Err  = HTTP/protocol errors")
    print()


if __name__ == "__main__":
    main()
