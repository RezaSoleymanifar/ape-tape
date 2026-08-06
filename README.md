# ape-tape

An hourly record of the [ApeWisdom](https://apewisdom.io) boards.

**[The tape →](https://rezasoleymanifar.github.io/ape-tape/)**

## What it is

ApeWisdom counts how often each **ticker** is mentioned and upvoted on nine
finance subreddits, and publishes only the current snapshot — no history
endpoint. This reads it hourly and keeps what it read, so a series exists.

It is not Reddit data. There are no posts, comments, users, subreddit
statistics or topics, and none are stored. Ticker matching is ApeWisdom's and
their method is unpublished, so common-word symbols (`OPEN`, `IT`, `ALL`) and
private companies do appear in the counts.

Boards recorded: `all-stocks`, `all-crypto`, `wallstreetbets`, `stocks`,
`cryptocurrency`, `options`, `investing`, `stockmarket`, `pennystocks` —
top 100 of each, hourly.

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
data/raw/…            the responses as received, gzipped, one file per hour
data/tape.jsonl       derived statistics, one line per reading
docs/                 the page, plus the JSON it reads
```

Raw is kept because a derived series can be rebuilt from raw and raw can never
be rebuilt from derived. If the delta formula here is wrong, or the API gains a
field, recomputation is only possible if the original bytes survived.

## Terms

ApeWisdom's API page states no licence or redistribution policy, so this stores
derived statistics rather than copies of their rows, and credits them.

Data is unverified and may be wrong, missing hours, or reflect whatever the API
returned at the time. Not investment advice.

## Related

[Vintage](https://github.com/RezaSoleymanifar/vintage) — point-in-time market
data, where `known_at` is the index this joins against.

MIT licensed.
