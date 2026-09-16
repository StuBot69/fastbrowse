# FastBrowse phase 6 — RESULTS (hover physics)

Pointer is state: hover menus live or die by where the pointer sits.
`phase6/hover.py` + `fixture.html` (3 menu types) + `human_mouse.precise`.

## Fixture results (all green)

| menu | result | effect |
|---|---|---|
| simple dropdown | ok, 1 attempt | clicked:m1-a |
| nested submenu | ok, 1 attempt | clicked:m2-a |
| hover-intent (400ms delay) | ok, 1 attempt | clicked:m3-a |

Sentinel rule verified both directions on all three: menu-open +
pointer-on-anchor → `EXPECTED-HOVER-OPEN`; pointer moved away →
`EXPECTED-HOVER-CLOSED`. Hover state is never novelty.

## What the nested menu taught us (3 real findings)
1. **Record the menu holding the target, not its parent.** First cut named
   `#m2-menu`; the target lives in `#m2-sub`. Rule: among visible holders,
   pick smallest area (deepest wins).
2. **Nested anchors have no box until parents open.** Recorder and player
   both walk the anchor chain (`pre_hover`), resolving boxes level by level.
3. **Steady-hand mode.** Bezier bulge + jitter cross dead pixels and kill
   menus mid-path. `human_move(precise=True)`: straight line, zero jitter,
   zero overshoot. Route through the open submenu's box, verify at waypoints.
4. **Gap analysis / NO-SAFE-PATH.** A 12px true gap between anchor edge and
   submenu edge is uncrossable by ANY pointer — elementFromPoint proved the
   collapse happens 2px off the anchor even over the parent menu. `play()`
   measures the gap and reports `NO-SAFE-PATH gap=Npx` so the planner routes
   around (keyboard, direct URL) instead of flailing. Detection IS the feature.

## Files
- `phase6/hover.py` — record/play/sentinel-key/gap analysis
- `phase6/fixture.html` — deterministic 3-menu test page
- `human_mouse.py` — `precise` steady-hand mode
