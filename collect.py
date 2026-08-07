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
SERIES = os.path.join(ROOT, "docs", "series.json")   # the index of the boards
SERIES_DIR = os.path.join(ROOT, "docs", "series")    # one file per board

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


MAX_PAGES = 4               # DEPTH needs one, so four is slack, not a budget
RETRIES = 3                 # a public endpoint throttles; one 429 is not news
BACKOFF = (1.5, 5.0, 15.0)  # seconds before each retry


def get_page(board: str, page: int, client: httpx.Client) -> dict:
    """One page, retried through the failures that pass on their own.

    A single 429 or 503 used to drop that board for the hour, permanently,
    because there is no history endpoint to go back and fill it in from. Rate
    limits and gateway errors are the normal weather on a free public API, so
    they get three attempts with a widening pause. A 404 or a 400 is a real
    answer and is not retried.
    """
    url = API.format(board=board, page=page)
    headers = {"User-Agent": "ape-tape (github.com/RezaSoleymanifar/ape-tape)"}
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = client.get(url, timeout=45, headers=headers)
            if r.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError(f"{r.status_code} on {board} page {page}",
                                            request=r.request, response=r)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPStatusError, httpx.TransportError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and status not in (429, 500, 502, 503, 504):
                raise
            last = exc
            if attempt + 1 < RETRIES:
                time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
    raise last                                          # type: ignore[misc]


def fetch_board(board: str, client: httpx.Client) -> dict:
    """The top DEPTH rows, with the envelope kept so truncation is visible.

    DEPTH equals PER_PAGE today, so one page is normally the whole job and the
    loop below does not run twice. It stays because the page size is theirs to
    change, and a board that starts answering fifty at a time should still come
    back with a hundred rather than silently halve the record.
    """
    rows: list[dict] = []
    count = pages = 0
    page = 1
    # Bounded. `pages` comes from the server, and an unbounded loop against it
    # can outrun the job's ten minute cap and lose the hour entirely.
    while len(rows) < DEPTH and page <= MAX_PAGES:
        body = get_page(board, page, client)
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
        envelope = None
        try:
            envelope = fetch_board(board, client)
            raw[board] = envelope           # kept even if deriving then fails
            if envelope["results"]:
                boards[board] = derive(envelope)
            meta[board] = {"count": envelope["count"], "pages": envelope["pages"],
                           "kept": envelope["kept"]}
        except Exception as exc:
            # `derive` used to sit outside this. One malformed row, a thousands
            # separator in a mentions field, and the exception escaped before
            # anything was written, throwing away all nine boards' raw
            # responses that were already fetched and in memory. Now the raw
            # envelope is kept whatever the deriving does, and a board that
            # cannot be derived is a named failure rather than a lost hour.
            meta[board] = {"error": type(exc).__name__, "detail": str(exc)[:200]}
            if envelope is not None:
                meta[board]["raw_kept"] = True
        time.sleep(0.4)

    return ({"known_at": known_at, "boards": raw},
            {"known_at": known_at, "boards": boards, "meta": meta})


def when(iso: str) -> datetime:
    """A stored timestamp, always UTC-aware.

    `fromisoformat` accepts a naive string without complaint, and subtracting
    one from an aware one raises. A single hand-edited line without its offset
    would otherwise stop the collector on every run from then on, so anything
    without a timezone is read as UTC, which is what this writes.
    """
    parsed = datetime.fromisoformat(iso)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def gap_hours(now_iso: str, previous_iso: str | None) -> float | None:
    """Real hours back to the reading everything here is compared against."""
    if not previous_iso:
        return None
    return round((when(now_iso) - when(previous_iso)).total_seconds() / 3600, 3)


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


def tail_lines(path: str, limit: int) -> list[str]:
    """The last `limit` non-blank lines, without holding the file in memory.

    A month of readings is around a hundred megabytes. Materialising all of it
    to take the last few would undo the point of sharding, so this walks
    backwards from the end in blocks and stops as soon as it has enough.
    """
    if limit <= 0:
        return []
    block, data, found = 262144, b"", 0
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        while pos > 0 and found <= limit:
            step = min(block, pos)
            pos -= step
            fh.seek(pos)
            data = fh.read(step) + data
            found = data.count(b"\n")
    lines = data.decode("utf-8").splitlines()
    return [ln for ln in lines if ln.strip()][-limit:]


def load_tape(limit: int = KEEP_HOURS) -> list[dict]:
    """The most recent `limit` readings.

    Only the tail is ever needed: the newest reading to compare against, and
    the site window to publish. Reading the whole archive to append one line
    would make every run slower than the last.
    """
    if limit <= 0:
        return []                       # a negative slice would return the lot
    out: list[dict] = []
    for path in reversed(tape_files()):
        want = limit - len(out)
        if want <= 0:
            break
        out = [json.loads(ln) for ln in tail_lines(path, want)] + out
    return out


def count_path() -> str:
    return os.path.join(TAPE, "counts.json")


def count_lines(path: str) -> int:
    """Newlines, read in blocks, without decoding a hundred megabytes."""
    n, block = 0, 1 << 20
    with open(path, "rb") as fh:
        while chunk := fh.read(block):
            n += chunk.count(b"\n")
        fh.seek(0, os.SEEK_END)
        if fh.tell():                       # a last line with no trailing \n
            fh.seek(fh.tell() - 1)
            if fh.read(1) != b"\n":
                n += 1
    return n


def total_readings() -> int:
    """How many readings exist in all, which the tail does not tell us.

    Closed months never change, so they are counted once and remembered in a
    small index beside the shards. Only the month being written to is counted
    again. Reading every line of every month on every run was the last thing
    here that got slower as the archive grew.
    """
    path = count_path()
    counts: dict[str, int] = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                counts = json.load(fh)
        except (ValueError, OSError):
            counts = {}

    live = datetime.now(timezone.utc).strftime("%Y-%m")
    fresh: dict[str, int] = {}
    for file in tape_files():
        key = os.path.basename(file).rsplit(".jsonl", 1)[0]
        fresh[key] = (counts[key] if key in counts and key != live
                      else count_lines(file))

    if fresh != counts:
        os.makedirs(TAPE, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(fresh, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    return sum(fresh.values())


def first_reading_at() -> str | None:
    """When the archive starts, from the oldest month's first line."""
    files = tape_files()
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        return json.loads(line)["known_at"]
        except (ValueError, OSError, KeyError):
            continue                    # a damaged oldest file is not fatal
    return None


def write_raw(raw: dict) -> str:
    """The response bytes, one file per reading.

    Named to the second. Two runs inside the same minute used to append two
    tape lines over a single raw file, leaving a reading with nothing behind
    it and a guard that could not see the difference.
    """
    at = when(raw["known_at"])
    folder = os.path.join(RAW, at.strftime("%Y-%m-%d"))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, at.strftime("%H%M%S") + ".json.gz")
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

    And the series is written one file per board rather than one file for all
    nine, because the page charts one board at a time. A reader who never leaves
    all-stocks was downloading the eight boards they did not look at: a month of
    everything is well over a megabyte, and a month of one board is a tenth of
    that. `series.json` is now an index naming the boards and how many readings
    each has; `series/<board>.json` is the board.

    That is roughly 150 bytes an hour for the board being read, against about
    8 KB for the same information written plainly for all nine.
    """
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    window = tape[-KEEP_HOURS:]
    os.makedirs(SERIES_DIR, exist_ok=True)

    counts: dict[str, int] = {}
    for b in SITE_BOARDS:
        dictionary: list[str] = []
        index: dict[str, int] = {}
        observations = []

        for obs in window:
            rows = (obs.get("boards") or {}).get(b)
            if not rows:
                continue        # the board did not answer, and a gap is honest
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
            observations.append({
                "t": obs["known_at"],
                "gap": obs.get("gap_hours"),
                "k": keys,
                "s": shares,
                # Total mentions across every row kept for this board, which is
                # the denominator `share` was divided by. mentions = share * m.
                "m": sum(int(x.get("mentions") or 0)
                         for x in (obs.get("boards") or {})[b]),
                "c": (obs.get("churn") or {}).get(b),
            })

        path = os.path.join(SERIES_DIR, b + ".json")
        if not observations:
            # Nothing recorded on this board inside the window. Leaving the last
            # file behind would publish a month-old board as though it were
            # current, so it goes.
            if os.path.exists(path):
                os.remove(path)
            continue
        counts[b] = len(observations)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({
                "board": b,
                "generated_at": generated,
                "expected_gap_hours": EXPECTED_GAP_HOURS,
                "depth": CHART_DEPTH,
                "tickers": dictionary,
                "observations": observations,
            }, fh, separators=(",", ":"))

    with open(SERIES, "w", encoding="utf-8") as fh:
        json.dump({
            "generated_at": generated,
            # Readings, not hours. Hours get missed, so 720 readings can span
            # more than 720 hours, and calling it `window_hours` said otherwise.
            "window_readings": KEEP_HOURS,
            "expected_gap_hours": EXPECTED_GAP_HOURS,
            "depth": CHART_DEPTH,
            "path": "series/{board}.json",
            "boards": [b for b in SITE_BOARDS if b in counts],
            "readings": counts,
        }, fh, separators=(",", ":"))

    newest = tape[-1] if tape else {}
    newest_boards = newest.get("boards") or {}
    with open(LATEST, "w", encoding="utf-8") as fh:
        json.dump({
            "known_at": newest.get("known_at"),
            "previous_known_at": newest.get("previous_known_at"),
            "gap_hours": newest.get("gap_hours"),
            "readings": readings,
            "first_known_at": first_known_at,
            "churn": newest.get("churn") or {},
            "meta": newest.get("meta") or {},
            "scale": scale_of(newest_boards, readings),
            # Only boards that actually answered. Publishing an empty list for
            # a board that failed made the page read "this board went quiet"
            # when the truth was "we could not reach it".
            "boards": {b: rows[:SITE_DEPTH]
                       for b, rows in newest_boards.items() if rows},
        }, fh, separators=(",", ":"))


def dir_bytes(path: str) -> int:
    total = 0
    for root, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def scale_of(boards: dict, readings: int) -> dict:
    """What a reader needs to judge how much is behind the page.

    Counts from the newest reading, plus the size of the archive on disk. A
    dashboard that shows a number without saying how much was counted to get
    it is asking to be taken on trust.
    """
    rows = [r for rows in boards.values() for r in rows]
    return {
        "boards_read": len(boards),
        "boards_possible": len(SITE_BOARDS),
        "rows": len(rows),
        "depth_per_board": DEPTH,
        "tickers": len({r["ticker"] for r in rows}),
        "mentions": sum(int(r.get("mentions") or 0) for r in rows),
        "upvotes": sum(int(r.get("upvotes") or 0) for r in rows),
        "readings": readings,
        "archive_bytes": dir_bytes(os.path.join(ROOT, "data")),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-gap", type=float, default=0.0, metavar="MINUTES",
                    help="do nothing if the last reading is newer than this. "
                         "Lets the job be scheduled more than once an hour "
                         "without recording more than once an hour.")
    ap.add_argument("--rebuild", action="store_true",
                    help="rewrite docs/ from the tape already on disk and stop. "
                         "The published files are derived, so a change to their "
                         "shape should not have to wait for the next hour or "
                         "spend a request to get one.")
    args = ap.parse_args()

    tape = load_tape()
    previous = tape[-1] if tape else None

    if args.rebuild:
        if not tape:
            print("no tape to rebuild from.", file=sys.stderr)
            return 1
        if args.dry_run:
            print(f"would rebuild docs from {total_readings()} readings. "
                  f"--dry-run writes nothing.")
            return 0
        first_at = first_reading_at()
        readings = total_readings()
        write_site(tape[-KEEP_HOURS:], readings, first_at)
        print(f"docs rebuilt from {readings} readings on disk, nothing fetched.")
        return 0

    if args.min_gap and previous:
        age = datetime.now(timezone.utc) - when(previous["known_at"])
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

    # Past this point the reading is on disk and must be committed. Anything
    # that throws here would exit non-zero, the workflow would skip its commit
    # step, and the runner would be destroyed with the only copy on it. The
    # published files are derived and can be rebuilt with --rebuild, so a
    # failure to write them is reported and survived, not raised.
    tape.append(obs)
    try:
        readings = total_readings()
        write_site(tape[-KEEP_HOURS:], readings, first_reading_at())
        span = gap_hours(obs["known_at"], first_reading_at())
        print(f"  tape now {readings} readings"
              + (f" across {span / 24:.1f} days" if span else ""))
    except Exception as exc:                    # noqa: BLE001
        print(f"  docs not rewritten ({type(exc).__name__}: {exc}). "
              f"The reading is safe on the tape; run --rebuild to catch up.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
