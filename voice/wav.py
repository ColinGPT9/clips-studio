"""WAV files, with the standard library only.

Every engine hands the layer speech in its own way: Kokoro as float samples,
Piper straight into a file, a local voice app as bytes over HTTP. All of it
ends as one kind of file: 16-bit PCM WAV.
"""

from __future__ import annotations

import io
import os
import subprocess
import wave
from pathlib import Path

# Formats a local server may send although WAV was asked for (OpenAI's
# default is mp3). They are converted with the bundled FFmpeg; anything that
# is not audio at all is refused.
_OTHER_AUDIO = (b"ID3", b"fLaC", b"OggS")


class NotAudio(ValueError):
    """The bytes are not a sound file."""


def info(path: Path) -> tuple[int, float]:
    """(sample rate, seconds) of a PCM WAV file."""
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        return rate, (w.getnframes() / rate if rate else 0.0)


def write_samples(path: Path, samples, rate: int) -> None:
    """Float samples (-1..1, mono) as 16-bit PCM.

    The same numbers python-soundfile writes for PCM_16 (each sample times
    32768, rounded down, held to the 16-bit range), so files made before the
    voice layer and after it are the same bytes.
    """
    import numpy as np

    pcm = np.clip(np.floor(np.asarray(samples, dtype=np.float64) * 32768.0), -32768, 32767).astype("<i2")
    _write(path, [pcm.tobytes()], 1, 2, int(rate))


# Quieter than this (a thousandth of full level, the same line the built-in
# voice draws for its own output) is silence, not speech.
SILENT_BELOW = 33


def peak(path: Path) -> int | None:
    """The loudest sample of a PCM WAV on the 16-bit scale (0..32768), or
    None when the file can't be read. Standard library only."""
    import sys
    from array import array

    try:
        with wave.open(str(path), "rb") as w:
            width = w.getsampwidth()
            frames = w.readframes(w.getnframes())
    except (OSError, wave.Error, EOFError, ValueError):
        return None
    if width == 1:  # 8-bit WAV is unsigned, with silence at 128
        return max((abs(v - 128) for v in frames), default=0) * 256
    if width not in (2, 3, 4):
        return None
    frames = frames[: len(frames) - len(frames) % width]
    if not frames:
        return 0
    # The top two bytes of every sample are its 16-bit value.
    top = bytearray(2 * (len(frames) // width))
    top[0::2] = frames[width - 2::width]
    top[1::2] = frames[width - 1::width]
    samples = array("h")
    samples.frombytes(bytes(top))
    if sys.byteorder == "big":
        samples.byteswap()
    return max(max(samples), -min(samples))


def is_silent(path: Path) -> bool:
    """True for a WAV with no sound in it (an unreadable file is not called
    silent: that is another fault)."""
    loudest = peak(path)
    return loudest is not None and loudest < SILENT_BELOW


def to_full_level(path: Path, most: float = 10.0) -> bool:
    """Make a 16-bit PCM WAV as loud as it can be without changing its sound:
    every sample times the same number, so the loudest one reaches the top.

    That is the level Piper hands dubbing each sentence at. `most` caps the
    gain (20 dB), so a take that is next to silent is not turned into noise.
    True when the file was rewritten; a file that is not 16-bit PCM, is
    silent, or is already there is left as it is. Standard library only.
    """
    import sys
    from array import array

    path = Path(path)
    try:
        with wave.open(str(path), "rb") as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            frames = w.readframes(w.getnframes())
    except (OSError, wave.Error, EOFError, ValueError):
        return False
    if width != 2 or not frames:
        return False
    samples = array("h")
    samples.frombytes(frames[: len(frames) - len(frames) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    peak = max(max(samples), -min(samples))
    if peak <= 0:
        return False
    gain = min(float(most), 32767.0 / peak)
    if gain <= 1.01:
        return False
    louder = array("h", [max(-32768, min(32767, int(round(v * gain)))) for v in samples])
    if sys.byteorder == "big":
        louder.byteswap()
    _write(path, [louder.tobytes()], channels, 2, rate)
    return True


def _write(path: Path, chunks: list[bytes], channels: int, width: int, rate: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with wave.open(str(part), "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(width)
            w.setframerate(rate)
            for chunk in chunks:
                w.writeframes(chunk)
        os.replace(part, path)
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass


def _is_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _is_other_audio(data: bytes) -> bool:
    if any(data.startswith(magic) for magic in _OTHER_AUDIO):
        return True
    # A bare MPEG audio frame: eleven set bits, then a layer that is not 0.
    return len(data) > 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0 and (data[1] & 0x06) != 0


def _read_pcm(data: bytes) -> tuple[bytes, int, int, int] | None:
    """(frames, channels, sample width, rate) of a PCM WAV, or None when it
    is a WAV the standard library cannot read (float samples, for one)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            # A streamed WAV may claim a length it does not have (0xFFFFFFFF);
            # readframes hands back what is really there.
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError, ValueError):
        return None
    if channels not in (1, 2) or width not in (1, 2, 3, 4) or not 4000 <= rate <= 192000:
        return None
    frames = frames[: len(frames) - len(frames) % (channels * width)]
    return frames, channels, width, rate


def _convert(data: bytes, out: Path) -> None:
    """Any audio FFmpeg can read, as 16-bit PCM WAV at `out`."""
    from core.binaries import ffmpeg

    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            [ffmpeg(), "-y", "-v", "error", "-nostdin", "-i", "pipe:0", "-vn", "-c:a", "pcm_s16le",
             "-f", "wav", str(out)],
            input=data, capture_output=True, timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise NotAudio(f"the sound could not be read ({e.__class__.__name__})") from None
    if r.returncode != 0 or not out.exists() or out.stat().st_size <= 44:
        raise NotAudio("the sound could not be read")


def save_answers(answers: list[bytes], out: Path) -> tuple[int, float]:
    """The sound files a server sent, in order, as one PCM WAV at `out`.

    Returns (sample rate, seconds). NotAudio when a piece is not a sound
    file (an HTML error page with a 200, say), is empty, or does not match
    the pieces before it.
    """
    out = Path(out)
    pieces: list[tuple[bytes, int, int, int]] = []
    for n, data in enumerate(answers):
        got = _read_pcm(data) if _is_wav(data) else None
        if got is None:
            if not (_is_wav(data) or _is_other_audio(data)):
                raise NotAudio("the voice app did not send a sound file")
            scratch = out.with_name(f"{out.name}.{n}.conv.wav")
            try:
                _convert(data, scratch)
                got = _read_pcm(scratch.read_bytes())
            finally:
                if scratch.exists():
                    try:
                        scratch.unlink()
                    except OSError:
                        pass
            if got is None:
                raise NotAudio("the sound could not be read")
        if not got[0]:
            raise NotAudio("the voice app sent a sound file with nothing in it")
        if pieces and got[1:] != pieces[0][1:]:
            raise NotAudio("the voice app changed its sound format part-way through")
        pieces.append(got)
    if not pieces:
        raise NotAudio("the voice app sent nothing")
    _frames, channels, width, rate = pieces[0]
    _write(out, [p[0] for p in pieces], channels, width, rate)
    total = sum(len(p[0]) for p in pieces) // (channels * width)
    return rate, total / rate
