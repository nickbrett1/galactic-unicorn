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
"""

# ruff: noqa: BLE001, S110
#
# This module is deliberately fail-soft. Audio is reinforcement, never a
# dependency: a synth that cannot configure, queue a note or stop must leave
# the *display* working, because a quiet screen must never look like a broken
# one. Every catch here is a blind one on purpose, so a file-level directive
# is the honest spelling rather than three identical inline ones.

# The one sound the board makes: a short fanfare in C, played when a timer
# expires. Deliberately identical for every routine - it means "time is up",
# and one unmistakable sound is easier to learn than three similar ones. The
# child hears it once per routine, at the moment the screen goes green.
#
# It used to be three long notes, which read as "a tone" rather than as a
# tune: nothing in it moved, so nothing in it celebrated. This is the same
# idea - one sound, earnable, the same every time - with a shape instead of
# a pitch: a fast run up, a hammer on the top (a single hit, and the only note
# that high, so the ear hears it as a peak), an answer back down, and a landing
# that is still ringing while the panel is green. Notes are short (80-90 ms)
# because the channel's envelope decays fast (see Audio._configure); at this
# length every note is a pluck, which is what makes the run read as rhythm
# rather than as a smear.
#
# Length is bounded by HANDOFF_MS (config.py): the fanfare is ~1.7 s and the
# green handoff is 10 s, so it always finishes on its own - cancel() is not
# what stops it. Keep it comfortably under HANDOFF_MS when editing, or the
# last note is cut off by the end of the state rather than by the tune.
DONE_SOUND = [
    # -- the run: four quick notes up the C major triad -------------------
    (659, 0.09),  # E5
    (784, 0.09),  # G5
    (1047, 0.09),  # C6
    (1319, 0.09),  # E6
    # -- the hammer: up again, wider, and the peak is a single hit --------
    (1319, 0.09),  # E6
    (1568, 0.09),  # G6
    (2093, 0.18),  # C7 - the top of the fanfare, and the only note up here
    # -- the answer: back down the triad, even and quick ------------------
    (1568, 0.08),  # G6
    (1319, 0.08),  # E6
    (1047, 0.08),  # C6
    # -- the turn: one step back up, so the landing has somewhere to fall to
    (1319, 0.08),  # E6
    (1568, 0.08),  # G6
    # -- the landing: low and held, resolving onto the tonic --------------
    (1047, 0.55),  # C6
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
                ch.SQUARE,
                attack=0.01,
                decay=0.08,
                sustain=0.85,
                release=0.12,
                volume=0.8,
            )
        except Exception:
            pass

    def chime(self):
        """Play the time-is-up fanfare. Returns immediately (play_tone is
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
