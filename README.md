# crowd-tape

An hourly recording of what retail forums are talking about, stamped with the
minute it was read and never revised.

**[See the tape →](https://rezasoleymanifar.github.io/crowd-tape/)**

## Why this exists

The source publishes only the present. There is no history endpoint, no archive,
no way to ask what last Tuesday's board looked like — and anyone selling years of
"historical sentiment" built it by re-scoring old posts with today's model, which
is a look-ahead machine wearing a timestamp.

So the history has to be recorded rather than bought. This repository starts the
hour it was begun and grows one observation at a time. That is the entire moat:
in a year it will hold something nobody can reconstruct, including someone with
more money.

## What is stored

Statistics we computed, not anyone else's table:

| Field | Meaning |
|---|---|
| `rank`, `mentions`, `upvotes` | the board position at the moment of reading |
| `delta_24h`, `growth_24h` | how fast attention is arriving, not how much sits there |
| `share` | the ticker's slice of the whole board, comparable across days |
| `rank_change` | movement since the previous hour — exists only because we kept it |
| `new_entry` | first appearance on the board in this recording |
| `churn` | what fraction of the board turned over since the last reading |

Every observation carries `known_at`: the minute we read it, **not** the minute
the posts were written. For a source with no history that is the only honest
stamp, and it is what lets this series survive a point-in-time backtest.

## How it runs

```bash
pip install httpx
python collect.py              # append one observation
python collect.py --dry-run    # print it, write nothing
```

A GitHub Actions cron runs it hourly and commits the result. The git history is
the provenance: you can prove when a number was recorded because the commit is
timestamped by someone other than us.

No servers, no S3, no AWS bill. The volume is roughly 18MB a year, which git
handles without noticing.

## Layout

```
collect.py                 the recorder
data/tape.jsonl            append-only, one observation per line
docs/index.html            the one-page view
docs/series.json           a rolling 90-day window the page reads
docs/latest.json           the most recent reading
.github/workflows/         hourly cron
```

## Terms

The upstream API states no licence, rate limit, or redistribution policy — only a
privacy page and a contact address. Silence is not permission, so this repository
publishes **derived statistics** rather than a copy of their rows, and credits
the source on every page.

## Related

- [Vintage](https://github.com/RezaSoleymanifar/vintage) — point-in-time market
  data behind six verbs, where `known_at` is the index this tape is built to join.
- [Alpha Archive](https://github.com/RezaSoleymanifar/alpha-archive) — quant
  papers reproduced on that data.

MIT licensed. Nothing here is investment advice.
