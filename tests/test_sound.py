#!/usr/bin/env python3
"""Regression tests for the time-is-up fanfare. Host-only, no board.

    python3 tests/test_sound.py

The fanfare is a list of (frequency, seconds) pairs and a queue-and-go call,
so a fake channel can record what the board would have played. What is under
test is that it reads as a warm *ta-da* - it lifts, it peaks once, it lands,
and it is never rapid or repeated like an alarm - and that a synth which
misbehaves still cannot take the display down with it.

The two properties that are easy to lose by editing a note list, and invisible
without a board, are the ones asserted hardest: the tune must still fit inside
HANDOFF_MS, and chime() must never raise.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, ROOT)

import config
import sound


class FakeChannel:
    """Records what the board's synth would have been asked to do."""

    SINE, TRIANGLE, SAW, SQUARE, NOISE = 8, 16, 32, 64, 128

    def __init__(self):
        self.configured = None
        self.tones = []
        self.fail_on_play = False

    def configure(self, waveform, **kwargs):
        self.configured = (waveform, kwargs)

    def play_tone(self, freq, seconds):
        if self.fail_on_play:
            raise OSError(12)  # ENOMEM: the failure this board actually had
        self.tones.append((freq, seconds))


class FakeDisplay:
    def __init__(self, channel):
        self.channel = channel
        self.plays = 0
        self.stops = 0
        self.fail_on_play_synth = False

    def synth_channel(self, n):
        return self.channel

    def play_synth(self):
        self.plays += 1
        if self.fail_on_play_synth:
            raise RuntimeError("synth gone")

    def stop_playing(self):
        self.stops += 1

    def set_volume(self, v):
        self.volume = v


class FakeConfig:
    AUDIO_ENABLED = True
    VOLUME = 0.5


def build(audio_enabled=True, fail_on_play=False, fail_on_play_synth=False):
    channel = FakeChannel()
    channel.fail_on_play = fail_on_play
    display = FakeDisplay(channel)
    display.fail_on_play_synth = fail_on_play_synth
    cfg = FakeConfig()
    cfg.AUDIO_ENABLED = audio_enabled
    return sound.Audio(display, cfg), display, channel


# -- the tune itself, as data ------------------------------------------------


def case_wellformed():
    ok = True
    detail = []
    for freq, dur in sound.DONE_SOUND:
        if not isinstance(freq, int) or freq <= 0:
            ok = False
            detail.append(f"bad freq {freq!r}")
        if not isinstance(dur, (int, float)) or dur <= 0:
            ok = False
            detail.append(f"bad duration {dur!r}")
    return _report("every note is an int hz and a positive duration", ok, "; ".join(detail))


def case_fits_handoff():
    total_ms = sum(dur for _, dur in sound.DONE_SOUND) * 1000
    # A margin, not just "under": the last note must finish rather than be
    # truncated by the state ending.
    ok = total_ms < config.HANDOFF_MS * 0.5
    return _report(
        "tune fits inside the handoff with room to spare",
        ok,
        f"{total_ms / 1000:.2f}s of {config.HANDOFF_MS / 1000:.0f}s handoff",
    )


def case_moves():
    notes = [f for f, _ in sound.DONE_SOUND]
    # The ta-da is deliberately short - a pickup, a three-note lift and one
    # landing (5 notes, 5 pitches) - so this pins "more than a tone" without
    # demanding the length of the fast run it replaced.
    ok = len(notes) >= 5 and len(set(notes)) >= 4
    return _report(
        "the tune has enough notes and pitches to be a phrase",
        ok,
        f"{len(notes)} notes, {len(set(notes))} distinct",
    )


def case_no_alarm():
    notes = [f for f, _ in sound.DONE_SOUND]
    durs = [d for _, d in sound.DONE_SOUND]
    # An alarm is a rapid run of repeated high beeps. Forbid the two things that
    # make one: no note is a rapid tick, and no pitch is repeated back to back
    # (the held landing is a single entry, so it cannot trip the repeat check).
    rapid = min(durs) < 0.1
    repeats = any(a == b for a, b in zip(notes, notes[1:]))
    ok = not rapid and not repeats
    return _report(
        "nothing rapid, no immediate repeats: a ta-da, not an alarm",
        ok,
        f"min {min(durs):.2f}s, repeats={repeats}",
    )


def case_has_rising_run():
    notes = [f for f, _ in sound.DONE_SOUND]
    run = best = 1
    for a, b in zip(notes, notes[1:]):
        run = run + 1 if b > a else 1
        best = max(best, run)
    ok = best >= 4
    return _report("opens with a rising run of at least 4 notes", ok, f"longest run {best}")


def case_peaks_once():
    notes = [f for f, _ in sound.DONE_SOUND]
    top = max(notes)
    ok = notes.count(top) == 1
    return _report(
        "the highest note is a single hit, not a held squeal",
        ok,
        f"top {top} Hz x{notes.count(top)}",
    )


def case_lands():
    notes = [f for f, _ in sound.DONE_SOUND]
    durs = [d for _, d in sound.DONE_SOUND]
    # The landing is the tonic of the run (the run is an arpeggio up the C
    # triad, so C is a multiple of 523 rounded to the synth's integer hz) and
    # it is the longest note - it is what the green panel rings under.
    ok = notes[-1] % 523 <= 1 and durs[-1] == max(durs)
    return _report(
        "lands on the run's tonic, as the longest note",
        ok,
        f"last {notes[-1]} Hz for {durs[-1]}s, longest {max(durs)}s",
    )


# -- the call itself ---------------------------------------------------------


def case_queues_in_order():
    audio, display, channel = build()
    audio.chime()
    ok = channel.tones == [(int(f), d) for f, d in sound.DONE_SOUND] and display.plays == 1
    return _report(
        "chime queues every note in order, then starts the synth once",
        ok,
        f"{len(channel.tones)} notes, play_synth x{display.plays}",
    )


def case_configured_triangle():
    _, _, channel = build()
    waveform, kwargs = channel.configured
    # TRIANGLE, not SQUARE: the warmer waveform is half of "ta-da, not alarm".
    ok = waveform == channel.TRIANGLE and kwargs.get("volume") == 0.75
    return _report(
        "channel is configured triangle (warm), positional waveform", ok, f"{kwargs}"
    )


def case_play_tone_failure_is_silent():
    audio, _display, _channel = build(fail_on_play=True)
    try:
        audio.chime()
        ok = True
    except Exception as exc:  # noqa: BLE001 - the point is that nothing escapes
        ok = False
        exc_name = type(exc).__name__
    return _report(
        "a play_tone that raises is swallowed, not propagated",
        ok,
        "" if ok else f"raised {exc_name}",
    )


def case_play_synth_failure_is_silent():
    audio, _display, _channel = build(fail_on_play_synth=True)
    try:
        audio.chime()
        ok = True
    except Exception as exc:  # noqa: BLE001
        ok = False
        exc_name = type(exc).__name__
    return _report(
        "a play_synth that raises is swallowed, not propagated",
        ok,
        "" if ok else f"raised {exc_name}",
    )


def case_muted_is_quiet():
    audio, display, channel = build()
    audio.toggle_mute()
    audio.chime()
    ok = channel.tones == [] and display.plays == 0
    return _report("muted board queues nothing", ok, f"{len(channel.tones)} notes")


def case_audio_disabled_is_quiet():
    audio, _display, channel = build(audio_enabled=False)
    audio.chime()
    ok = audio.channel is None and channel.tones == []
    return _report("AUDIO_ENABLED False -> no channel, chime is a no-op", ok, "")


def case_stop_is_safe_without_channel():
    audio, _display, _channel = build(audio_enabled=False)
    try:
        audio.stop()
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    return _report("stop() is safe when there is no channel", ok, "")


# -- runner ------------------------------------------------------------------


def _report(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL':<4} {label:<52} {detail}")
    return ok


def main():
    results = [
        case_wellformed(),
        case_fits_handoff(),
        case_moves(),
        case_no_alarm(),
        case_has_rising_run(),
        case_peaks_once(),
        case_lands(),
        case_queues_in_order(),
        case_configured_triangle(),
        case_play_tone_failure_is_silent(),
        case_play_synth_failure_is_silent(),
        case_muted_is_quiet(),
        case_audio_disabled_is_quiet(),
        case_stop_is_safe_without_channel(),
    ]
    print()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
