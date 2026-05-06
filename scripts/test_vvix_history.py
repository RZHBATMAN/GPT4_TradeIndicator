"""Diagnostic: does our Polygon tier give us enough VVIX history for Bot D?

Run from repo root:
    python scripts/test_vvix_history.py

Verdict: GO (>=252 bars) / PARTIAL / NO.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from core.config import get_config  # noqa: E402

VVIX_TICKER = "I:VVIX"
BASE_URL = "https://api.massive.com"

WINDOWS = [
    ("30 calendar days  (~21 trading)", 30),
    ("90 calendar days  (~63 trading)", 90),
    ("180 calendar days (~126 trading)", 180),
    ("365 calendar days (~252 trading)", 365),
    ("730 calendar days (~504 trading)", 730),
]


def banner(text: str) -> None:
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)


def try_aggregates(api_key: str, days_back: int) -> dict:
    end = datetime.utcnow().date()
    start = end - timedelta(days=days_back)
    url = (
        f"{BASE_URL}/v2/aggs/ticker/{VVIX_TICKER}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}"
        f"?adjusted=true&sort=asc&limit=50000&apiKey={api_key}"
    )

    print(f"  GET {url.replace(api_key, '***')}")

    try:
        r = requests.get(url, timeout=30)
    except Exception as e:
        return {"ok": False, "status_code": 0, "bars": 0,
                "error": f"network: {e}", "raw_keys": [], "first_date": None,
                "last_date": None}

    out = {"status_code": r.status_code, "ok": False, "bars": 0, "error": None,
           "raw_keys": [], "first_date": None, "last_date": None}

    print(f"  HTTP {r.status_code}")

    try:
        data = r.json()
    except Exception:
        out["error"] = f"non-JSON body: {r.text[:200]!r}"
        return out

    out["raw_keys"] = list(data.keys())

    if r.status_code != 200:
        out["error"] = f"{data.get('error') or data.get('message') or '(no message)'}"
        print(f"  body keys: {out['raw_keys']}")
        print(f"  body (truncated): {json.dumps(data)[:400]}")
        return out

    results = data.get("results") or []
    out["bars"] = len(results)
    if results:
        first_ts = results[0].get("t")
        last_ts = results[-1].get("t")
        if first_ts:
            out["first_date"] = datetime.utcfromtimestamp(first_ts / 1000).date().isoformat()
        if last_ts:
            out["last_date"] = datetime.utcfromtimestamp(last_ts / 1000).date().isoformat()
        out["ok"] = True
    else:
        out["error"] = (
            f"empty results -- status='{data.get('status')}', "
            f"resultsCount={data.get('resultsCount')}"
        )

    print(f"  body keys: {out['raw_keys']}")
    print(f"  bars: {out['bars']}")
    if out["first_date"] and out["last_date"]:
        print(f"  first bar: {out['first_date']}   last bar: {out['last_date']}")
    if out["error"]:
        print(f"  error: {out['error']}")

    return out


def main() -> int:
    banner("Polygon VVIX history diagnostic")

    cfg = get_config()
    api_key = cfg.get("POLYGON_API_KEY", "").strip()
    if not api_key:
        print("ERROR: POLYGON_API_KEY not configured in .config or env")
        return 2

    print(f"Using API key prefix: {api_key[:6]}...")
    print(f"Endpoint: {BASE_URL}/v2/aggs/ticker/{VVIX_TICKER}/range/1/day/<start>/<end>")

    banner("Sanity check -- VVIX snapshot (should already work)")
    snap_url = f"{BASE_URL}/v3/snapshot/indices?ticker.any_of={VVIX_TICKER}&apiKey={api_key}"
    print(f"  GET {snap_url.replace(api_key, '***')}")
    try:
        r = requests.get(snap_url, timeout=15)
        print(f"  HTTP {r.status_code}")
        if r.status_code == 200:
            j = r.json()
            results = j.get("results") or []
            if results:
                v = results[0].get("value")
                print(f"  current VVIX = {v}")
            else:
                print(f"  WARN: empty snapshot results: {json.dumps(j)[:200]}")
        else:
            print(f"  body: {r.text[:300]}")
    except Exception as e:
        print(f"  network error: {e}")

    results: list[tuple[str, int, dict]] = []
    for label, days in WINDOWS:
        banner(f"Aggregates test -- {label}")
        out = try_aggregates(api_key, days)
        results.append((label, days, out))

    banner("VERDICT")
    longest_ok_days = 0
    longest_ok_bars = 0
    for label, days, out in results:
        marker = "OK " if out["ok"] else "FAIL"
        bars = out["bars"]
        err = out["error"] or ""
        print(f"  [{marker}]  {label:40s}  bars={bars:5d}  {err}")
        if out["ok"] and days > longest_ok_days:
            longest_ok_days = days
            longest_ok_bars = bars

    print()
    if longest_ok_bars >= 252:
        print("  GO -- your tier supports >=252 daily VVIX bars.")
        print("       We can replace vvix_static_bucket() with a true 252-day")
        print("       rolling percentile in core/data/market_data.py.")
        print(f"       (longest successful window: {longest_ok_days} cal days, "
              f"{longest_ok_bars} bars)")
        return 0
    elif longest_ok_bars > 0:
        print(f"  PARTIAL -- got {longest_ok_bars} VVIX bars from your longest")
        print("            successful window. Not enough for a 252-day")
        print("            percentile, but might work as a shorter window.")
        print("            Options: (a) stay static, (b) shorter percentile,")
        print("            (c) upgrade Polygon tier.")
        return 1
    else:
        print("  NO -- your tier does not return VVIX historical aggregates.")
        print("       Stay on static thresholds for now.")
        print("       To enable Bot D's percentile mode, either:")
        print("         (a) upgrade Polygon tier, or")
        print("         (b) source VVIX history elsewhere and cache locally.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
