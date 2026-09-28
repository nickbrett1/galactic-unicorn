# Phase C / deliverable C1 — notes (board-independent half)

Long detail for the C1 change set. Nothing here is board-facing; no network
loop, no `lib/routine.py` edit, no deploy.

## What already existed (plans / design notes)

- **This (firmware) repo has no plan for phase 2.** No `specs/`, no `plan.md`,
  no phase-2/remote notes. The only plan-like docs are the generated
  `README.md` and `.buildkite/README.md`. So there was nothing to follow and
  nothing duplicated — C1 was derived from the memos and the wire contract.
- **The sibling service repo (`galactic-unicorn-remote`) owns the plan.**
  `specs/plan.md` §6 explicitly puts **Phase C (firmware) out of scope as an
  external repo**, and `specs/plan-progress.md` shows Phase B complete. Its
  only shared artefact is `specs/spec/api/device-protocols.md`, which C1
  mirrors (as does `src/lib/server/reconcile.js`, the semantics reference).
- The governing firmware spec is the memo
  `memos/galactic-unicorn-remote-v1` ("…remote triggering (phase 2)") §6.4.

## What was added / changed

| File | Change |
|---|---|
| `lib/reconcile.py` | **new.** Pure reconcile decision + report builder. |
| `tests/test_reconcile.py` | **new.** 25 pytest cases. |
| `pyproject.toml` | pytest added to the `dev` extra + `[tool.pytest.ini_options]`. |
| `.buildkite/pipeline.yml` | `python3 -m pytest tests/ -q` added next to `ruff check .`. |

Semantics mirror `reconcile.js`: `is_gen_applied` (ignore `<=`), equality is a
no-op, `is_expired` (drop, never queue), `event_for_desired` (cancel →
`reset`), `resolve_command` (the §5 conflict table verbatim), `next_poll_ms`,
`clear_on_boot`, `is_event_live` (routine events inert in COUNTDOWN/HANDOFF;
`reset` live in all active states), and `decide()`, the single
`(desired, applied_gen, panel, now_epoch_s)` decision. `build_report()` fills a
caller-owned dict in place so the reporting path allocates nothing per poll
(memo §6.3.4).

MicroPython-subset notes: no f-strings, no walrus, no annotations in either new
file. Ruff (`py37`) is a lint, not a parser (README).

## Contradictions / open items found (do NOT silently fix here)

1. **Routine id mismatch — RESOLVED (commit `c8a833e`).** C1 found
   `routines.json` defining the third routine as id `tidyup` (symbol `boxes`)
   while the memo §12, `device-protocols.md` §2 and the service's
   `reconcile.js` all use `cleanup` (symbol `toy-box`). That landed before C2:
   commit `c8a833e` ("fix(routines): id cleanup, symbol toy-box to match the
   frozen wire contract") reconciled `routines.json` (and `lib/icons.py` /
   `scripts/bench-smoke.py`) to `cleanup`/`toy-box`. `reconcile.py` still names
   the **wire** vocabulary (`ROUTINE_IDS`) and now refuses a `start` naming
   anything outside it, so the mismatch cannot reappear silently.
2. **TTL and new-boot clearing are, per the wire contract, the *server's* job.**
   `device-protocols.md` §3 says "There is no `expires_at` comparison on the
   board at all", and §3.2 gives boot-id clearing to the server. But memo §6.4
   lists *TTL expiry* and *new-boot-id clearing* among the board's pytest
   cases. C1 implements both as **defensive parity** (so the decision is total
   and host-testable) and `decide()` takes `now_epoch_s`. On the board path the
   server should already have dropped an expired/none slot; the report is that
   the memo and the spec disagree, and C1 follows the memo's test list.
3. **Extra board state.** `lib/routine.py` has `OFF` in addition to
   `ambient/prompt/countdown/handoff`. `OFF` is not in the wire
   `PANEL_STATES`; it is never reported and `decide()` has no rule for it.
4. **`D` hold semantics.** The board's physical D honours
   `config.D_CANCEL_HOLD_MS` in COUNTDOWN (`lib/routine.py`). The pure module
   treats `reset` as live in COUNTDOWN; the hold-vs-press distinction is the
   event layer's, not the decision's.
5. **`+2 min` (hold-to-extend) is physical-only.** `lib/routine.py` extends on a
   routine hold in COUNTDOWN (`EXTEND_MINUTES`). It is not in the four-event
   wire vocabulary (memo §12 defers it), so the remote cannot trigger it — that
   is consistent, recorded so nobody mistakes it for an omission.
6. **Naming.** The memo/event vocabulary calls the D event **`reset`**; the
   server's desired action is **`cancel`**. `event_for_desired` maps
   `cancel → reset`. The report builder emits the server's `state` names
   (`ambient/prompt/countdown/handoff`), matching routine.py.

## C1 — explicitly not done (and where C2 did it)

`lib/remote.py` (network loop), `lib/routine.py` event seam / state exposure,
any board or deploy step. (The `routines.json` item is resolved — see item 1.)

## C2 — the network half (this change set, uncommitted as noted)

The board-facing half of Phase C, derived from memo §6 and the frozen
`device-protocols.md`:

| File | Change |
|---|---|
| `lib/net.py` | **new.** Associate/reconnect extracted verbatim from updater so both callers share one radio implementation. |
| `lib/remote.py` | **new.** The poller: one bounded `GET /device/poll`, hard byte cap, `gc.collect()` before the request, one report built into a reused dict, `next_poll_ms` clamped by the config floors, no join in COUNTDOWN/HANDOFF, heap-vs-link failure logging. |
| `lib/routine.py` | event seam (`post_event` → `_injected` drained by the ordinary button dispatch) + state exposure (`routine_id`, `remaining_s`). Semantics unchanged. |
| `config.py`, `config_secrets.example.py` | poll floors/ceiling, the service-URL literal, `REMOTE_ENABLED`, device token, applied-gen/log paths. |
| `lib/updater.py` | `_join_wifi` / `apply_static_ip` now thin wrappers over `lib/net.py`. |
| `main.py` | construct the poller; poll once per cadence; defer the OTA check while it reports `busy()` (COUNTDOWN/HANDOFF). |
| `tests/test_remote.py` | **new.** Pure helpers on the host: clamp, URL split, query, response split, heap-vs-link classifier. |
| `.gitignore` | the device-written `remote.log`, `applied_gen.txt`, `crash.log`. |

Still not done: any board flash or deploy, and running the two suites against
the real radio (no emulator — memo §6.4). The pure half is what pytest pins.

## T1 — task: cleanup 3->5 min + warmer "ta-da" DONE_SOUND (2026-09-26)

Plan, in order:
1. routines.json cleanup "minutes": 3 -> 5 (engine reads it at lib/routine.py:147;
   bathtime/booktime stay 5).
2. lib/sound.py DONE_SOUND -> warm ta-da (G4 C5 E5 G5 -> C6 held), _configure ->
   TRIANGLE, attack 0.04, release 0.25, volume 0.75. Update docstrings.
3. scripts/render-fanfare.py -> triangle + new envelope (stays in sync).
4. tests/test_sound.py -> triangle case, relax note-count for the simpler tune,
   add a no-alarm property (no immediate repeats, no rapid notes).
5. python3 -m pytest tests/ -q ; ruff check . ; python3 scripts/render-fanfare.py
6. ./scripts/deploy.sh ; confirm board banner + new build.

BOARD STATE at T1 start: /dev/tty.usbmodem201NTFA540352 is present but SILENT.
find-board.sh: 10/10 rounds no REPL (exit 3). raw serial Ctrl-C/Ctrl-D -> b''.
mpremote connect -> "could not enter raw repl". The repo's find-board.sh says this
is the power-cycle case. Doing the code+test work first, deploy last.

### T1 edits + host checks (done)

- routines.json: cleanup "minutes": 3 -> 5.
- lib/sound.py: DONE_SOUND = [(392,0.14),(523,0.14),(659,0.14),(784,0.18),(1047,0.95)];
  _configure -> TRIANGLE, attack=0.04 decay=0.08 sustain=0.85 release=0.25 volume=0.75.
- scripts/render-fanfare.py -> triangle + new envelope (32 kHz... no, 22050).
- tests/test_sound.py: triangle case, relaxed note count, added case_no_alarm.

Host results: pytest 41 passed; ruff clean; test_sound.py 14/14;
render: 5 notes, 1.55s, no >half-HANDOFF warning.

Next: deploy.sh. Board was silent at start; retry now.

### T1 deploy attempt (blocked)

./scripts/deploy.sh ran: gen-secrets.sh OK (doppler -> config_secrets.py,
ssid set, static 192.168.1.63, device_token set). Then step 2 find-board.sh:
10/10 rounds, board never answered as a REPL -> exit 3, deploy aborted BEFORE
any file was pushed or the board was reset. No code reached the board.

Direct evidence the board is silent (not a wrong-port guess):
  /dev/tty.usbmodem201NTFA540352 present; raw Ctrl-C/Ctrl-D -> b'';
  mpremote connect -> TransportError: could not enter raw repl.
find-board.sh's own instruction for this exact state: "Power-cycle the board,
then re-run." NEEDS A PHYSICAL RE-PLUG / POWER-CYCLE. Not guessing a port.

## W1 — NYC weather on the idle screen (2026-09-28, deployed)

Task: show the current NYC weather (temperature in Celsius + a condition glyph)
on the idle/AMBIENT screen, refreshed periodically. Device is on USB.

### Decisions

- **Source = Open-Meteo, plain HTTP.** The board cannot complete a TLS
  handshake (config.UPDATE_MANIFEST_URL), so the source must speak plain HTTP.
  Open-Meteo needs no API key and answers this exact request with ~200 bytes:
  `{"current":{"temperature_2m":14.3,"weather_code":3}}`. Verified over plain
  HTTP before writing any code. NYC is a fixed lat/lon (Manhattan).
- **Idle only.** `main.py` calls `weather.poll_if_due(now)` only when
  `engine.routine is None` (i.e. AMBIENT/OFF), so a fetch - which may join
  WiFi - never shares the thread with a running timer. Same rule as the remote
  poller (device-protocols.md 8.2).
- **Weather replaces the clock.** The panel is 53x11, so a clock and an
  icon+temperature do not fit together. The idle screen is now the weather,
  permanently; the clock is only the fallback for the moment before the first
  reading lands (no reading yet, or `WEATHER_ENABLED=False`).
- **The weather is exempt from the dark-room blanking** (found 2026-09-28 after
  "I don't see any weather on the board"). `routine.tick()` sends a dark room to
  `ambient.draw_dark()`, which by design lit the lamp and nothing else - and the
  board sits where the phototransistor reads a steady `light()=17` against
  `LIGHT_DARK=40` even in a lit room, so the weather was drawn for exactly zero
  frames. `draw_dark()` now also draws the weather when a reading exists, at the
  same `BRIGHTNESS_AMBIENT` (0.10) the lit-room idle path already used, so it is
  no brighter than the idle screen has always been. `config.AMBIENT_WEATHER_IN_DARK
  = False` restores lamp-only. Verified: the telemetry (`light()=17`), the
  host render (a weather frame is 138-229 lit pixels), and the two new
  `tests/test_ambient.py` cases.
- **Its own brightness** (2026-09-28, after "its way too dim"). The weather
  first drew at `BRIGHTNESS_AMBIENT` (0.10) - the level meant to make the clock
  and the status pixel vanish into the furniture - and 0.10 is unreadable
  across a room, which the dark path hit on every frame. Added
  `config.BRIGHTNESS_WEATHER = 0.40` and `ambient._weather_brightness()`; the
  weather frame (lit or dark room) now raises itself to that level, while the
  clock fallback and the lamp-only dark frame keep 0.10. Still under
  `BRIGHTNESS_PROMPT` (0.45), so the idle screen stays the dimmest frame with
  content on it. Notched 0.40 -> 0.55 on the next report ("a little dim
  perhaps").
- **Animated rain** (2026-09-28, "the raindrop does not look like a raindrop").
  The old `_RAIN` was a single 11x11 frame: a full-height cloud blob with the
  drops drawn *inside* the silhouette, which read on the wall as "a blob with
  dots". Redrawn as a short cloud (top six rows) with three one-pixel streams
  falling through the five rows under it, in three frames that step down one
  row each (`_rain_frame`), cycled on `icons.WEATHER_FRAME_MS` (300 ms - a
  0.9 s cycle, a steady shower rather than a twitch). `draw_weather_icon` now
  takes `age_ms`; `Ambient` passes its phase (and `time.ticks_ms()` from the
  dark path, which has no phase). The other conditions stay single static
  frames - the idle panel is furniture. Pinned by
  `test_rain_animates_and_the_drops_fall` and `test_only_rain_animates`.
- **The cloud had to stop looking like a drop** (2026-09-28, "its back to a big
  drop" - the real reading was `cloud`, WMO code 3, and the animated rain was
  only my preview). `_CLOUD` was a smooth oval: it tapered at top AND bottom, so
  its silhouette was a teardrop. Every cloud is now built from one rule - a
  DEAD FLAT base with lobes on top (`_CLOUD_TOP`), so `cloud`, `rain`, `snow`,
  `thunder` and `partly` all read the same way.
- **`cloud` = the rain cloud, drops removed** (2026-09-28, "cant we use the cloud
  from the rain example (just remove the rain)"). The taller flat-base cloud
  (`_CLOUD_TOP` plus five full rows) read as a slab; the shape known to look
  right on the wall is the rain frames' own cloud. `_CLOUD` is now `_CLOUD_TOP`
  followed by the five empty rows the drops fell through - the literal "remove
  the rain" - so the icon is top-aligned in the frame, matching the rain icon.
- **New module `lib/weather.py`**, modelled on `lib/remote.py`: passive
  `poll_if_due`, bounded `net.http_get` (timeout + byte cap), `gc.collect()`
  before the request, failure logged to a bounded `weather.log` with heap AND
  link context, retry-sooner-than-refresh. Pure helpers (`classify`,
  `parse_current`, `format_temp`) are host-tested.
- **Glyphs** go in `lib/icons.py` (`WEATHER_ICONS`, 7 static 11x11 frames);
  **digits/minus** added to `lib/bigfont.py` for the temperature text.

### Verification

- Host: `ruff check .` clean (excluding the pre-existing untracked
  `scripts/rescue-usb.py`); `pytest tests/ -q` -> 53 passed (new
  `tests/test_weather.py`); `tests/test_ambient.py` -> 10/10 (weather frame
  keeps the lamp; alternates; falls back to the clock with no reading).
- Board (MicroPython 1.29.0, RP2040, fw=0.1.29, USB): captured boot log shows
  `weather: polling http://api.open-meteo.com/...` then `weather: 14C cloud`
  after the real fetch (weather_code 3 = overcast), NTP synced, remote polling.
  `deploy.sh` pushed `lib/weather.py` and the edited files.

Unrelated to this change: the boot-time update check logged
`update failed, keeping current firmware: OSError(28,)` (LAN service / flash) -
pre-existing, and it kept the current firmware as designed.

### W1 update — two-tone `cloud` (2026-09-28)

- **The cloud is drawn in two inks**, and it is the only glyph that is. Four
  filled attempts and an outline attempt all failed by eye: filled read as a
  blob (no interior detail), a one-colour outline read as a hollow box (one pen
  cannot say "edge" AND "middle"). So the glyph carries two ink symbols: `O` is
  the RIM, `X` is the BODY. `_draw_frame` paints `X` with the body pen and `O`
  with the rim pen; `ambient.CLOUD_RIM_RGB` (white) is passed only for `cloud`,
  the digits stay on `WEATHER_RGB` (dim blue-cyan).
- **The rim is the filled silhouette's top boundary plus its left boundary** —
  a lit-from-top-left cloud. The silhouette (two lobes over a dead-flat base) is
  unchanged; only the top/left contour is picked out in white.
- **The cloud is 9 rows (rows 2-10)**, not 11: a cloud roughly twice as wide as
  it is tall, sitting on the same baseline as the bigfont digits. An 11-row
  square of cloud read as a boulder.
- **Verification:** `ruff check lib tests` clean; `pytest tests/test_weather.py`
  -> 17 passed (new: only `cloud` is two-tone, rim runs the left contour, the
  two pens reach the panel); `tests/test_ambient.py` -> 15/15 (new:
  `case_cloud_is_drawn_in_two_tones`, asserting the cloud frame paints both
  `CLOUD_RIM_RGB` and `WEATHER_RGB` while rain paints only the body pen).

### W1 update 2 — the cloud *shape* (2026-09-28)

- The two-tone ink was right ("I like the outline idea") but the silhouette was
  not a cloud: two equal teeth over straight vertical sides reads as a loaf, and
  a smooth oval reads as a teardrop. The fix is **big lobe left + clearly
  smaller puff right, with a cleft between them, over a wide flat base** — a
  cloud's edges *step in and out*, which is what the earlier flat-sided shapes
  were missing. Rendered the candidates to PNG on the host and picked by eye
  before touching the board.
- Rim is still the filled silhouette's top + left boundary; unchanged in
  `_draw_frame` / `ambient._draw_weather`.

### W1 update 3 — the cloud, Salesforce-style (2026-09-28)

- Third shape pass. Root cause of every previous failure: the cloud was drawn
  SQUARE (9-11 rows in an 11-row box) with straight vertical sides. A cloud is
  wide. Now 7 rows (2-8 -> it centers itself beside the full-height digits) with
  the lobes *stepping down* to a wide flat base.
- Massing follows the Salesforce mark: one big round lobe, a smaller lobe beside
  it, merged, dead-flat base. Picked by rendering candidates to PNG on the host
  (`/tmp` helper) and looking at them - circles/supersampling and multi-bump
  column profiles all collapsed into slabs at 11 px.
- Ink unchanged: rim = filled silhouette's top + left boundary.

### W1 update 4 — closed contour, fluffy cloud (2026-09-28)

- "Looks more like a mountain" was the rim, not just the shape: marking only the
  top + left of a shape that widens in one diagonal gives an open ramp. The rim
  is now the WHOLE boundary - the cloud's contour is closed, which is how a
  fluffy cloud icon is recognised.
- Shape back to two round humps over a wide dead-flat base, 7 rows (1-7, self-
  centering beside the digits). Anything widening in a single long diagonal
  reads as a mountain; two humps + stepped sides + flat base reads as a cloud.

### W1 update 5 — the cloud goes wide (2026-09-28)

- "Could you use much more horizontal space?" Yes: the panel is 53 px and only
  11 were used. The cloud is now **22 px wide** (24 is the max that still fits
  `-20C` at 20-27 px). Aspect ~2:1 is what finally reads as a cloud; at 11 px
  square, every silhouette collapsed into a mountain/loaf.
- Shape: three puffs of decreasing size anchored low so the sides drop nearly
  straight (a monotone diagonal = mountain), full closed outline, flat base.
- Tests generalised to per-icon width (`weather_icon_width`), so the cloud can
  be wider than the 11-wide conditions.

### W1 update 6 — round the cloud, soften the rim (2026-09-28)

- Two notes on the wide cloud: "I'd like something a little more rounded", and
  "the white outline is a little too bright, a more subtle grey perhaps?".
- Rounding is a real constraint, not a taste call. The 22x7 shape left the
  humps as flat-topped BARS with square corners, which read as a castle/loaf.
  A dome only reads as a dome if its top steps out about a pixel per column,
  and at four rows of height there is no room to step more gently than that.
  Also learned: a dome apex one column wide reads as an ANTENNA, so the apex is
  drawn at least 2 px wide (tried and rejected as a spire).
- Final shape: two rounded domes (left taller) over the same wide flat base,
  full closed outline, ink across cols 1-20 of the 22 px glyph.
- Rim: `CLOUD_RIM_RGB` (255,255,255) -> (150,150,150). The panel scales by
  BRIGHTNESS_WEATHER, so the old rim landed at ~140 against a body that peaks
  at 46 - it out-shouted the temperature. 150 lands at ~82: still unmistakably
  a contour, no longer a highlight.
- Method note: the deciding tool was again rendering candidates to PNG on the
  host and looking at them (union-of-discs -> outline -> PNG), plus a
  contour verifier that asserts every ink row is rim at its ends and body
  inside. Both `/tmp` helpers, not in the repo.

### W1 update 7 — cloud, third pass: lobes, and a much darker rim (2026-09-28)

- "This doesn't look like a cloud, and the white is too bright. But width
  helps." Two separate faults.
- SHAPE. Two equal humps over a wide base reads as a SOFA, which is what the
  last pass shipped. A cloud needs one lobe to dominate: small left lobe, big
  centre lobe, small right lobe, all on the wide flat base.
- Also learned, and it cost several passes: an algorithm that steps the outline
  out 1 px per column to keep it "smooth" flattens every candidate into a mesa
  (a slab with a bump). Real pixel-art clouds step 2 px per row - that is what
  reads as round at this size. Dropped the slope-limit, kept only the
  no-single-column-spire rule.
- Lobes are drawn full height (rows 2-10) so they are big enough to be round.
- RIM. `CLOUD_RIM_RGB` 150 -> 110. White (255) landed at ~140 effective and
  150 at ~82, against a body peaking at 46 - so the rim, not the cloud, was the
  brightest thing in the glyph, which is what made the shape read as an
  outlined badge rather than a cloud. 110 lands at ~60.

### W1 update 8 — iOS massing, then tapered sides (2026-09-28)

- "More like the iOS weather app - that has a nice cloud on its icon." Rebuilt
  the silhouette on that massing: one big round lobe dominating the left of
  centre and a clearly smaller lobe to its right, over a broad base. The big
  lobe is deliberately BIG (about half the width) so its curve is unmistakable;
  the second lobe is visibly smaller rather than a mirror, which is what stops
  it reading as a sofa again.
- "I like the top and the grey outline - can we ensure the sides aren't vertical
  lines and instead gradient down to the bottom?" The iOS-massing draft still
  dropped from the shoulders as two vertical walls onto a full-width shelf
  (rows 6-8 all `OXXXXXXXXXXXXXXXXXXXXO`, base row full width). Widening at the
  base is what made them read as walls.
- Fix: the **lower rows step in one column per row** - rows 6-8 go from cols
  1-20 to 2-19 to 3-18 - so the silhouette curves inward to the base instead of
  standing up as walls. The base ends up ~3/4 of the widest row.
- Kept the taper GENTLE on purpose. An earlier pass with a base much narrower
  than the cloud read as LIPS (two bumps over a pinched bottom), so the rule is
  not "flat base against walls" but "broad base, softly tapered sides". The top
  (big lobe + small lobe, uneven humps) and the grey rim are unchanged - both
  were liked.
- Iterated on the host first: rendered seven taper candidates to a PNG
  (`outline()` contour + the zlib PNG writer) and picked the 1-px-per-row taper
  by eye before touching the board. The rim colour was left at 85 - it was
  explicitly liked, so it was not touched.

### W1 update 9 — the cloud goes pixel-art: terraces and shading (2026-09-28)

- "This is worse - take inspiration from this image [pixel-art cloud icons] and
  let's use some additional colours to make it clearer." Two asks in one: the
  SHAPE (blocky pixel-art cloud) and the INK (more than one colour).
- SHAPE. Dropped the smooth/tapers and rebuilt the silhouette from rectangle
  terraces - the stair-stepped look of classic pixel-art sky clouds. Uneven
  bumps (a taller left one, a smaller right one) over a broad base, with the
  bottom row stepping in one column per side so the base is not a sheer wall.
- INK. This is the real fix. On a dark panel a cloud reads by TONE, not by an
  outline - that is why every flat-blue-plus-rim pass still looked like a blob
  or a hill. The glyph now carries four inks:
    'X' body   `CLOUD_BODY_RGB  = (119, 145, 158)`
    'L' lit    `CLOUD_LIT_RGB   = (123, 150, 165)`  up-facing edges + the row under
    'S' shade  `CLOUD_SHADE_RGB = (55,  84, 106)`   the underside band
    'O' rim    `CLOUD_RIM_RGB   = (84,  88,  95)`   quiet edge on the flanks
  (Values as of update 10 follow-up 3, after the whole cloud was scaled to
  ~0.88; see the follow-ups below for how they got here.)
  The body is a soft blue-grey, NOT white: white would blow out the dark room.
  Every glyph is shaded this way; the cloud keeps the extra rim ink.
- `icons._draw_frame` now paints a pen per ink symbol (each falling back to the
  body pen), so the extra inks cost nothing on the single-ink glyphs.
- Picked the shape and palette by rendering candidates to PNG on the host
  (union-of-rects silhouettes + a classifier for lit/shade/rim) and looking at
  them - the render is the only way to judge "does this read as a cloud".
- Rejected along the way: pale cloud with a full dark rim (boxy), cyan-only
  shading (still a mound), a two-row dark base bar (looked like a plinth),
  highlight bands keyed off the global top row (lit the wrong cells - the band
  has to be per column and must not spill into the shaded base).

- W1 update 9 follow-up: "a little too bright in white on the maximum - tone it
  down." `CLOUD_LIT_RGB` 205/230/240 -> 170/195/205 (panel ~112 -> ~93). It
  still sits clearly above the body (panel ~91 vs body ~82), so the lit-top
  read survives; only the glare is gone. Body, shade and rim unchanged.

- W1 update 10 follow-up: "tune down the brightness on the white on the cloud
  icon, it's a bit too bright" - still about the lit top. `CLOUD_LIT_RGB`
  170/195/205 -> 150/178/190 (panel ~93 -> ~82). Rendered 170 / 150 / 140 / 130
  / 122 on the host with the real glyph; 150 keeps the lit-top step fresh while
  the glare goes, and 140 and below start merging into the body (panel ~74).
  Body, shade and rim unchanged.

- W1 update 10 follow-up 2: "can we make it a bit less bright - still a bit too
  much glare." `CLOUD_LIT_RGB` 150/178/190 -> 140/170/187 (panel ~82 -> ~77).
  Rendered 150 / 140 / 134 / 128 / 122 on the host with the real glyph; the
  reds and greens come down hardest, so what is left is a cooler, bluer
  highlight rather than white. 134 and below still shows a step in the render
  but only in the blue channel (panel ~73 vs body ~74 in red), i.e. it stops
  reading as a *brightness* cue. Body, shade and rim unchanged.

- W1 update 10 follow-up 3: "ok - it's still a touch hot." Rather than dim the
  lit again (which only closes the gap to the body), the WHOLE cloud is scaled
  to ~0.88: body 135/165/180 -> 119/145/158, lit 140/170/187 -> 123/150/165,
  shade 62/95/120 -> 55/84/106, rim 95/100/108 -> 84/88/95. Panel highs go
  ~77 -> ~67 and the underside ~52 -> ~46, so the cloud drops back to being
  furniture while keeping the same lit/mid/shade structure. Rendered 1.00 /
  0.93 / 0.88 / 0.83 / 0.78 with the real glyph before picking 0.88; the
  relative contrast is unchanged at every factor, only the level moves.

### W1 update 10 — shading for every icon, and a per-condition palette (2026-09-28)

- "Let's see what the other icons are - and like the cloud, add colour
  gradients to give impact." The cloud's lit/shade treatment generalised to the
  whole set.
- Ink vocabulary is now shared across every weather glyph: 'X' body, 'L' lit
  top, 'S' shade, and 'O' reused as the ACCENT ink for the one element that
  wants a different colour.
- Per glyph: sun = amber disc (lit rays, shaded lower half); partly = amber sun
  ('O') over a lit/shaded cloud; fog = bars fading lit-to-shade top to bottom;
  rain = shaded cloud with cyan drops ('O'); snow = shaded cloud with white
  flakes ('O'); thunder = shaded cloud with a yellow bolt ('O'). `_CLOUD_TOP`
  (shared by rain/snow/thunder) became a shaded six-row cloud.
- `ambient.WEATHER_PENS` maps condition -> (body, lit, shade, accent), and
  `_draw_weather` picks by condition, falling back to the cloud palette for an
  unknown code (whose icon also falls back to the cloud). The digits stay on
  WEATHER_RGB.
- Picked the palettes by rendering the whole set to a PNG on the host and
  looking at it, so the ambers and cyans sit at a sensible level after
  BRIGHTNESS_WEATHER (0.55) rather than guessing.
- Tests updated: every weather glyph must use a lit or shade ink (so a future
  edit cannot quietly flatten one back to a single-ink stamp), and the ambient
  detail test now checks cloud and rain each draw with their own palette.
- `scripts/rescue-usb.py` (previously untracked) linted: `ruff check .` is
  clean again, so CI's `ruff check .` no longer breaks on it.

- Follow-up 4: "how can I see the rest?" - the other six glyphs. Added
  `scripts/preview-icons.py`, a DEVICE-side script (`mpremote run`) that paints
  all seven conditions in turn with the real palettes / brightness, then soft
  resets back to main.py. Two things it has to do that are easy to miss:
  mirror `_draw_weather`'s centring (icon + digits as one group) rather than
  reimplementing an approximation, and FEED THE WATCHDOG - main.py is the thing
  that normally does, and it is not running, so without `watchdog.feed()` the
  ~8 s fuse resets the board about three conditions in (measured: died between
  cloud and fog). Also rendered the whole set to a host PNG through a fake
  display, which is the cheaper loop when there is no board to hand.

- Follow-up 5: "can we just make the sun all the same brightness." The sun is a
  LIGHT SOURCE, so the lit/shade treatment that gives the cloud volume is wrong
  on it: with body/lit/shade the disc reads as a stripe - bright top half, dark
  bottom half. Rendered the current palette against four flat levels; flat wins
  outright. `WEATHER_PENS["sun"]` is now the same gold (170, 140, 32) for all
  four inks, via a named `SUN_RGB`. The glyph still carries 'L'/'S' cells (the
  frame is unchanged) - they just no longer mean anything different.
  Also fixed the preview script's documented interface: `mpremote run` takes NO
  script arguments (anything after the path is the next mpremote command), so
  the `run scripts/preview-icons.py rain` form never worked. Pace and condition
  are now globals read from the REPL namespace and set with a leading `exec`.
