"""Synth helper: the fanfare the board plays when a timer expires.

NOT called `audio.py`. MicroPython v1.29.0 (flashed 2026-09-19) ships a FROZEN
`audio` module (I2S / WavPlayer), and sys.path is ['', '.frozen', '/lib'] -
`.frozen` wins over `/lib`, so a `lib/audio.py` is silently shadowed and
`from audio import Audio` raises ImportError. Do not rename this file back.

No audio files: the fanfare is generated with the board's synth, so it is just
a list of (frequency, seconds) pairs - cheap to add, cheap to change.

Audio is deliberately minimal: the panel is silent when a routine is chosen
and through the whole countdown, and speaks only at the end. One sound, the
same for every routine, so it can only ever mean "time is up".

API verified on this board 2026-09-19:
    channel.configure(WAVEFORM, attack=<s>, decay=<s>, sustain=<0-1>,
                      release=<s>, volume=<0-1>)     <- waveform is positional
    channel.play_tone(freq, seconds)                <- non-blocking, queues
    gu.play_synth() / gu.stop_playing()
Waveform constants live on the *channel* (SINE 8, TRIANGLE 16, SAW 32,
SQUARE 64, NOISE 128), not on GalacticUnicorn.

2026-09-21, changing the note list from a three-note triad to this fanfare: the
DATA and the fail-soft path are covered on the host (tests/test_sound.py), but
the tune itself has NOT been heard on this board - no board was on the wire. To
audition it from the host, `python3 scripts/render-fanfare.py` renders the same
notes and envelope to a .wav; to hear it ON the board, bench-smoke.py plays it
in full. Do that before trusting how it sounds.

2026-09-26, the fast 13-note SQUARE run above read as an alarm - a rapid high
run up and back is what an urgent beep IS. It is now a warm ta-da: a short
pickup into a rising lift into one big held landing note on the tonic, played
on TRIANGLE with a gentler envelope (longer attack/release, quieter). There is
deliberately NO drumroll and NO rapid repeated high note - nothing urgent.
"""

# ruff: noqa: BLE001, S110
#
# This module is deliberately fail-soft. Audio is reinforcement, never a
# dependency: a synth that cannot configure, queue a note or stop must leave
# the *display* working, because a quiet screen must never look like a broken
# one. Every catch here is a blind one on purpose, so a file-level directive
# is the honest spelling rather than three identical inline ones.

# The one sound the board makes: a warm "ta-da" in C, played when a timer
# expires. Deliberately identical for every routine - it means "time is up",
# and one unmistakable sound is easier to learn than three similar ones. The
# child hears it once per routine, at the moment the screen goes green.
#
# The shape is what makes it a ta-da and not an alarm. An alarm is a rapid
# run of high beeps; this is the opposite - a calm pickup, a short rising lift
# through the C major triad to the dominant, and then ONE long landing on the
# tonic that is still ringing while the panel is green. The top note is a
# single held note, never repeated, so the ear hears a resolution rather than
# a squeal. No drumroll, no fast repeats, nothing urgent.
#
# The channel is TRIANGLE with a gentle envelope (see Audio._configure): the
# soft attack and long release make the held landing swell and fade instead of
# poking the ear the way the old square-wave run did. The notes are longer than
# they were (140-950 ms) for the same reason - this is a phrase, not a rattle.
#
# Length is bounded by HANDOFF_MS (config.py): the ta-da is ~1.55 s and the
# green handoff is 10 s, so it always finishes on its own - cancel() is not
# what stops it. Keep it comfortably under HANDOFF_MS when editing, or the
# last note is cut off by the end of the state rather than by the tune.
DONE_SOUND = [
    # -- the pickup: one step below the tonic, leaning into the lift ------
    (392, 0.14),  # G4
    # -- the lift: up the C major triad to the dominant ------------------
    (523, 0.14),  # C5
    (659, 0.14),  # E5
    (784, 0.18),  # G5
    # -- the landing: the tonic, one big held note, resolving -------------
    (1047, 0.95),  # C6 - held, and the only time this pitch is heard
]


class Audio:
    """Owns one synth channel. Silent every failure: audio is reinforcement,
    never a dependency (a quiet display must never look like a broken one)."""

    def __init__(self, display, config):
        self.display = display
        self.config = config
        self.channel = None
        self.muted = False
        if config.AUDIO_ENABLED:
            try:
                self.channel = display.synth_channel(0)
                self._configure()
                display.set_volume(config.VOLUME)
            except Exception:
                self.channel = None

    def _configure(self):
        ch = self.channel
        if ch is None:
            return
        try:
            ch.configure(
                ch.TRIANGLE,  # softer than SQUARE: warm, not an alarm
                attack=0.04,  # gentle swell-in rather than a poke
                decay=0.08,
                sustain=0.85,
                release=0.25,  # long fade on the held landing note
                volume=0.75,
            )
        except Exception:
            pass

    def chime(self):
        """Play the time-is-up ta-da. Returns immediately (play_tone is
        non-blocking, so it queues the whole tune and the display loop keeps
        running)."""
        ch = self.channel
        if ch is None or self.muted:
            return
        try:
            for freq, dur in DONE_SOUND:
                ch.play_tone(int(freq), dur)
            self.display.play_synth()
        except Exception:
            pass

    def stop(self):
        if self.channel is None:
            return
        try:
            self.display.stop_playing()
        except Exception:
            pass

    def toggle_mute(self):
        self.muted = not self.muted
        if self.muted:
            self.stop()
