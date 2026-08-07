# What the page has to be

The contract `tools/visual_check.py` enforces. Every rule here is machine
checked, at every viewport, on every board. A rule that cannot be checked does
not belong on this page; it belongs in a note.

The point is not taste. It is that a dashboard which renders `NaN`, or draws
3-pixel text on a phone, or claims eighteen missed hours in the header and
seventeen in the legend, is broken in a way nobody notices until a reader
quietly stops trusting it.

## Truth

1. **No placeholder leaks.** The rendered text never contains `undefined`,
   `NaN`, `null`, `[object Object]`, `Infinity`, or a bare `—` standing in for
   a number. A value we do not have is written `n/a` or the element is absent.
2. **The counts agree.** Every number that appears twice must match. The
   header's missed-hour count and the legend's unrecorded-column count are the
   same quantity and are compared directly.
3. **Nothing is asserted that the data does not carry.** Any figure on the page
   traces to a field in `latest.json` or `series.json`.
4. **The source is credited without interaction.** The ApeWisdom link is
   visible on load, not behind a disclosure.

## Legibility

5. **No visible text renders below 10 effective pixels**, where effective means
   after any SVG viewBox scaling. This is the rule that catches 2.9px axis
   labels on a phone.
6. **Body text clears 4.5:1 contrast** against what is actually behind it, and
   large text clears 3:1.
7. **Numbers are tabular** wherever they sit in a column, so digits line up.

## Layout

8. **The page never scrolls sideways.** At every viewport, `scrollWidth` must
   not exceed `clientWidth` by more than one pixel.
9. **Nothing overflows its card.** No element extends past its container's
   right edge.
10. **The headline and the metadata strip are above the fold** at 1280x800 and
    wider, because they are what tells a first-time reader what this is.
11. **Controls are at least 24px tall** on touch viewports.

## Function

12. **Every board renders.** All nine chips produce a non-empty heatmap, a
    non-empty table, and a filled "at this moment" panel. A board with no data
    says so in words instead of rendering blank.
13. **Every range renders**, and switching one never empties the page.
14. **The console stays clean.** No errors, no unhandled rejections.
15. **One `h1`,** and headings descend without skipping a level.

## Viewports

| Name | Size | Why |
|---|---|---|
| phone | 390 x 844 | the smallest thing anyone will really use |
| tablet | 768 x 1024 | where the grid collapses |
| laptop | 1280 x 800 | the fold test |
| desktop | 1536 x 864 | the common large size |

## Running it

```bash
python tools/visual_check.py                 # every viewport, every board
python tools/visual_check.py --quick         # laptop only, one board
python tools/visual_check.py --open          # write the report and show failures
```

Artifacts land in `.visual/`: one PNG per viewport and board, plus
`report.json` listing every rule with its result and the evidence. The exit
code is non-zero when any rule fails, so it gates a commit the same way a test
does.

The loop this feeds is in `tools/VISUAL_LOOP.md`: run the check, hand the
report and the screenshots to a reviewer that has not seen the code, fix what
comes back, run it again, and stop when a round returns nothing.
