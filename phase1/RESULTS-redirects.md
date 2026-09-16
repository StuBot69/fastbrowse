# RESULTS — redirects flow (phase-1)

Source: `snapshots-redirects/` (gitignored raw captures) + `redirect_cases.json`.
Harness: `measure.py::run_redirects` — per target, a mid-chain capture
(~300 ms after navigation commit) then a settled capture (`domcontentloaded` +
2.5 s dwell + `networkidle` up to 8 s). `capture_step` for all observations.

## Table

| case | start URL | mid-chain URL (~300 ms) | final URL | redirected | settle time | settle state |
|---|---|---|---|---|---|---|
| r1 httpbin chain | `https://httpbin.org/redirect/3` | `https://httpbin.org/get` (already final) | `https://httpbin.org/get` | yes | 2.87 s | networkidle reached |
| r2 meta-refresh | data-URL, refresh `2;url=https://example.com/` | data-URL (transient page) | `https://example.com/` | yes | 2.51 s | networkidle reached |
| r3 control | `https://en.wikipedia.org/wiki/Main_Page` | same | same | no | 3.25 s | networkidle reached |

### Mid vs settled observation sizes

| case | mid ax / dom bytes | settled ax / dom bytes | region diff mid→settled |
|---|---|---|---|
| r1 | 25,007 / 1,083 | 25,027 / 1,083 | NONE (+20 B AX noise) |
| r2 | 2,794 / 130 | 852 / 559 | NONE — **but a different document** (see below) |
| r3 | 1,042,704 / 429,131 | 1,087,832 / 429,131 | NONE (late AX hydration only) |

No observation was lost mid-chain: all 3 mid captures and all 3 settled
captures succeeded.

## Findings

1. **Server-side chains settle faster than any useful mid window.** The 3-hop
   302 chain (r1) was already at its final URL 300 ms after commit. A mid-chain
   snapshot of an HTTP redirect chain buys nothing — it duplicates the settled
   one (+20 B noise).
2. **Meta-refresh is the opposite: the mid capture is the *wrong* document.**
   r2-mid caught the transient `data:` stub; r2-settled is example.com. Any
   observation taken before the refresh delay fires must be discarded.
3. **Region-hash diff is blind to full-document swaps.** r2 mid→settled reports
   `changed: NONE` because neither document has header/nav/main/footer elements
   (empty-region hashes match). URL change + full ax/dom byte change caught what
   regions missed. Never gate "did the page change" on region hashes alone.

## Redirect-handling rules for the sentinel

1. **Snapshot only after settle**: `domcontentloaded` + dwell (≥ longest
   meta-refresh delay seen, 2 s here) + `networkidle` with a timeout fallback
   (~2.5–3.3 s per navigation in this run). Never snapshot on `commit`.
2. **Always compare start vs final URL.** If they differ, discard every
   pre-chain observation — it belongs to a document that no longer exists.
3. **Gate change detection on URL + full-doc bytes first, regions second.**
   A URL change with unchanged region hashes (r2 pattern) still means
   re-observe everything.
4. **Mid-chain captures are not worth taking** for HTTP chains (already settled)
   and actively misleading for meta-refresh (wrong document). If a mid-chain
   peek is ever needed (e.g. stuck-chain diagnosis), label it transient and
   never let it enter the snapshot cache.
5. **Control (r3) confirms the steady state**: same-URL re-capture after settle
   shows zero region change — settle-then-snapshot is a stable baseline.
