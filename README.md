# ape-tape

An hourly record of the [ApeWisdom](https://apewisdom.io) boards.

**[The tape →](https://rezasoleymanifar.github.io/ape-tape/)**

## What it is

ApeWisdom publishes the current snapshot and nothing else — no history endpoint.
This reads it every hour and keeps what it read, so a time series exists.

That is the whole thing. It is metadata about Reddit mention counts, recorded on
a schedule.

## What is stored

Numbers we computed from the board, not the board itself:

| Field | Meaning |
|---|---|
| `rank`, `mentions`, `upvotes` | board position when we read it |
| `delta_24h`, `growth_24h` | change against the 24h-ago figure the API returns |
| `share` | the ticker's fraction of the board's mentions |
| `rank_change` | movement since our previous reading |
| `new_entry` | not on the board an hour ago |
| `churn` | fraction of the board that turned over |

Every row carries `known_at` — when we read it, not when the posts were written.
The posts themselves are not stored.

## Running it

```bash
pip install httpx
python collect.py              # append one observation
python collect.py --dry-run    # print it, write nothing
```

GitHub Actions runs it hourly and commits the result. About 18MB a year.

```
collect.py            the recorder
data/tape.jsonl       append-only, one observation per line
docs/                 the page, plus the JSON it reads
```

## Terms

ApeWisdom's API page states no licence or redistribution policy, so this stores
derived statistics rather than copies of their rows, and credits them.

Data is unverified and may be wrong, missing hours, or reflect whatever the API
returned at the time. Not investment advice.

## Related

[Vintage](https://github.com/RezaSoleymanifar/vintage) — point-in-time market
data, where `known_at` is the index this joins against.

MIT licensed.
