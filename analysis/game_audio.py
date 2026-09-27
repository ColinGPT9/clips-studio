"""What the game sounds like, for the gaming profile.

Chat and the streamer's voice say something happened; the game's own sound
often says what: gunfire and explosions in a fight, a stadium crowd roaring
at a goal, a crash in a race, a scream in a horror game. A quiet streamer's
big play still sounds like one.

A sound tagger (analysis/panns.py, trained on AudioSet's 527 everyday
sounds) listens to every second, over a 2-second window, and its classes
are pooled into groups (config/gaming.yaml's sound_groups). On a stream it
mostly hears the streamer's voice and music, so a game sound is judged
against its own level in this stream, the way chat_moments judges a chat
burst: Rocket League's goal explosions score about 0.25, an Apex fight's
gunfire 0.5-0.8, and both are the loudest of their kind in their stream.

listen() is the expensive half (decode + tagging, run beside Whisper: about
600x realtime on a GPU, 120x on a CPU). sound_signal() turns what it heard
into the game channel's evidence and events, weighted for the kind of game.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from analysis import panns

CHUNK_SECONDS = 60     # decoded at a time: a 3-hour VOD never sits in memory
BASELINE = 90          # seconds either side that set a sound's usual level here
MIN_PROB = 0.1         # below this the tagger isn't really hearing it
MIN_REF = 0.2          # "as strong as it gets" is never set lower than this, so a
                       # stream with no explosions doesn't turn its loudest bang into one
EVENT = 0.5            # strength at which a sound is named to the model
MERGE_SECONDS = 10     # a goal and its replay, a fight's bursts: one moment
MAX_EVENTS = 60
DEFAULT_WEIGHT = 0.5   # a sound group a genre doesn't list


@dataclass
class GameSounds:
    """Per second 0..1: `game` for sounds the game makes (gunfire, a goal),
    `people` for the streamer's or friends' (a scream, laughter). events:
    (second, "GAME SOUND: ...") for the model."""
    game: np.ndarray
    people: np.ndarray
    events: list = field(default_factory=list)


def _pcm_chunks(path: Path, seconds: int):
    """Mono 32 kHz float32 audio, `seconds` at a time."""
    from core.binaries import ffmpeg

    proc = subprocess.Popen(
        [ffmpeg(), "-v", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(panns.SAMPLE_RATE), "-f", "f32le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = seconds * panns.SAMPLE_RATE * 4
    try:
        while True:
            buf = proc.stdout.read(size)
            if not buf:
                break
            yield np.frombuffer(buf[: len(buf) // 4 * 4], dtype=np.float32)
    finally:
        proc.stdout.close()
        proc.wait()


def listen(path: Path, groups: dict, tag=None, chunks=None) -> dict[str, np.ndarray] | None:
    """{group: per-second probability} over the whole video: for second s,
    the strongest class of the group in the 2 s around it. None when there
    is no audio. `tag` and `chunks` stand in for the model and the decoder
    in tests."""
    tag = tag or panns.tag
    names = {n: i for i, n in enumerate(panns.labels())}
    index = {g: [names[c] for c in spec.get("classes") or [] if c in names] for g, spec in groups.items()}
    index = {g: ix for g, ix in index.items() if ix}
    if not index:
        return None

    sr = panns.SAMPLE_RATE
    half, width = sr // 2, 2 * sr
    out: dict[str, list] = {g: [] for g in index}
    # The window for second s spans [s - 0.5, s + 1.5): half a second of
    # silence stands in before the start and after the end.
    buf = np.zeros(half, dtype=np.float32)
    start = -half                 # sample offset of buf[0]
    total = 0
    s = 0

    def run(windows: list) -> None:
        if windows:
            probs = tag(np.stack(windows))
            for g, ix in index.items():
                out[g].append(probs[:, ix].max(axis=1))

    for chunk in chunks if chunks is not None else _pcm_chunks(path, CHUNK_SECONDS):
        total += chunk.size
        buf = np.concatenate([buf, chunk])
        windows = []
        while (s + 1) * sr + half <= start + buf.size:
            a = s * sr - half - start
            windows.append(buf[a:a + width])
            s += 1
        run(windows)
        keep = s * sr - half - start
        buf, start = buf[keep:], start + keep

    seconds = total // sr
    if seconds < 1:
        return None
    buf = np.concatenate([buf, np.zeros(width, dtype=np.float32)])
    windows = []
    while s < seconds:
        a = s * sr - half - start
        windows.append(buf[a:a + width])
        s += 1
    run(windows)
    return {g: np.concatenate(v)[:seconds].astype(np.float32) for g, v in out.items() if v}


def _rolling_median(x: np.ndarray, half: int) -> np.ndarray:
    """Median of the `half` seconds either side of each second."""
    from numpy.lib.stride_tricks import sliding_window_view

    padded = np.pad(x, half, mode="edge")
    return np.median(sliding_window_view(padded, 2 * half + 1), axis=1).astype(np.float32)


def sound_signal(heard: dict[str, np.ndarray] | None, groups: dict, genres, genre_sounds: dict,
                 max_events: int = MAX_EVENTS) -> GameSounds | None:
    """What listen() heard, as evidence of in-game moments. `genres`: the
    kind of game per second (or one for the whole video), which sets how much
    each sound means (config/gaming.yaml's genre_sounds)."""
    if not heard:
        return None
    n = max(v.size for v in heard.values())
    if isinstance(genres, str):
        genres = [genres] * n
    kinds = sorted(set(genres))
    at = {k: np.array([g == k for g in genres[:n]] + [False] * (n - len(genres[:n]))) for k in kinds}

    game = np.zeros(n, dtype=np.float32)
    people = np.zeros(n, dtype=np.float32)
    runs = []
    for g, p in heard.items():
        spec = groups.get(g) or {}
        p = np.asarray(p, dtype=np.float32)[:n]
        level = _rolling_median(p, BASELINE)
        # How strong this sound gets in this stream: its loudest moments
        # score 1, its usual level 0.
        ref = max(float(np.percentile(p, 99.5)), MIN_REF)
        usual = float(np.median(p))
        strength = np.clip((p - level) / max(ref - usual, 0.05), 0.0, 1.0) * (p >= MIN_PROB)
        weight = np.zeros(n, dtype=np.float32)
        for k in kinds:
            weight[at[k][: p.size]] = float((genre_sounds.get(k) or {}).get(g, DEFAULT_WEIGHT))
        strength = (strength * weight[: p.size]).astype(np.float32)
        side = people if spec.get("side") == "people" else game
        np.maximum(side[: p.size], strength, out=side[: p.size])

        # A moment: where it is named to the model (short gaps bridged).
        sec = 0
        while sec < strength.size:
            if strength[sec] < EVENT:
                sec += 1
                continue
            end = sec
            while end + 1 < strength.size and (strength[end + 1] >= EVENT
                                               or strength[end + 2:end + 4].max(initial=0) >= EVENT):
                end += 1
            runs.append((sec, end, g, float(strength[sec:end + 1].max())))
            sec = end + 1

    # Sounds close together are one moment (a fight's bursts, a goal and its
    # replay), named by what was heard, strongest first.
    moments: list[dict] = []
    for lo, hi, g, peak in sorted(runs):
        if moments and lo - moments[-1]["end"] <= MERGE_SECONDS:
            m = moments[-1]
            m["end"] = max(m["end"], hi)
            m["peak"] = max(m["peak"], peak)
            m["groups"][g] = max(m["groups"].get(g, 0.0), peak)
        else:
            moments.append({"start": lo, "end": hi, "peak": peak, "groups": {g: peak}})
    events = []
    for m in sorted(moments, key=lambda m: -m["peak"])[:max_events]:
        heard_here = sorted(m["groups"], key=lambda g: -m["groups"][g])
        label = " + ".join(str((groups.get(g) or {}).get("label", g)) for g in heard_here)
        own = all((groups.get(g) or {}).get("side") != "people" for g in heard_here)
        span = m["end"] - m["start"] + 1
        events.append((float(m["start"]), ("GAME SOUND: " if own else "SOUND: ") + label
                       + (f" ({span}s)" if span >= 3 else "")))
    events.sort(key=lambda ev: ev[0])
    return GameSounds(game=game, people=people, events=events)
