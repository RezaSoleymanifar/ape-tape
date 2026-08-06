"""Read the ApeWisdom boards and append what they said.

ApeWisdom publishes the current snapshot with no history endpoint, so a time
series only exists if something records one. This does that, hourly.

Stored are numbers computed from the board — ranks, deltas, share, movement,
churn — rather than copies of their rows, since the API states no
redistribution terms. Posts are not stored.

Each observation carries `known_at`: when we read it, not when the posts were
written.

    python collect.py                 # append one observation
    python collect.py --dry-run       # print it, write nothing
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime, timezone

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
TAPE = os.path.join(ROOT, "data", "tape.jsonl")
LATEST = os.path.join(ROOT, "docs", "latest.json")
SERIES = os.path.join(ROOT, "docs", "series.json")

FEEDS = {
    "stocks": "https://apewisdom.io/api/v1.0/filter/all-stocks/page/1",
    "crypto": "https://apewisdom.io/api/v1.0/filter/all-crypto/page/1",
}
DEPTH = 25                      # how far down each board we keep
KEEP_HOURS = 24 * 90            # the site reads a rolling quarter


def fetch(url: str, client: httpx.Client) -> list[dict]:
    r = client.get(url, timeout=45, headers={"User-Agent": "ape-tape (github)"})
    r.raise_for_status()
    return r.json().get("results", [])[:DEPTH]


def observe(client: httpx.Client) -> dict:
    """One reading of each board, reduced to numbers we computed."""
    known_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    boards: dict[str, list[dict]] = {}

    for board, url in FEEDS.items():
        rows = fetch(url, client)
        total = sum(int(r.get("mentions") or 0) for r in rows) or 1
        out = []
        for r in rows:
            now = int(r.get("mentions") or 0)
            day = int(r.get("mentions_24h_ago") or 0)
            out.append({
                "ticker": str(r.get("ticker") or "").upper(),
                "rank": int(r.get("rank") or 0),
                "mentions": now,
                # velocity: how fast attention is arriving, not how much sits there
                "delta_24h": now - day,
                "growth_24h": round((now - day) / day, 4) if day else None,
                # share of the board's total attention — comparable across days
                "share": round(now / total, 5),
                "upvotes": int(r.get("upvotes") or 0),
            })
        boards[board] = out

    return {"known_at": known_at, "boards": boards}


def enrich(obs: dict, previous: dict | None) -> dict:
    """Fields that need the previous reading: rank movement, first appearance,
    and how much of the board turned over."""
    for board, rows in obs["boards"].items():
        prev_rows = {r["ticker"]: r for r in (previous or {}).get("boards", {}).get(board, [])}
        seen_before = set(prev_rows)
        for row in rows:
            before = prev_rows.get(row["ticker"])
            row["rank_change"] = (before["rank"] - row["rank"]) if before else None
            row["new_entry"] = row["ticker"] not in seen_before if previous else None
        if previous:
            now_set = {r["ticker"] for r in rows}
            obs.setdefault("churn", {})[board] = round(
                len(now_set - seen_before) / max(len(now_set), 1), 4)
    return obs


def load_tape() -> list[dict]:
    if not os.path.exists(TAPE):
        return []
    with open(TAPE, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_site(tape: list[dict]) -> None:
    """A rolling window the page can fetch in one request, plus the latest read."""
    window = tape[-KEEP_HOURS:]
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
        obs = enrich(observe(client), tape[-1] if tape else None)

    top = obs["boards"]["stocks"][:5]
    print(f"{obs['known_at']}  " + "  ".join(
        f"{r['ticker']}:{r['mentions']}" for r in top))

    if args.dry_run:
        print(json.dumps(obs, indent=1)[:900])
        return

    os.makedirs(os.path.dirname(TAPE), exist_ok=True)
    with open(TAPE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obs, separators=(",", ":")) + "\n")
    tape.append(obs)
    write_site(tape)
    print(f"tape now {len(tape)} observations ({(len(tape) / 24):.1f} days)")


if __name__ == "__main__":
    main()
