"""Read the ApeWisdom boards and append what they said.

ApeWisdom publishes the current snapshot with no history endpoint, so a time
series only exists if something records one. This does that, hourly.

Two things are written every hour:

  data/raw/YYYY-MM-DD/HHMM.json.gz  the responses as received, gzipped
  data/tape/YYYY-MM.jsonl           derived statistics, one line per reading

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

Hours get missed. A scheduler can drop a tick, a run can fail, the API can be
down. So every observation also carries `previous_known_at` and `gap_hours`,
the real distance back to the reading it was compared against. Without those,
`rank_change` and `churn` read as "since an hour ago" when they may mean "since
yesterday", and the chart draws a day-long gap as though it were an hour.

    python collect.py                 # append one observation
    python collect.py --dry-run       # print it, write nothing
    python collect.py --min-gap 45    # skip if the last reading is newer than that
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
TAPE = os.path.join(ROOT, "data", "tape")          # one .jsonl file per month
LEGACY_TAPE = os.path.join(ROOT, "data", "tape.jsonl")
RAW = os.path.join(ROOT, "data", "raw")
LATEST = os.path.join(ROOT, "docs", "latest.json")
SERIES = os.path.join(ROOT, "docs", "series.json")

API = "https://apewisdom.io/api/v1.0/filter/{board}/page/{page}"

# Every board the API answers for. The per-subreddit ones are not derivable
# from the aggregate, wallstreetbets moving alone is a different fact from the
# whole board moving, so each is recorded separately.
BOARDS = [
    "all-stocks", "all-crypto", "wallstreetbets", "stocks", "cryptocurrency",
    "options", "investing", "stockmarket", "pennystocks",
]

DEPTH = 100                 # rows kept per board, which is one page of the API
PER_PAGE = 100              # what one page returns
KEEP_HOURS = 24 * 30        # the rolling window the site reads
SITE_DEPTH = 25             # rows per board in the snapshot the page tables
CHART_DEPTH = 16            # rows per board in the series the page charts

# Every board is charted, not two of them. Nine were being recorded and seven
# were never shown anywhere, which made the archive larger than the product.
SITE_BOARDS = tuple(BOARDS)

# Longer than this since the previous reading and the hour was missed. The
# comparisons still work, they just describe a wider gap, and saying so is the
# difference between a series and a guess.
EXPECTED_GAP_HOURS = 1.0


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


def gap_hours(now_iso: str, previous_iso: str | None) -> float | None:
    """Real hours back to the reading everything here is compared against."""
    if not previous_iso:
        return None
    delta = datetime.fromisoformat(now_iso) - datetime.fromisoformat(previous_iso)
    return round(delta.total_seconds() / 3600, 3)


def enrich(obs: dict, previous: dict | None) -> dict:
    """Fields that need the previous reading: rank movement since we last
    looked, first appearance, and how much of each board turned over.

    All three are differences against whatever the previous reading was, and
    the previous reading is not always an hour old. `gap_hours` says how old,
    so a consumer can tell an hourly move from a daily one.
    """
    obs["previous_known_at"] = (previous or {}).get("known_at")
    obs["gap_hours"] = gap_hours(obs["known_at"], obs["previous_known_at"])

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


def tape_files() -> list[str]:
    """Every month's file, oldest first, with the pre-sharding file ahead."""
    found = []
    if os.path.exists(LEGACY_TAPE):
        found.append(LEGACY_TAPE)
    if os.path.isdir(TAPE):
        found += [os.path.join(TAPE, n) for n in sorted(os.listdir(TAPE))
                  if n.endswith(".jsonl")]
    return found


def tape_path(known_at: str) -> str:
    """The month a reading belongs in.

    One file per month rather than one growing forever. A full reading is
    about 135 KB, so an unsharded tape passes a gigabyte inside a year: too
    large to open, too large to diff, and read in full by every hourly run.
    """
    return os.path.join(TAPE, known_at[:7] + ".jsonl")


def load_tape(limit: int = KEEP_HOURS) -> list[dict]:
    """The most recent `limit` readings.

    Only the tail is ever needed: the newest reading to compare against, and
    the site window to publish. Reading the whole archive to append one line
    would make every run slower than the last.
    """
    out: list[dict] = []
    for path in reversed(tape_files()):
        with open(path, encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()]
        out = [json.loads(ln) for ln in lines[-(limit - len(out)):]] + out
        if len(out) >= limit:
            break
    return out


def total_readings() -> int:
    """How many readings exist in all, which the tail does not tell us."""
    n = 0
    for path in tape_files():
        with open(path, encoding="utf-8") as fh:
            n += sum(1 for ln in fh if ln.strip())
    return n


def write_raw(raw: dict) -> str:
    when = datetime.fromisoformat(raw["known_at"])
    folder = os.path.join(RAW, when.strftime("%Y-%m-%d"))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, when.strftime("%H%M") + ".json.gz")
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(raw, fh, separators=(",", ":"))
    return path


def write_site(tape: list[dict], readings: int, first_known_at: str | None) -> None:
    """Two files, split by what the page actually needs from each.

    The chart wants a few numbers per ticker per hour, for a long stretch of
    hours. The tables want every field, for the newest hour only. Writing both
    needs into one file was costing about 8 KB an hour, which reaches roughly
    17 MB across ninety days and is then downloaded whole on every visit.

    So `series.json` carries only what a chart can be drawn from, and
    `latest.json` carries the full newest reading.

    The page lets a reader move a playhead back through the window and asks
    what the board looked like at that hour, so the series has to answer for
    every hour and not only the last one. Ranks alone could not: rank says the
    order but not the distance, and "gaining attention" is a change in size,
    not in position. Share is the size.

    Three compressions keep that affordable:

      * tickers are written once into a dictionary and referenced by index,
        because the same forty or so symbols repeat across seven hundred hours
      * rank is the position in the list, so it is not stored at all
      * mentions are not stored per ticker, only the board's total, since
        `share` already carries the fraction and the product recovers the count

    That is roughly 1 KB an hour for nine boards, against about 8 KB for the
    same information written plainly.
    """
    window = []
    dictionary: list[str] = []
    index: dict[str, int] = {}

    for obs in tape[-KEEP_HOURS:]:
        boards = obs.get("boards") or {}
        if not boards:
            continue                    # a failed reading charts as a gap
        packed = {}
        for b in SITE_BOARDS:
            rows = boards.get(b)
            if not rows:
                continue
            rows = sorted(rows, key=lambda r: r["rank"])[:CHART_DEPTH]
            keys, shares = [], []
            for r in rows:
                tk = r["ticker"]
                if tk not in index:
                    index[tk] = len(dictionary)
                    dictionary.append(tk)
                keys.append(index[tk])
                # share in hundred-thousandths, the precision `derive` rounds to
                shares.append(round((r.get("share") or 0) * 100000))
            packed[b] = {
                "k": keys,
                "s": shares,
                # Total mentions across every row we kept, which is the
                # denominator `share` was divided by. mentions = share * m.
                "m": sum(int(r.get("mentions") or 0) for r in boards.get(b, [])),
            }
        window.append({
            "t": obs["known_at"],
            "gap": obs.get("gap_hours"),
            "b": packed,
            "c": {k: v for k, v in (obs.get("churn") or {}).items()
                  if k in SITE_BOARDS},
        })

    os.makedirs(os.path.dirname(SERIES), exist_ok=True)
    with open(SERIES, "w", encoding="utf-8") as fh:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "window_hours": KEEP_HOURS,
            "expected_gap_hours": EXPECTED_GAP_HOURS,
            "depth": CHART_DEPTH,
            "boards": list(SITE_BOARDS),
            "tickers": dictionary,
            "observations": window,
        }, fh, separators=(",", ":"))

    newest = tape[-1] if tape else {}
    with open(LATEST, "w", encoding="utf-8") as fh:
        json.dump({
            "known_at": newest.get("known_at"),
            "previous_known_at": newest.get("previous_known_at"),
            "gap_hours": newest.get("gap_hours"),
            "readings": readings,
            "first_known_at": first_known_at,
            "churn": newest.get("churn") or {},
            "meta": newest.get("meta") or {},
            "boards": {b: (newest.get("boards") or {}).get(b, [])[:SITE_DEPTH]
                       for b in SITE_BOARDS},
        }, fh, separators=(",", ":"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-gap", type=float, default=0.0, metavar="MINUTES",
                    help="do nothing if the last reading is newer than this. "
                         "Lets the job be scheduled more than once an hour "
                         "without recording more than once an hour.")
    args = ap.parse_args()

    tape = load_tape()
    previous = tape[-1] if tape else None

    if args.min_gap and previous:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(previous["known_at"])
        if age < timedelta(minutes=args.min_gap):
            print(f"last reading is {age.total_seconds() / 60:.0f} min old, "
                  f"under the {args.min_gap:.0f} min floor. Nothing to do.")
            return 0

    with httpx.Client(follow_redirects=True) as client:
        raw, obs = observe(client)
    obs = enrich(obs, previous)

    # Every board failed. Appending this would put a line on the tape with no
    # numbers behind it and overwrite the page's data with blanks, which reads
    # as "the boards went quiet" rather than "we could not reach them".
    if not obs["boards"]:
        print("no board answered. Recording nothing, so the gap stays visible.",
              file=sys.stderr)
        print(json.dumps(obs["meta"], indent=1), file=sys.stderr)
        return 1

    kept = sum(len(v) for v in obs["boards"].values())
    failed = [b for b, m in obs["meta"].items() if "error" in m]
    top = obs["boards"].get("all-stocks", [])[:5]
    gap = obs["gap_hours"]
    print(f"{obs['known_at']}  {len(obs['boards'])} boards, {kept} rows  " +
          "  ".join(f"{r['ticker']}:{r['mentions']}" for r in top))
    print(f"  gap since previous: "
          + ("first reading" if gap is None else f"{gap:.2f}h")
          + (f"  MISSED {round(gap - EXPECTED_GAP_HOURS)} hour(s)"
             if gap and gap >= EXPECTED_GAP_HOURS * 2 else ""))
    if failed:
        print(f"  boards that did not answer: {', '.join(failed)}")

    if args.dry_run:
        print(json.dumps(obs["meta"], indent=1))
        return 0

    print(f"  raw -> {os.path.relpath(write_raw(raw), ROOT)}")
    shard = tape_path(obs["known_at"])
    os.makedirs(TAPE, exist_ok=True)
    with open(shard, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obs, separators=(",", ":")) + "\n")
    print(f"  tape -> {os.path.relpath(shard, ROOT)}")

    tape.append(obs)
    first = tape_files()
    with open(first[0], encoding="utf-8") as fh:
        first_at = json.loads(fh.readline())["known_at"]
    readings = total_readings()
    write_site(tape[-KEEP_HOURS:], readings, first_at)
    print(f"  tape now {readings} readings ({readings / 24:.1f} days)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
