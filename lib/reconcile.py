"""The reconciliation decision -- pure, host-testable, no sockets.

Design memo: "Galactic Unicorn -- remote triggering (phase 2)", section 6.4
(`memos/galactic-unicorn-remote-v1`). Wire contract:
`specs/spec/api/device-protocols.md` in the sibling `galactic-unicorn-remote`
repo. The service's own pure core is `src/lib/server/reconcile.js` there.

Everything here is a *decision*, not an action: given the desired-state slot
the server last sent, the board's persisted `applied_gen`, and what the panel
is doing right now, it says whether to apply an event, do nothing, or drop the
slot. There is no socket, no I/O and no clock read in this module -- the caller
supplies `now_epoch_s` -- so it runs unmodified under CPython for the pytest
suite. That suite is the only part of the firmware testable without a flash and
a look (memo section 6.4).

This mirrors the *service's* semantics so the two sides agree on `gen`
ordering, equality, TTL, the boot-id rule and the event vocabulary. It is NOT
shared code: the board and the service share the HTTP contract and nothing
else (memo section 3, stack-memo section 1).

Subset note: this module stays inside MicroPython 1.19.1's language subset
(roughly CPython 3.4). No f-strings, no walrus operator, no type annotations.
Ruff's py37 floor is a lint, not a parser (README, "Linting firmware").

Two things the memo asks the board to mirror are, per the wire contract, really
the *server's* job -- TTL expiry (device-protocols.md section 3: "There is no
expires_at comparison on the board at all") and clearing pending desired on a
new boot id (section 3.2). They are implemented here anyway, as defensive
parity, so the decision is total and testable on the host. See
`NOTES.md`.
"""

# -- vocabulary --------------------------------------------------------------
# These are the WIRE values (device-protocols.md sections 2-3). The board's
# routines.json is a separate table; where they disagree see NOTES.md.

# The three routine ids on the wire. The service's reconcile.js derives these
# from routines.json; here they are the frozen contract.
ROUTINE_IDS = ("bathtime", "booktime", "cleanup")

# The four device events, and nothing else (memo section 3, event-flow.md
# section 1 invariant 4). `reset` is the D button.
EVENT_RESET = "reset"
DEVICE_EVENTS = ROUTINE_IDS + (EVENT_RESET,)

# The actions a desired slot may carry. `none` is a valid, idempotent no-op.
ACTION_START = "start"
ACTION_CANCEL = "cancel"
ACTION_NONE = "none"
DESIRED_ACTIONS = (ACTION_START, ACTION_CANCEL, ACTION_NONE)

# The panel's own states as REPORTED BY THE BOARD. AMBIENT, PROMPT, COUNTDOWN
# and HANDOFF are the wire vocabulary; the board also has an OFF state that is
# never reported (see NOTES.md).
STATE_AMBIENT = "ambient"
STATE_PROMPT = "prompt"
STATE_COUNTDOWN = "countdown"
STATE_HANDOFF = "handoff"
PANEL_STATES = (STATE_AMBIENT, STATE_PROMPT, STATE_COUNTDOWN, STATE_HANDOFF)

# States in which the panel is doing something. D -- and so a remote cancel --
# is live in all three (memo section 3; device-protocols.md section 2).
ACTIVE_STATES = (STATE_PROMPT, STATE_COUNTDOWN, STATE_HANDOFF)

# Decision kinds returned by decide().
DECISION_APPLY = "apply"
DECISION_NOOP = "noop"
DECISION_IGNORE = "ignore"


# -- the small pure comparisons (mirror reconcile.js) ------------------------

def is_gen_applied(applied_gen, gen):
    """True once the board has applied `gen`.

    The board ignores anything `<= applied_gen` (device-protocols.md section
    3), so a desired slot is already satisfied when `applied_gen >= gen`.

    Equality is deliberately included: `applied_gen == gen` is a no-op.
    """
    return applied_gen >= gen


def reseed_gen(reported_applied_gen):
    """A fresh server re-seeds from the board's high-water mark plus one.

    Risk R6 (memo section 11.6): get this wrong and the system wedges silently
    -- the panel ignores everything and merely looks offline.
    """
    return reported_applied_gen + 1


def is_expired(desired, now_epoch_s):
    """True if the desired slot is past its TTL, or there is no slot.

    Pending desired state is dropped -- never queued, never fired late -- once
    its TTL has elapsed (device-protocols.md section 4.1, memo section 5.4).
    Expiry is compared against the slot's own `expires_at`; the constant is a
    server config value, not this module's business.
    """
    if not desired:
        return True
    expires_at = desired.get("expires_at")
    if expires_at is None:
        return False
    return now_epoch_s >= expires_at


def event_for_desired(desired):
    """Map a desired slot to the device event it produces, or None.

    A `cancel` is the `reset` (D) event -- there is no fifth event
    (event-flow.md section 1 invariant 4).
    """
    if not desired or desired.get("action") == ACTION_NONE:
        return None
    if desired.get("action") == ACTION_CANCEL:
        return EVENT_RESET
    routine = desired.get("routine")
    # The wire vocabulary is exactly four events (device-protocols.md section
    # 2). A `start` naming anything else is not a remote we can honour, so it
    # produces no event rather than an event the button map would silently drop
    # while the poll still logged "applied".
    if routine not in ROUTINE_IDS:
        return None
    return routine


def resolve_command(requested, routine, panel):
    """The conflict table of device-protocols.md section 5, verbatim.

    This is the *server's* decision (the panel keeps its simple rules and gains
    no notion of a remote override, memo section 5.5). It lives here so the
    board and the service agree on the table; on the board the same outcome is
    reached by the panel's own rules, exercised through `decide`.

    `panel` is a mapping with optional `state`, `routine`, `online` and
    `last_seen_s`. `requested` is one of `start` | `cancel` | `replace`.

    Returns a tuple:
      ("set", action, routine)          -- write desired; routine only for start
      ("noop", reason)                 -- already true / nothing to do
      ("conflict", current_routine)    -- different routine counting down
      ("refuse_offline", last_seen_s)  -- panel is offline
    """
    safe = panel or {}

    # Unreachable -> refuse BEFORE setting anything, for every request
    # (memo section 5.5).
    if safe.get("online") is False:
        return ("refuse_offline", safe.get("last_seen_s"))

    state = safe.get("state")
    active = state in ACTIVE_STATES
    active_routine = safe.get("routine")

    if requested == ACTION_START:
        if state == STATE_AMBIENT:
            return ("set", ACTION_START, routine)
        if active and active_routine == routine:
            return ("noop", "already_running")
        if active and active_routine and active_routine != routine:
            return ("conflict", active_routine)
        # Active but no routine reported: let the panel decide; a routine event
        # is inert mid-countdown anyway.
        return ("set", ACTION_START, routine)

    if requested == ACTION_CANCEL:
        if state == STATE_AMBIENT:
            return ("noop", "nothing_to_cancel")
        return ("set", ACTION_CANCEL)

    if requested == "replace":
        if not active:
            return ("noop", "nothing_to_cancel")
        if active_routine == routine:
            return ("noop", "already_running")
        return ("set", ACTION_CANCEL)

    raise ValueError("resolve_command: unknown requested action")


def next_poll_ms(pending_apply=False, active_countdown=False, subscribers=0,
                 min_ms=1000, max_ms=10000):
    """Server-directed, demand-driven cadence (device-protocols.md section 6).

    ~2000 ms if any demand input is true, ~5000 ms otherwise, then clamped.
    Defaults are the plan's recommended bounds (O5, pending ratification).
    """
    demand = bool(pending_apply) or bool(active_countdown) or subscribers > 0
    raw = 2000 if demand else 5000
    if raw < min_ms:
        return min_ms
    if raw > max_ms:
        return max_ms
    return raw


# -- the boot-id rule --------------------------------------------------------

def clear_on_boot(pending, boot, last_boot):
    """Clear pending desired when the boot id changes, else pass it through.

    The server clears pending desired the moment it sees a new boot id
    (device-protocols.md section 3.2, memo section 5.6); this is the board-side
    statement of the same rule. Auto-resume after a reboot is worse than a
    countdown that quietly ended.
    """
    if boot is not None and last_boot is not None and boot != last_boot:
        return None
    return pending


# -- the panel's own event rules ---------------------------------------------

def is_event_live(state, event):
    """Is `event` live in `state`? The panel's rules, as one predicate.

    `reset` (D) is live in every active state -- PROMPT, COUNTDOWN and HANDOFF
    (memo section 3; event-flow.md section 1 invariant 2). ROUTINE events are
    INERT during COUNTDOWN and HANDOFF -- a remote "book time" while bathtime
    runs does nothing, with no silent switch and no lost countdown (invariant
    1).
    """
    if event == EVENT_RESET:
        return state in ACTIVE_STATES
    return state in (STATE_AMBIENT, STATE_PROMPT)


# -- the decision ------------------------------------------------------------

def decide(desired, applied_gen, panel, now_epoch_s):
    """The reconciliation decision, as a pure function.

    Inputs (mirrors the memo's `(desired, applied_gen, current_state/boot)`):
      desired      -- the slot the server last sent: a mapping with `gen`,
                      `action`, optional `routine`/`expires_at`, or None.
      applied_gen  -- the board's persisted high-water mark (int).
      panel        -- what the panel reports / knows: `boot`, `last_boot`,
                      `state`, `routine`.
      now_epoch_s  -- the caller's clock, for the TTL comparison only.

    Returns a 2-tuple `(kind, detail)` with no side effects:
      ("apply", event)   -- carry out `event` (a routine id, or "reset")
      ("noop", reason)   -- nothing to do; the slot is valid but inert/none
      ("ignore", reason) -- drop the slot; it is stale, equal, expired or from
                            a previous boot

    The caller (lib/remote.py) owns the side effects: injecting the event into
    lib/routine.py, and persisting `applied_gen` to flash. Nothing here
    allocates a socket or touches I/O; the 2-tuple is the only allocation and
    the reasons are interned strings.
    """
    boot = panel.get("boot")
    last_boot = panel.get("last_boot")
    if boot is not None and last_boot is not None and boot != last_boot:
        return (DECISION_IGNORE, "boot_changed")

    if not desired:
        return (DECISION_NOOP, "no_desired")

    action = desired.get("action")
    if action is None or action == ACTION_NONE:
        return (DECISION_NOOP, "no_desired")

    gen = desired.get("gen")
    # Newer gen only; `<=` is ignored, which makes equality a no-op.
    if gen is None or is_gen_applied(applied_gen, gen):
        return (DECISION_IGNORE, "stale_gen")

    if is_expired(desired, now_epoch_s):
        return (DECISION_IGNORE, "expired")

    event = event_for_desired(desired)
    if event is None:
        return (DECISION_NOOP, "no_desired")

    state = panel.get("state")
    if not is_event_live(state, event):
        if event == EVENT_RESET and state == STATE_AMBIENT:
            return (DECISION_NOOP, "nothing_to_cancel")
        return (DECISION_NOOP, "inert")

    return (DECISION_APPLY, event)


# -- the observed-state report -----------------------------------------------

def build_report(report, boot, fw, applied_gen, state, routine=None,
                 remaining_s=None, rssi=None, uptime_s=None):
    """Fill the observed-state JSON, in place, from one snapshot.

    `report` is a caller-owned dict, allocated ONCE and reused on every poll,
    so the reporting path allocates nothing per poll (memo section 6.3.4, the
    "no per-poll allocation in the reporting path" rule; the 1 Hz allocation
    that was removed from the health sampler must not come back). The fields
    are exactly the `Observed` type in the service's domain-models.ts.

    Optional fields are cleared when not supplied, so a reused dict never
    reports a stale value from the previous poll. Returns `report`.
    """
    report["boot"] = boot
    report["fw"] = fw
    report["applied_gen"] = applied_gen
    report["state"] = state

    if routine is None:
        report.pop("routine", None)
    else:
        report["routine"] = routine

    if remaining_s is None:
        report.pop("remaining_s", None)
    else:
        report["remaining_s"] = remaining_s

    if rssi is None:
        report.pop("rssi", None)
    else:
        report["rssi"] = rssi

    if uptime_s is None:
        report.pop("uptime_s", None)
    else:
        report["uptime_s"] = uptime_s

    return report
