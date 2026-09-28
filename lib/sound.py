"""Synth helper: the jingle the board plays when a timer expires.

NOT called `audio.py`. MicroPython v1.29.0 (flashed 2026-09-19) ships a FROZEN
`audio` module (I2S / WavPlayer), and sys.path is ['', '.frozen', '/lib'] -
`.frozen` wins over `/lib`, so a `lib/audio.py` is silently shadowed and
`from audio import Audio` raises ImportError. Do not rename this file back.

No audio files: the jingle is generated with the board's synth, so it is just a
list of (frequency, seconds) pairs - cheap to add, cheap to change.

Audio is deliberately minimal: the panel is silent when a routine is chosen
and through the whole countdown, and speaks only at the end. One sound, the
same for every routine, so it can only ever mean "time is up".

WHY THIS FILE SCHEDULES THE TUNE ITSELF (read before "simplifying" it back)
-------------------------------------------------------------------------
`play_tone` is NOT a note. From the frozen module's own binding
(micropython/modules/galactic_unicorn/galactic_unicorn.cpp, `Channel_play_tone`):

    play_tone(frequency, volume=None, attack=None, release=None)

It sets the channel's frequency and volume, forces the waveform to SINE, sets
decay/sustain to a flat hold, and calls `trigger_attack()`. There is **no
duration argument and no queue** - it is a sustained voice, not a note. It
keeps sounding at that pitch until the frequency is changed or the synth is
stopped.

So the obvious-looking version of this module was wrong:

    for freq, dur in DONE_SOUND:
        ch.play_tone(int(freq), dur)   # WRONG

That runs in a few microseconds. Each call retunes the same voice, so the ear
hears exactly ONE tone - the last one (1047 Hz) - held until the handoff
ended, and the "duration" was being passed as the note's *volume*. That is the
"one tone, not a jingle" this file now fixes. The tune is therefore advanced
over time by `tick(now)`, which the engine's frame loop calls; each note is
retuned at its own moment and held for its own duration, and the synth is
released when the landing note has finished ringing.

    chime(now)  arms the tune and starts the synth   (once, at time-up)
    tick(now)   retunes to the next note when its turn comes (every frame)

API verified on this board 2026-09-19:
    channel.configure(WAVEFORM, attack=<s>, decay=<s>, sustain=<0-1>,
                      release=<s>, volume=<0-1>)     <- waveform is positional
    channel.play_tone(freq, volume=None, attack=None, release=None)
    gu.play_synth() / gu.stop_playing()
Waveform constants live on the *channel* (SINE 8, TRIANGLE 16, SAW 32,
SQUARE 64, NOISE 128), not on GalacticUnicorn.

2026-09-28, the jingle was scheduled here for the first time. Until then the
notes were queued in a loop, which on this board is one sustained tone - and
because `play_tone` pins SINE, the TRIANGLE envelope `_configure` asked for
never actually reached the ear either. The tune is unchanged and the note data
is still host-testable (tests/test_sound.py), but the tune itself has still NOT
been heard on this board - no board was on the wire. To audition it from the
host, `python3 scripts/render-fanfare.py` renders the same notes to a .wav; to
hear it ON the board, `scripts/bench-smoke.py` plays it in full. Do that before
trusting how it sounds.

The shape is what makes it a ta-da and not an alarm: a calm pickup, a short
rising lift through the C major triad to the dominant, then ONE long landing on
the tonic. Nothing rapid, nothing repeated, nothing urgent. Length is bounded
by HANDOFF_MS (config.py): the tune is ~1.55 s and the green handoff is 10 s,
so it always finishes on its own - cancel() is not what stops it. Keep it
comfortably under HANDOFF_MS when editing, or the last note is cut off by the
end of the state rather than by the tune.
"""

# ruff: noqa: BLE001, S110
#
# This module is deliberately fail-soft. Audio is reinforcement, never a
# dependency: a synth that cannot configure, retune a note or stop must leave
# the *display* working, because a quiet screen must never look like a broken
# one. Every catch here is a blind one on purpose, so a file-level directive
# is the honest spelling rather than three identical inline ones.

try:  # MicroPython
    from time import ticks_add, ticks_diff
except ImportError:  # CPython, for the host tests

    def ticks_add(a, b):
        return a + b

    def ticks_diff(a, b):
        return a - b


# The one sound the board makes: a warm "ta-da" in C, played when a timer
# expires. Deliberately identical for every routine - it means "time is up",
# and one unmistakable sound is easier to learn than three similar ones. The
# child hears it once per routine, at the moment the screen goes green.
#
# The shape is what makes it a ta-da and not an alarm. An alarm is a rapid run
# of high beeps; this is the opposite - a calm pickup, a short rising lift
# through the C major triad to the dominant, and then ONE long landing on the
# tonic that is still ringing while the panel is green. The top note is a
# single held note, never repeated, so the ear hears a resolution rather than a
# squeal. No drumroll, no fast repeats, nothing urgent. The notes are long
# (140-950 ms) for the same reason - this is a phrase, not a rattle.
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

# Each note is retuned with an explicit volume. This is not decoration:
# `play_tone`'s volume defaults to FULL SCALE when omitted, so leaving it off
# would make the jingle as loud as the channel can go.
NOTE_VOLUME = 0.75


class Audio:
    """Owns one synth channel and the clock that walks the tune. Silent on
    every failure: audio is reinforcement, never a dependency (a quiet display
    must never look like a broken one)."""

    def __init__(self, display, config):
        self.display = display
        self.config = config
        self.channel = None
        self.muted = False
        self._seq = None  # the armed tune, or None when silent
        self._idx = 0  # index of the note currently sounding
        self._due = 0  # ticks at which the next note takes over
        if config.AUDIO_ENABLED:
            try:
                self.channel = display.synth_channel(0)
                self._configure()
                display.set_volume(config.VOLUME)
            except Exception:
                self.channel = None

    def _configure(self):
        """Set the channel's defaults. Note that every one of these is a
        *default*: `play_tone` pins the waveform to SINE and re-arms the
        envelope on each note (see the module docstring), so the waveform named
        here is truthful only because play_tone forces the same one."""
        ch = self.channel
        if ch is None:
            return
        try:
            ch.configure(
                ch.SINE,  # the softest waveform: a tone, not a buzzer
                attack=0.04,  # gentle swell-in rather than a poke
                decay=0.08,
                sustain=0.85,
                release=0.25,  # long fade on the held landing note
                volume=NOTE_VOLUME,
            )
        except Exception:
            pass

    def chime(self, now):
        """Arm the time-is-up jingle. Returns immediately: the notes are played
        by `tick(now)` from the frame loop, so the display keeps running (the
        old queueing version was heard as one sustained tone - see the module
        docstring)."""
        if self.channel is None or self.muted:
            return
        self._seq = DONE_SOUND
        self._idx = -1  # the first tick plays note 0
        self._due = now
        try:
            self.display.play_synth()
        except Exception:
            self._seq = None

    def tick(self, now):
        """Walk one step of the tune. Called every frame; a no-op when silent.

        A note holds for its own duration, so most ticks do nothing at all -
        this only retunes the voice when the next note is due."""
        seq = self._seq
        if seq is None:
            return
        if self._idx >= 0 and ticks_diff(now, self._due) < 0:
            return  # still holding the current note
        self._idx += 1
        if self._idx >= len(seq):
            # The landing note has rung for its full length: release the voice
            # rather than let it sustain silently under the green screen.
            self._seq = None
            self.stop()
            return
        freq, dur = seq[self._idx]
        self._due = ticks_add(now, int(dur * 1000))
        try:
            self.channel.play_tone(int(freq), NOTE_VOLUME)
        except Exception:
            self._seq = None

    def silence(self):
        """Drop the armed tune without touching the synth (used by stop())."""
        self._seq = None

    def stop(self):
        self.silence()
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
