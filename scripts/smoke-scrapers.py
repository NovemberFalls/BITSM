"""Smoke-test CLI for BITSM scrapers.

Exercises every registered scraper with max_docs=3 (or a specified limit) against
live third-party sources and reports which ones work today.

Usage:
    python scripts/smoke-scrapers.py                    # run all slugs with max_docs=3
    python scripts/smoke-scrapers.py --slug toast       # single slug
    python scripts/smoke-scrapers.py --max-docs 5       # override limit
    python scripts/smoke-scrapers.py --timeout 90       # override per-slug timeout (default 60s)
    python scripts/smoke-scrapers.py --output-dir /tmp/mysmoke/  # override smoke output dir
"""

import argparse
import concurrent.futures
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path — match manage.py pattern so this runs from project root
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env if dotenv is available (mirrors manage.py)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Logging — show scraper output so the operator can see progress
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("smoke")

# ---------------------------------------------------------------------------
# Import scraper registry
# ---------------------------------------------------------------------------
try:
    from services.scrapers import available, get_scraper
except ImportError as exc:
    print(f"SETUP-FAILED: cannot import services.scrapers — {exc}", file=sys.stderr)
    sys.exit(2)

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------
PASS = "PASS"
FAIL = "FAIL"
TIMEOUT = "TIMEOUT"
SKIPPED_ENV = "SKIPPED-ENV"

# Playwright-dependent scrapers (confirmed via salesforce.py)
PLAYWRIGHT_SCRAPERS = {"bill_com"}

DEFAULT_MAX_DOCS = 3
DEFAULT_TIMEOUT = 60  # seconds per slug


# ---------------------------------------------------------------------------
# Core: run one scraper in isolation
# ---------------------------------------------------------------------------

def _is_playwright_env_error(exc: Exception) -> bool:
    """Return True if the exception is a missing Chromium environment error."""
    exc_type = type(exc).__name__
    exc_mod = type(exc).__module__ or ""
    msg = str(exc)

    # playwright._impl._errors.Error with "Executable doesn't exist"
    if "playwright" in exc_mod and "Error" in exc_type:
        if "Executable doesn't exist" in msg or "Executable" in msg:
            return True
    # RuntimeError raised by salesforce.py when playwright import fails
    if exc_type == "RuntimeError" and "playwright install chromium" in msg.lower():
        return True
    # Catch ImportError if playwright itself is not pip-installed
    if exc_type == "ImportError" and "playwright" in msg.lower():
        return True
    return False


def run_slug(slug: str, output_dir: Path, max_docs: int) -> dict:
    """Run a single scraper; return a result dict (no timeout logic here)."""
    scraper_fn = get_scraper(slug)
    if scraper_fn is None:
        return {
            "status": FAIL,
            "stats": None,
            "exception": ValueError(f"No scraper registered for '{slug}'"),
            "output_dir": output_dir,
        }

    slug_dir = output_dir / slug
    slug_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    try:
        stats = scraper_fn(slug_dir, max_docs=max_docs)
        duration = time.monotonic() - t0
        return {
            "status": None,  # will be set by classify()
            "stats": stats,
            "exception": None,
            "duration": duration,
            "output_dir": slug_dir,
        }
    except Exception as exc:
        duration = time.monotonic() - t0
        return {
            "status": None,
            "stats": None,
            "exception": exc,
            "duration": duration,
            "output_dir": slug_dir,
        }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _find_sample_file(slug_dir: Path) -> tuple[Path | None, int | None]:
    """Return (path, byte_size) of one .txt file in slug_dir, or (None, None)."""
    try:
        for f in sorted(slug_dir.iterdir()):
            if f.suffix == ".txt":
                size = f.stat().st_size
                return f, size
    except Exception:
        pass
    return None, None


def classify(slug: str, raw: dict, timeout_sec: int) -> dict:
    """Turn a raw run_slug result into a fully-classified report row."""
    exc = raw.get("exception")
    stats = raw.get("stats")
    slug_dir: Path = raw.get("output_dir", Path(tempfile.gettempdir()))
    duration = raw.get("duration", 0.0)
    timed_out = raw.get("timed_out", False)
    status = raw.get("status")  # may already be set (e.g. TIMEOUT)

    sample_file, sample_bytes = _find_sample_file(slug_dir)
    sample_path = str(sample_file) if sample_file else None

    if timed_out or status == TIMEOUT:
        return {
            "slug": slug,
            "status": TIMEOUT,
            "duration_sec": round(duration, 1),
            "saved": None,
            "skipped": None,
            "errors": None,
            "total": None,
            "sample_file": sample_path,
            "sample_bytes": sample_bytes,
            "note": f"timeout at {timeout_sec}s",
        }

    if exc is not None:
        if _is_playwright_env_error(exc):
            note = f"Chromium not installed — {type(exc).__name__}: {str(exc)[:120]}"
            return {
                "slug": slug,
                "status": SKIPPED_ENV,
                "duration_sec": round(duration, 1),
                "saved": None,
                "skipped": None,
                "errors": None,
                "total": None,
                "sample_file": sample_path,
                "sample_bytes": sample_bytes,
                "note": note,
            }
        note = f"{type(exc).__name__}: {str(exc)[:160]}"
        return {
            "slug": slug,
            "status": FAIL,
            "duration_sec": round(duration, 1),
            "saved": None,
            "skipped": None,
            "errors": None,
            "total": None,
            "sample_file": sample_path,
            "sample_bytes": sample_bytes,
            "note": note,
        }

    # No exception — check stats
    saved = stats.get("saved", 0) if stats else 0
    skipped = stats.get("skipped", 0) if stats else 0
    errors = stats.get("errors", 0) if stats else 0
    total = stats.get("total", 0) if stats else 0

    # Refresh sample after scraper ran
    sample_file, sample_bytes = _find_sample_file(slug_dir)
    sample_path = str(sample_file) if sample_file else None

    if saved >= 1 and sample_file is not None:
        status = PASS
        note = ""
    elif saved >= 1 and sample_file is None:
        # Stats say saved but no file on disk — suspicious
        status = FAIL
        note = f"stats.saved={saved} but no .txt found in output dir"
    elif total == 0:
        status = FAIL
        note = "discovery yielded 0 articles (site structure change suspected or seed 404)"
    else:
        status = FAIL
        note = f"saved=0 (total={total}, skipped={skipped}, errors={errors})"

    return {
        "slug": slug,
        "status": status,
        "duration_sec": round(duration, 1),
        "saved": saved,
        "skipped": skipped,
        "errors": errors,
        "total": total,
        "sample_file": sample_path,
        "sample_bytes": sample_bytes,
        "note": note,
    }


# ---------------------------------------------------------------------------
# Parallel executor with per-slug timeout
# ---------------------------------------------------------------------------

def run_all(
    slugs: list[str],
    output_dir: Path,
    max_docs: int,
    timeout_sec: int,
) -> list[dict]:
    results = []

    # Run one slug per thread; futures carry per-slug timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(slugs)) as executor:
        fs = {}
        slug_start = {}
        for slug in slugs:
            t0 = time.monotonic()
            slug_start[slug] = t0
            future = executor.submit(run_slug, slug, output_dir, max_docs)
            fs[future] = slug

        for future, slug in fs.items():
            elapsed_before_submit = time.monotonic() - slug_start[slug]
            remaining = max(1, timeout_sec - elapsed_before_submit)
            try:
                raw = future.result(timeout=remaining)
            except concurrent.futures.TimeoutError:
                raw = {
                    "status": TIMEOUT,
                    "stats": None,
                    "exception": None,
                    "duration": timeout_sec,
                    "timed_out": True,
                    "output_dir": output_dir / slug,
                }
            row = classify(slug, raw, timeout_sec)
            results.append(row)
            _print_row_live(row)

    # Sort to match input slug order
    slug_order = {s: i for i, s in enumerate(slugs)}
    results.sort(key=lambda r: slug_order.get(r["slug"], 999))
    return results


# ---------------------------------------------------------------------------
# Live progress printing
# ---------------------------------------------------------------------------

def _print_row_live(row: dict):
    status = row["status"]
    slug = row["slug"]
    dur = row["duration_sec"]
    saved = row["saved"]
    note = row["note"] or ""
    line = f"  [{status:<12}] {slug:<25} {dur:>6.1f}s  saved={saved}  {note}"
    print(line)


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def _format_summary(results: list[dict], max_docs: int, timeout_sec: int, wall: float) -> str:
    groups: dict[str, list[str]] = {PASS: [], FAIL: [], TIMEOUT: [], SKIPPED_ENV: []}
    for r in results:
        groups.setdefault(r["status"], []).append(r["slug"])

    mins = int(wall) // 60
    secs = int(wall) % 60
    wall_str = f"{mins}m {secs}s" if mins else f"{secs}s"

    lines = [f"\nScraper smoke summary (max_docs={max_docs}, timeout={timeout_sec}s):"]
    for status in (PASS, FAIL, TIMEOUT, SKIPPED_ENV):
        slugs = groups.get(status, [])
        slug_list = ", ".join(slugs) if slugs else "-"
        lines.append(f"  {status:<14}: {len(slugs):>2} ({slug_list})")
    lines.append(f"  {'Total slugs':<14}: {len(results):>2}")
    lines.append(f"  {'Total wall':<14}: {wall_str}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test BITSM scrapers with a small document budget."
    )
    parser.add_argument(
        "--slug",
        metavar="SLUG",
        help="Run a single slug instead of all registered scrapers.",
    )
    parser.add_argument(
        "--max-docs",
        type=int,
        default=DEFAULT_MAX_DOCS,
        metavar="N",
        help=f"Maximum documents per scraper (default: {DEFAULT_MAX_DOCS}).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        metavar="SECS",
        help=f"Per-slug wall-clock timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--output-dir",
        metavar="PATH",
        help="Base directory for smoke output (default: <tempdir>/bitsm-scraper-smoke/).",
    )
    args = parser.parse_args()

    # Determine output dir
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = Path(tempfile.gettempdir()) / "bitsm-scraper-smoke"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Determine slugs to run
    all_slugs = available()
    if args.slug:
        if args.slug not in all_slugs:
            print(f"ERROR: slug '{args.slug}' not registered. Available: {all_slugs}", file=sys.stderr)
            sys.exit(1)
        slugs = [args.slug]
    else:
        slugs = all_slugs

    max_docs = args.max_docs
    timeout_sec = args.timeout

    print(f"BITSM scraper smoke run")
    print(f"  Slugs         : {len(slugs)} ({', '.join(slugs)})")
    print(f"  max_docs      : {max_docs}")
    print(f"  timeout/slug  : {timeout_sec}s")
    print(f"  output_dir    : {output_dir}")
    print(f"  registered    : {len(all_slugs)} scrapers total")
    if len(all_slugs) != 18:
        print(f"  WARNING: expected 18 registered scrapers, found {len(all_slugs)}")
    print()
    print(f"{'Status':<14}  {'Slug':<25} {'Duration':>9}  {'saved=?'}  Note")
    print("-" * 80)

    wall_start = time.monotonic()
    results = run_all(slugs, output_dir, max_docs, timeout_sec)
    wall = time.monotonic() - wall_start

    # Save JSON report
    report_path = output_dir / "smoke-report.json"
    report_data = {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "max_docs": max_docs,
        "timeout_sec": timeout_sec,
        "total_wall_sec": round(wall, 1),
        "slugs_expected": 18,
        "slugs_found": len(all_slugs),
        "results": results,
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)

    print()
    print(_format_summary(results, max_docs, timeout_sec, wall))
    print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    main()
