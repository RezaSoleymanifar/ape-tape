"""Read the ApeWisdom boards and append what they said.

ApeWisdom publishes the current snapshot with no history endpoint, so a time
series only exists if something records one. This does that, hourly.

Two things are written every hour:

  data/raw/YYYY-MM-DD/HH.json.gz   the responses as received, gzipped
  data/tape.jsonl                  derived statistics, one line per reading

Raw first, because a derived series can always be rebuilt from raw and raw can
never be rebuilt from derived. If the delta formula here turns out to be wrong,
or the API adds a field, the recomputation is possible only if the original
bytes were kept. The published page reads the derived file; the redistribution
question governs what is *published*, not what is kept.

Depth is capped at DEPTH rows per board rather than every page, and each
reading records the board's full `count` and `pages` so the truncation is
visible rather than silent.

Each observation carries `known_at`: when we read it, not when the posts were
written. Posts themselves are never stored.

    python collect.py                 # append one observation
    python collect.py --dry-run       # print it, write nothing
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
TAPE = os.path.join(ROOT, "data", "tape.jsonl")
RAW = os.path.join(ROOT, "data", "raw")
LATEST = os.path.join(ROOT, "docs", "latest.json")
SERIES = os.path.join(ROOT, "docs", "series.json")

API = "https://apewisdom.io/api/v1.0/filter/{board}/page/{page}"

# Every board the API answers for. The per-subreddit ones are not derivable
# from the aggregate — wallstreetbets moving alone is a different fact from the
# whole board moving — so each is recorded separately.
BOARDS = [
    "all-stocks", "all-crypto", "wallstreetbets", "stocks", "cryptocurrency",
    "options", "investing", "stockmarket", "pennystocks",
]

DEPTH = 100                 # rows kept per board: two pages, where the tail turns over
PER_PAGE = 100              # what one page returns
KEEP_HOURS = 24 * 90        # the rolling window the site reads
SITE_BOARDS = ("all-stocks", "all-crypto")      # what the page charts


def fetch_board(board: str, client: httpx.Client) -> dict:
    """Pages until DEPTH is met, keeping the envelope so truncation is visible."""
    rows: list[dict] = []
    count = pages = 0
    page = 1
    while len(rows) < DEPTH:
        r = client.get(API.format(board=board, page=page), timeout=45,
                       headers={"User-Agent": "ape-tape (github.com/RezaSoleymanifar/ape-tape)"})
        r.raise_for_status()
        body = r.json()
        count, pages = int(body.get("count") or 0), int(body.get("pages") or 0)
        got = body.get("results") or []
        rows += got
        if not got or page >= pages:
            break
        page += 1
        time.sleep(0.4)
    return {"board": board, "count": count, "pages": pages,
            "kept": len(rows[:DEPTH]), "results": rows[:DEPTH]}


def derive(board: dict) -> list[dict]:
    """Numbers computed from the board, not copies of it."""
    rows = board["results"]
    total = sum(int(r.get("mentions") or 0) for r in rows) or 1
    out = []
    for r in rows:
        now = int(r.get("mentions") or 0)
        day = int(r.get("mentions_24h_ago") or 0)
        rank = int(r.get("rank") or 0)
        rank_day = int(r.get("rank_24h_ago") or 0)
        out.append({
            "ticker": str(r.get("ticker") or "").upper(),
            "rank": rank,
            "mentions": now,
            "delta_24h": now - day,
            "growth_24h": round((now - day) / day, 4) if day else None,
            "rank_24h": (rank_day - rank) if rank_day else None,
            "share": round(now / total, 5),
            "upvotes": int(r.get("upvotes") or 0),
        })
    return out


def observe(client: httpx.Client) -> tuple[dict, dict]:
    """One reading of every board: the raw envelopes, and the derived series."""
    known_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    raw: dict[str, dict] = {}
    boards: dict[str, list[dict]] = {}
    meta: dict[str, dict] = {}

    for board in BOARDS:
        try:
            envelope = fetch_board(board, client)
        except Exception as exc:
            meta[board] = {"error": type(exc).__name__}
            continue
        raw[board] = envelope
        if envelope["results"]:
            boards[board] = derive(envelope)
        meta[board] = {"count": envelope["count"], "pages": envelope["pages"],
                       "kept": envelope["kept"]}
        time.sleep(0.4)

    return ({"known_at": known_at, "boards": raw},
            {"known_at": known_at, "boards": boards, "meta": meta})


def enrich(obs: dict, previous: dict | None) -> dict:
    """Fields that need the previous reading: rank movement since we last
    looked, first appearance, and how much of each board turned over."""
    for board, rows in obs["boards"].items():
        prev = {r["ticker"]: r for r in (previous or {}).get("boards", {}).get(board, [])}
        for row in rows:
            before = prev.get(row["ticker"])
            row["rank_change"] = (before["rank"] - row["rank"]) if before else None
            row["new_entry"] = row["ticker"] not in prev if prev else None
        if prev:
            now_set = {r["ticker"] for r in rows}
            obs.setdefault("churn", {})[board] = round(
                len(now_set - set(prev)) / max(len(now_set), 1), 4)
    return obs


def load_tape() -> list[dict]:
    if not os.path.exists(TAPE):
        return []
    with open(TAPE, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_raw(raw: dict) -> str:
    when = datetime.fromisoformat(raw["known_at"])
    folder = os.path.join(RAW, when.strftime("%Y-%m-%d"))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, when.strftime("%H%M") + ".json.gz")
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(raw, fh, separators=(",", ":"))
    return path


def write_site(tape: list[dict]) -> None:
    """A rolling window small enough for the page to fetch in one request."""
    window = []
    for obs in tape[-KEEP_HOURS:]:
        window.append({
            "known_at": obs["known_at"],
            "churn": {k: v for k, v in (obs.get("churn") or {}).items() if k in SITE_BOARDS},
            "boards": {b: obs["boards"].get(b, [])[:25] for b in SITE_BOARDS},
        })
    os.makedirs(os.path.dirname(SERIES), exist_ok=True)
    with open(SERIES, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "observations": window}, fh, separators=(",", ":"))
    with open(LATEST, "w", encoding="utf-8") as fh:
        json.dump(window[-1] if window else {}, fh, indent=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tape = load_tape()
    with httpx.Client(follow_redirects=True) as client:
        raw, obs = observe(client)
    obs = enrich(obs, tape[-1] if tape else None)

    kept = sum(len(v) for v in obs["boards"].values())
    top = obs["boards"].get("all-stocks", [])[:5]
    print(f"{obs['known_at']}  {len(obs['boards'])} boards, {kept} rows  " +
          "  ".join(f"{r['ticker']}:{r['mentions']}" for r in top))

    if args.dry_run:
        print(json.dumps(obs["meta"], indent=1))
        return

    print(f"  raw -> {os.path.relpath(write_raw(raw), ROOT)}")
    os.makedirs(os.path.dirname(TAPE), exist_ok=True)
    with open(TAPE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obs, separators=(",", ":")) + "\n")
    tape.append(obs)
    write_site(tape)
    print(f"  tape now {len(tape)} observations ({len(tape) / 24:.1f} days)")


if __name__ == "__main__":
    main()
