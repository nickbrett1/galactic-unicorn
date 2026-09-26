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
