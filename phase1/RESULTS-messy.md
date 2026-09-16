# RESULTS — messy flow (phase-1)

Source: `snapshots-messy/` — Guardian UK homepage (`https://www.theguardian.com/uk`),
8 captures: `01-landed` → `02-scroll1` → `03-scroll2` → `04-vol-1..5`
(5 no-action probes, ~6.3 s apart, ~31 s window). AX via CDP
`Accessibility.getFullAXTree`, full DOM via `page.content()`.
Harness: `measure.py::run_messy` (`capture_step` + `volatility_score`, volatile
time/token attrs normalized before hashing).

## Numbers

| step | ax bytes | dom bytes | shot bytes | changed regions vs prev | dom diff vs prev |
|---|---|---|---|---|---|
| 01-landed | 2,784,988 | 1,527,169 | 278,141 | — (baseline) | — |
| 02-scroll1 | 2,784,988 | 1,527,259 | 278,141 | NONE | +90 B, 6 small hunks, line-ratio 0.9895 |
| 03-scroll2 | 2,784,988 | 1,527,259 | 278,141 | NONE | byte-identical |
| 04-vol-1..5 (×5) | 2,784,988 | 1,527,259 | 278,141 | NONE each | byte-identical |

AX files are sha-identical (`ea698efa41f8`) across all 8 captures.

### Per-region volatility (5 probes, 4 intervals)

| region | norm bytes | changes / probes | rate | verdict |
|---|---|---|---|---|
| header | 108,662 | 0 / 4 | 0.00 | stable |
| nav | 44,577 | 0 / 4 | 0.00 | stable |
| main | 561,810 | 0 / 4 | 0.00 | stable |
| footer | 1,403 | 0 / 4 | 0.00 | stable |

### Scrolled vs landed DOM

`01-landed → 02-scroll1`: only 6 line-hunks changed (+90 B on a 1.5 MB DOM).
Hunks are attribute-level, not content: scroll-position markers
(`data-previous-scroll-y`), nav menu checkbox/`aria` toggle states, edition-picker
markup. No article text, links, or ad slots changed. `02-scroll1 → 03-scroll2`
and all volatility probes: byte-identical DOMs. Scrolling loaded **zero**
additional content — Guardian serves the full page upfront; no infinite-scroll
or lazy-load fired in this run.

## Volatility verdict

**No chronically noisy region in this sample — 0.00 change rate everywhere.**
That is itself the finding, with two caveats: (a) single site, (b) a ~31 s
window. The normalization (timestamps, nonces/tokens, rev-ids stripped before
hash) held: not one region hash flipped without a real change.

Sentinel policy derived from this run:

1. **Cache `header`/`nav`/`footer` aggressively** (~155 KB normalized combined).
   Re-hash per step, but skip re-snapshot while the hash holds.
2. **Always re-snapshot `main`** (~562 KB normalized) after scroll or navigation —
   it is where lazy-loaded content would appear (it just didn't here).
3. **Ignore sub-KB attribute churn** (scroll markers, `aria-expanded`/checkbox
   toggles): below ~1 KB DOM delta with unchanged region hashes ⇒ treat as
   no-change, don't re-observe.
4. **Do not trust scroll alone as a change signal** — verify with a region
   re-hash before spending an observation.

## Bot-wall finding (dailymail / thesun)

From `meta.json`: the first two messy candidates never yielded a page.

- `https://www.dailymail.co.uk/home/index.html` → stub, dom=310 B,
  title `'Access Denied'`.
- `https://www.thesun.co.uk/` → stub, dom=2,062 B, title `'Verifying Device'`.

Both return HTTP-200 bot-wall shells, so status codes can't detect them. The
harness now skips any candidate with `dom < 20,000 B` or a deny title
(`measure.py::run_messy` blocker check) and records it under
`meta["blocked"]`. Flow fell through to Guardian (3rd candidate).
Sentinel rule: **treat tiny-DOM + deny-title as blocked, log and move to the
next candidate — never snapshot a wall as if it were content.**
