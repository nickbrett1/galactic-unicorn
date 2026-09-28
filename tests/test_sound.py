#!/usr/bin/env python3
"""Regression tests for the time-is-up fanfare. Host-only, no board.

    python3 tests/test_sound.py

The fanfare is a list of (frequency, seconds) pairs and a tune the frame loop
walks with `tick(now)`, so a fake channel can record what the board would have
played - and, just as importantly, *when*. What is under test is that it reads
as a warm *ta-da* - it lifts, it peaks once, it lands, and it is never rapid or
repeated like an alarm - and that a synth which misbehaves still cannot take the
display down with it.

The two properties that are easy to lose by editing a note list, and invisible
without a board, are the ones asserted hardest: the tune must still fit inside
HANDOFF_MS, and chime()/tick() must never raise.

`case_plays_over_time` is the regression test for the real board bug: the tune
was once queued in a loop, which this synth does NOT do - `play_tone` retunes a
single sustained voice, so the whole "tune" arrived as one held tone (the last
note). That case fails if the notes are ever fired all at one instant again.
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

    def play_tone(self, freq, volume=None):
        # The real signature is (frequency, volume=None, attack=None,
        # release=None) - there is no duration and no queue. Recording the
        # volume too pins that we always pass one: omitting it means FULL
        # SCALE on the board, not "quiet".
        if self.fail_on_play:
            raise OSError(12)  # ENOMEM: the failure this board actually had
        self.tones.append((freq, volume))


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


def _walk(audio, channel, start=0, span_ms=4000, step_ms=10):
    """Drive the frame loop and report (freqs, when-each-note-started)."""
    at = []
    for t in range(start, start + span_ms, step_ms):
        before = len(channel.tones)
        audio.tick(t)
        if len(channel.tones) > before:
            at.append(t - start)
    return [f for f, _ in channel.tones], at


def case_plays_over_time():
    audio, display, channel = build()
    audio.chime(0)
    freqs, at = _walk(audio, channel)
    # The bug this pins: all five notes must NOT land on one instant. They are
    # a phrase, spread over the tune's length, in order.
    ok = (
        freqs == [int(f) for f, _ in sound.DONE_SOUND]
        and len(at) == len(sound.DONE_SOUND)
        and at[0] == 0
        and at[-1] >= 500
        and display.plays == 1
    )
    return _report(
        "chime arms the tune and tick plays the notes one at a time",
        ok,
        f"{len(at)} notes over {at[-1] if at else 0} ms, play_synth x{display.plays}",
    )


def case_every_note_held_for_its_own_duration():
    audio, _display, channel = build()
    audio.chime(0)
    _, at = _walk(audio, channel)
    # Note n+1 must arrive one note-n duration after note n - that is what
    # makes them separate notes instead of one sustained tone.
    expected = [0]
    for _freq, dur in sound.DONE_SOUND[:-1]:
        expected.append(expected[-1] + int(dur * 1000))
    # The walk steps in 10 ms frames, so a note lands within one step.
    ok = len(at) == len(expected) and all(
        abs(got - want) <= 10 for got, want in zip(at, expected)
    )
    return _report(
        "each note holds for its own duration before the next one",
        ok,
        f"started at {at} ms, wanted {expected} ms",
    )


def case_releases_when_the_tune_ends():
    audio, display, channel = build()
    audio.chime(0)
    _walk(audio, channel, span_ms=4000)
    total_ms = int(sum(dur for _, dur in sound.DONE_SOUND) * 1000)
    # The landing note rings for its full length and is then released, rather
    # than droning on under the green screen for the rest of the handoff.
    ok = display.stops == 1
    return _report(
        "the synth is released once the landing note has rung",
        ok,
        f"stops={display.stops}, tune {total_ms} ms of {config.HANDOFF_MS} ms handoff",
    )


def case_notes_carry_an_explicit_volume():
    audio, _display, channel = build()
    audio.chime(0)
    _walk(audio, channel)
    # Omitting it is not "quiet" - play_tone defaults to full scale.
    ok = channel.tones and all(vol == sound.NOTE_VOLUME for _f, vol in channel.tones)
    return _report(
        "every note is retuned with an explicit volume",
        ok,
        f"volumes {sorted({vol for _f, vol in channel.tones})}",
    )


def case_configured_soft():
    _, _, channel = build()
    waveform, kwargs = channel.configured
    # SINE, not SQUARE: the softer waveform is half of "ta-da, not alarm".
    ok = waveform == channel.SINE and kwargs.get("volume") == sound.NOTE_VOLUME
    return _report(
        "channel is configured sine (soft), positional waveform", ok, f"{kwargs}"
    )


def case_play_tone_failure_is_silent():
    audio, _display, _channel = build(fail_on_play=True)
    try:
        audio.chime(0)
        for t in range(0, 3000, 10):  # the failure lands on the first tick
            audio.tick(t)
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
        audio.chime(0)
        for t in range(0, 3000, 10):
            audio.tick(t)
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
    audio.chime(0)
    _walk(audio, channel)
    ok = channel.tones == [] and display.plays == 0
    return _report("muted board plays nothing", ok, f"{len(channel.tones)} notes")


def case_mute_mid_tune_stops_it():
    audio, display, channel = build()
    audio.chime(0)
    _walk(audio, channel, span_ms=200)  # let a note or two out
    mid = len(channel.tones)
    audio.toggle_mute()  # the parent's escape hatch, mid-jingle
    audio.tick(6000)
    ok = mid > 0 and len(channel.tones) == mid and display.stops == 1
    return _report(
        "muting mid-tune silences the rest of it", ok, f"{mid} notes then a stop"
    )


def case_audio_disabled_is_quiet():
    audio, _display, channel = build(audio_enabled=False)
    audio.chime(0)
    _walk(audio, channel)
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
        case_plays_over_time(),
        case_every_note_held_for_its_own_duration(),
        case_releases_when_the_tune_ends(),
        case_notes_carry_an_explicit_volume(),
        case_configured_soft(),
        case_play_tone_failure_is_silent(),
        case_play_synth_failure_is_silent(),
        case_muted_is_quiet(),
        case_mute_mid_tune_stops_it(),
        case_audio_disabled_is_quiet(),
        case_stop_is_safe_without_channel(),
    ]
    print()
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
