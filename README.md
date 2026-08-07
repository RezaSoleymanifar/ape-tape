# ape-tape

A record of the [ApeWisdom](https://apewisdom.io) boards, read on the hour.

**[The tape →](https://rezasoleymanifar.github.io/ape-tape/)**

## What it is

ApeWisdom counts how often each **ticker** is mentioned and upvoted across
Reddit's finance communities, and publishes only the current snapshot, with no
history endpoint. This reads it on the hour and keeps what it read, so a series
exists.

Nine boards are recorded: seven subreddits, plus ApeWisdom's own `all-stocks`
and `all-crypto` aggregates, which span more subreddits than the seven and are
not derivable from them.

It is not Reddit data. There are no posts, comments, users, subreddit
statistics or topics, and none are stored. Ticker matching is ApeWisdom's and
their method is unpublished, so common-word symbols (`OPEN`, `IT`, `ALL`) and
private companies do appear in the counts.

| | |
|---|---|
| Aggregates | `all-stocks`, `all-crypto` |
| Subreddits | `wallstreetbets`, `stocks`, `cryptocurrency`, `options`, `investing`, `stockmarket`, `pennystocks` |

The top 100 rows of each, once an hour. All nine are on the page, not just the
aggregates. A board reports its full `count` too, so `all-stocks` naming 951
tickers while 100 are kept is recorded rather than hidden.

## What is stored

Two things: the rows each board returned, and figures computed from them. The
computed ones are marked.

| Field | Meaning |
|---|---|
| `ticker`, `rank`, `mentions`, `upvotes` | as returned, at the moment we read it |
| `delta_24h`, `growth_24h` | *computed*, against the 24h-ago figure the API returns |
| `share` | *computed*, the ticker's fraction of the mentions in the rows kept |
| `rank_24h` | rank movement against the 24h-ago figure the API returns |
| `rank_change` | rank movement since our own previous reading |
| `new_entry` | not in the rows we kept at the previous reading |
| `churn` | per board, the fraction of kept rows that turned over since then |

`share` is a ticker's fraction of the mentions **across the 100 rows kept**,
not across every ticker the board saw. It says how much of the front page a
name is taking. Each reading also carries a `meta` block giving every board's
`count`, `pages` and `kept`, so the truncation is visible rather than silent.

Each reading carries `known_at`, when we read it, not when the posts were
written, and it applies to every row in that reading. The posts themselves are
not stored.

## Missed hours are recorded as missed

Hours get dropped. A scheduler skips a tick, a job fails, the API is down. So
`rank_change`, `new_entry` and `churn` are differences against the previous
reading, and the previous reading is not always an hour back.

Each reading therefore carries `previous_known_at` and `gap_hours`, the real
distance to whatever it was compared against. The page columns are readings,
and a missed hour has no column and is drawn as no reading rather than as a
quiet one; the header counts how many hours are absent. A day-long outage is
not allowed to pass for a calm day.

Readings taken before those two fields existed do not have them. There are
four such lines, at the very start of the tape, and they are left as they were
rather than backfilled with a guess.

If no board answers at all, nothing is written. A gap in the tape is honest; a
row of zeroes is not.

## Running it

```bash
pip install httpx
python collect.py              # append one reading
python collect.py --dry-run    # print it, write nothing
python collect.py --min-gap 45 # do nothing if the last reading is newer
```

GitHub Actions is scheduled on two crons an hour, at `:07` and `:37`, and the
second does nothing if the first worked.

**Cadence is aspiration, not a guarantee, and the record says which.** GitHub
queues cron best-effort and drops ticks; over this repository's first day not
one scheduled run fired, and every reading so far was triggered by hand. The
two crons exist to give each hour two chances. Whether they land is visible on
the page, which counts the missed hours rather than smoothing them away, so do
not read "hourly" as a promise about any particular hour.

```
collect.py                the recorder
data/raw/YYYY-MM-DD/…     each board's response, gzipped, one file per reading
data/tape/YYYY-MM.jsonl   derived statistics, one line per reading, one file a month
data/tape/counts.json     readings per month, so a run need not recount the archive
docs/                     the page, plus the two JSON files it reads
```

Raw is kept because a derived series can be rebuilt from raw and raw can never
be rebuilt from derived. If the delta formula here is wrong, or the API gains a
field, recomputation is only possible if the original rows survived.

To be exact about what "raw" means here: it is every field of the top 100 rows
each board returned, unaltered, wrapped with that board's `count` and `pages`.
It is not the HTTP response byte for byte, and it is not the whole board. Rows
past 100 are never fetched and are gone.

### Size

A reading is about 15 KB of gzipped raw and about 135 KB of derived JSON, so a
full year is roughly **1.3 GB**, most of it the derived tape. That is the real
number: an earlier version of this file said 18 MB, which was wrong by a factor
of seventy.

The tape is sharded a month per file and each run reads only the tail, so a run
does not get slower as the archive grows. A full month still reaches about
96 MB, which is under GitHub's 100 MB per-file limit but not by much, and is
the next thing that will have to change. If the repository ever needs to
shrink, the tape is the part to drop: every field in it is recomputable from
the raw rows.

## Terms

ApeWisdom's API page states no licence or redistribution policy. This project
credits them on every page and links to them as the source.

Both the raw rows and the derived statistics are public, in `data/`. The raw
rows are theirs, kept because an archive that cannot be recomputed is not an
archive. If ApeWisdom would rather this were not mirrored, open an issue and it
comes down.

Data is unverified and may be wrong, may be missing hours, and reflects
whatever the API returned at the time. Not investment advice.

## Related

[Vintage](https://github.com/RezaSoleymanifar/vintage), point-in-time market
data, where `known_at` is the index this joins against.

MIT licensed.
