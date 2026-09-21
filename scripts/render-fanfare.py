#!/usr/bin/env python3
"""Render the time-is-up fanfare to a .wav, so a note list can be heard from
the host.

    python3 scripts/render-fanfare.py [out.wav]      # default fanfare.wav

There is usually no board on the wire when a note list is being edited, and
"does this sound exciting" is not a question a unit test can answer. This
renders lib/sound.DONE_SOUND with the same square wave and the same envelope
lib/sound.Audio configures on the synth, so a change can be auditioned before
it is flashed.

A PREVIEW, NOT A MEASUREMENT: this is an off-board approximation of a synth
whose implementation lives in the board's firmware. Frequencies and durations
are exact (they are the data); the *timbre* is close, not identical - the
board's envelope generator and its tiny speaker are the real thing, and this
is a laptop speaker standing in. Treat it as a way to hear the shape of a
tune, not as a way to prove what the board will do.
"""

import os
import struct
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, ROOT)

import config
import sound

RATE = 22050

# The envelope Audio._configure sets on the channel, and the amplitudes it
# ends up at: channel volume x the board's global volume.
ATTACK, DECAY, SUSTAIN, RELEASE = 0.01, 0.08, 0.85, 0.12
CHANNEL_VOLUME = 0.8


def envelope(t, dur):
    """ADSR at time t into a note of length dur, scaled to fit the note.

    The fanfare's notes (80-90 ms) are all shorter than attack + decay, so the
    sustain/release part barely appears in practice - which is exactly why the
    notes read as plucks and why the run has rhythm rather than smearing into
    one tone.
    """
    if dur <= 0:
        return 0.0
    attack = min(ATTACK, dur)
    decay = min(DECAY, max(dur - attack, 0.0))
    release = min(RELEASE, max(dur - attack - decay, 0.0))
    hold = max(dur - attack - decay - release, 0.0)

    if t < attack:
        return t / attack
    t -= attack
    if t < decay:
        return 1.0 - (1.0 - SUSTAIN) * (t / decay)
    t -= decay
    if t < hold:
        return SUSTAIN
    t -= hold
    if release and t < release:
        return SUSTAIN * (1.0 - t / release)
    return 0.0


def render(notes, rate=RATE):
    samples = []
    amp = CHANNEL_VOLUME * config.VOLUME * 32767
    for freq, dur in notes:
        n = int(dur * rate)
        for i in range(n):
            t = i / rate
            phase = (freq * t) % 1.0
            square = 1.0 if phase < 0.5 else -1.0
            samples.append(int(amp * envelope(t, dur) * square))
    return samples


def main(argv):
    out = argv[1] if len(argv) > 1 else "fanfare.wav"
    notes = sound.DONE_SOUND
    samples = render(notes)

    with wave.open(out, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(RATE)
        fh.writeframes(b"".join(struct.pack("<h", s) for s in samples))

    total = sum(dur for _, dur in notes)
    print(f"{out}: {len(notes)} notes, {total:.2f}s, {len(samples)} samples @ {RATE} Hz")
    print(f"  peak {max(abs(s) for s in samples) / 32767:.2f} of full scale")
    print(f"  notes: {' '.join(str(f) for f, _ in notes)}")
    if total * 1000 > config.HANDOFF_MS * 0.5:
        print("  WARNING: over half of HANDOFF_MS - the ending may be cut off")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
