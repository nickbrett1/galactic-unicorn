"""Synth helper: the one chime the board plays when a timer expires.

NOT called `audio.py`. MicroPython v1.29.0 (flashed 2026-09-19) ships a FROZEN
`audio` module (I2S / WavPlayer), and sys.path is ['', '.frozen', '/lib'] -
`.frozen` wins over `/lib`, so a `lib/audio.py` is silently shadowed and
`from audio import Audio` raises ImportError. Do not rename this file back.

No audio files: the chime is generated with the board's synth, so it is just
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
"""

# ruff: noqa: BLE001, S110
#
# This module is deliberately fail-soft. Audio is reinforcement, never a
# dependency: a synth that cannot configure, queue a note or stop must leave
# the *display* working, because a quiet screen must never look like a broken
# one. Every catch here is a blind one on purpose, so a file-level directive
# is the honest spelling rather than three identical inline ones.

# The one sound the board makes: a bright rising triad, played when a timer
# expires. Deliberately identical for every routine - it means "time is up",
# and one unmistakable sound is easier to learn than three similar ones. The
# child hears it once per routine, at the moment the screen goes green.
DONE_SOUND = [(784, 0.11), (988, 0.11), (1319, 0.35)]  # G5 B5 E6, rising


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
        """Play the time-is-up sound. Returns immediately (play_tone is
        non-blocking, so it queues the whole triad and the display loop keeps
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
