# ape-tape

An hourly record of the [ApeWisdom](https://apewisdom.io) boards.

**[The tape →](https://rezasoleymanifar.github.io/ape-tape/)**

## What it is

ApeWisdom counts how often each **ticker** is mentioned and upvoted on nine
finance subreddits, and publishes only the current snapshot, no history
endpoint. This reads it hourly and keeps what it read, so a series exists.

It is not Reddit data. There are no posts, comments, users, subreddit
statistics or topics, and none are stored. Ticker matching is ApeWisdom's and
their method is unpublished, so common-word symbols (`OPEN`, `IT`, `ALL`) and
private companies do appear in the counts.

Boards recorded: `all-stocks`, `all-crypto`, `wallstreetbets`, `stocks`,
`cryptocurrency`, `options`, `investing`, `stockmarket`, `pennystocks`.
Top 100 of each, hourly. All nine are on the page, not just the aggregates.

## What is stored

Numbers we computed from the board, not the board itself:

| Field | Meaning |
|---|---|
| `rank`, `mentions`, `upvotes` | board position when we read it |
| `delta_24h`, `growth_24h` | change against the 24h-ago figure the API returns |
| `share` | the ticker's fraction of the board's mentions |
| `rank_change` | movement since our previous reading |
| `new_entry` | not on the board at the previous reading |
| `churn` | fraction of the board that turned over since then |

Every row carries `known_at`, when we read it, not when the posts were written.
The posts themselves are not stored.

## Missed hours are recorded as missed

Hours get dropped. A scheduler skips a tick, a job fails, the API is down. So
`rank_change`, `new_entry` and `churn` are differences against the previous
reading, and the previous reading is not always an hour back.

Each reading therefore carries `previous_known_at` and `gap_hours`, the real
distance to whatever it was compared against. The chart plots against the clock
rather than against the reading count, so a missed stretch appears as a gap of
the right width, shaded, with the hours named. A day-long outage is not allowed
to look like a quiet hour.

If no board answers at all, nothing is written. A gap in the tape is honest; a
row of zeroes is not.

## Running it

```bash
pip install httpx
python collect.py              # append one reading
python collect.py --dry-run    # print it, write nothing
python collect.py --min-gap 45 # do nothing if the last reading is newer
```

GitHub Actions runs it on two crons an hour, at `:07` and `:37`. The second
does nothing if the first worked. GitHub queues cron on a best-effort basis and
drops ticks, and this repository once went nineteen hours without a single
scheduled run, so an hour that matters gets two chances.

```
collect.py               the recorder
data/raw/…               the responses as received, gzipped, one file per reading
data/tape/YYYY-MM.jsonl  derived statistics, one line per reading, one file a month
docs/                    the page, plus the two JSON files it reads
```

Raw is kept because a derived series can be rebuilt from raw and raw can never
be rebuilt from derived. If the delta formula here is wrong, or the API gains a
field, recomputation is only possible if the original bytes survived.

### Size

A reading is about 15 KB of gzipped raw and about 135 KB of derived JSON, so a
full year is roughly **1.3 GB**, most of it the derived tape. That is the real
number: an earlier version of this file said 18 MB, which was wrong by a factor
of seventy.

The tape is sharded a month per file so no single file grows without limit, and
each run reads only the tail. If the repository ever needs to shrink, the tape
is the part to drop: every field in it can be recomputed from raw.

## Terms

ApeWisdom's API page states no licence or redistribution policy. This project
credits them on every page and links to them as the source.

Both the raw responses and the derived statistics are public, in `data/`. Raw
is a copy of what their endpoint returned, kept because an archive that cannot
be recomputed is not an archive. If ApeWisdom would rather this were not
mirrored, open an issue and it comes down.

Data is unverified and may be wrong, may be missing hours, and reflects
whatever the API returned at the time. Not investment advice.

## Related

[Vintage](https://github.com/RezaSoleymanifar/vintage), point-in-time market
data, where `known_at` is the index this joins against.

MIT licensed.
