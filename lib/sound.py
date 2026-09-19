"""Synth helpers and the per-routine motifs.

NOT called `audio.py`. MicroPython v1.29.0 (flashed 2026-09-19) ships a FROZEN
`audio` module (I2S / WavPlayer), and sys.path is ['', '.frozen', '/lib'] -
`.frozen` wins over `/lib`, so a `lib/audio.py` is silently shadowed and
`from audio import Audio` raises ImportError. Do not rename this file back.

No audio files: everything is generated with the board's synth, so a motif is
a list of (frequency, seconds) pairs - cheap to add, cheap to change.

The same motif plays twice per routine: ascending at PROMPT (the heads-up)
and resolved/brighter at HANDOFF (the go). Repetition is the point - it wires
the sound to what is about to happen.

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

# Distinct intervals and rhythms per routine, so the tune alone identifies it.
# The child does not have to be looking at the screen.
MOTIFS = {
    "motif1": [(523, 0.10), (659, 0.10), (784, 0.18)],  # duck: rising thirds
    "motif2": [(587, 0.12), (698, 0.12), (880, 0.20)],  # book: wider rising
    "motif3": [(659, 0.08), (659, 0.08), (988, 0.20)],  # cleanup: double tap
}

# Handoff resolves the same motif an octave up, on a brighter waveform.
_HANDOFF_LIFT = 2  # multiply the final note by 2 ** this


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
                self._configure("prompt")
                display.set_volume(config.VOLUME)
            except Exception:
                self.channel = None

    def _configure(self, variant):
        ch = self.channel
        if ch is None:
            return
        waveform = ch.SQUARE if variant == "handoff" else ch.TRIANGLE
        try:
            ch.configure(
                waveform,
                attack=0.01,
                decay=0.08,
                sustain=0.85,
                release=0.12,
                volume=0.8,
            )
        except Exception:
            pass

    def play(self, tune_name, variant="prompt"):
        """Queue a motif. Returns immediately (play_tone is non-blocking)."""
        ch = self.channel
        if ch is None or self.muted:
            return
        notes = MOTIFS.get(tune_name)
        if not notes:
            return
        self._configure(variant)
        try:
            for i, (freq, dur) in enumerate(notes):
                if variant == "handoff" and i == len(notes) - 1:
                    freq = freq * (2 ** _HANDOFF_LIFT)
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
