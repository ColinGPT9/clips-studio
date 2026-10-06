"""Whose voice it is: where in a clip someone other than the video's main
speaker is talking, so their captions can take another colour (#126).

Nothing else here knows voices apart. The transcript is words and times, and
the active-speaker model (video/asd.py) chooses between faces that are on
screen together; a second voice is as often a caller or a friend on voice
chat, with no face at all. So this goes by the sound of the voice, with two
small models (fetched by scripts/fetch_voice_model.py):

  - a segmentation model (pyannote's segmentation-3.0, MIT) hears ten seconds
    at a time and marks, 60 times a second, which of up to three speakers is
    talking. It is what finds the turn: a question and its answer are a
    second apart with no pause between them. Its speakers are only "first,
    second, third in these ten seconds", though, not anybody in particular.
  - a speaker model (WeSpeaker's VoxCeleb ResNet34-LM, CC BY 4.0) turns a few
    seconds of one voice into a vector that sits close to other speech by the
    same person. It is what says who a turn belongs to.

Once per video the voices in it are found from speech sampled across the
whole video, and ranked by how much each talks: the one that talks most is
the main speaker, in every clip of that video. In a clip, each speaker the
segmentation hears is scored against that main voice, and the words of the
ones who are clearly somebody else come back as turns.

A solo clip coloured by mistake is the worst this can do, so a doubtful clip
comes back with no turns at all: one voice, too little of a second, a clip
the main speaker is barely in.

Both models run on the onnxruntime the app already ships, one inference at a
time on one core: a render is busy encoding on the others.
"""

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SPEAKER = "wespeaker_resnet34_lm.onnx"
SEGMENTER = "pyannote_segmentation_3.onnx"
MODEL = "pyannote-segmentation-3.0+wespeaker-voxceleb-resnet34-LM"
VERSION = 1            # of the saved profile; raise it when how one is made changes

# What the speaker model was trained on; none of these are free parameters.
# The window matters as much as the rest: a Povey window in place of Hamming
# gives a vector only 0.88 like the right one.
SAMPLE_RATE = 16000
WIN = 400              # 25 ms
HOP = 160              # 10 ms
NFFT = 512
BINS = 80
DIM = 256

# The segmentation model's own geometry (its metadata says the same).
CHUNK = 160000         # the 10 s it hears at a time
CHUNK_SHIFT = 80000    # chunks overlap by half, so every moment is heard twice
FRAME = 270            # samples from one of its frames to the next (17 ms)
FRAME_SPAN = 991       # samples one frame looks at
# Its seven answers per frame: nobody, one of three speakers, or two at once.
_POWERSET = ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, 0, 1), (0, 1, 1))

SOLO_SECONDS = 1.0     # speech a speaker needs, with nobody over it, to be known by
EMBED_SECONDS = 6.0    # and more than this of it adds time, not certainty

# The video's voices.
STRETCHES = 24         # sampled across the video's speech
STRETCH_SECONDS = 20.0
LINK = 0.60            # cosine distance two groups of voices merge within
MERGE = 0.55           # two groups this alike are one voice heard twice
MAIN_VOICES = 3        # heard fewer times than this, there is no main voice to speak of
KEEP_VOICES = 6
BESIDE = 1             # chunks a voice has to share with the main one to be surely another

# A clip. Scores are cosine similarity to the main voice: 1 the same, 0 unrelated.
MAIN_LIKE = 0.55       # at or above: the main speaker
OTHER_LIKE = 0.30      # at or below: somebody else, whoever else is in earshot
APART = 0.15           # in between: somebody else when this much nearer another known voice
MARGIN = 0.10          # an unsure one goes to the other voice only this much nearer to it

# Words. The segmentation is sure of a turn to within a word or so; a
# sentence is surer of where it ends.
TURN_SECONDS = 1.0     # a turn shorter than this has to be a whole remark ("Yeah.")
SAID_SECONDS = 1.5     # and a clip where the others say less than this, all told, stays plain
BREATH = 0.3           # a pause this long is as good as a full stop
REACH = 2              # words a turn's edge may move to meet the end of a sentence
_SENTENCE_END = re.compile(r"[.!?…。！？]['\")\]]*$")   # as transcription/cloud.py ends one

_sessions: dict = {}
_load_lock = threading.Lock()
_run_lock = threading.Lock()       # one inference at a time: this never takes a second core
_told_missing = False
_profiles: dict[str, tuple] = {}   # video id -> (source fingerprint, profile)
_profile_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


# ---- the models ------------------------------------------------------------------


def weights_path(name: str = SPEAKER) -> Path:
    """Where a model lives, frozen build or checkout."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / name  # type: ignore[attr-defined]
    return _ROOT / "models" / name


def available() -> bool:
    """True when voices can be told apart. Without the models, captions stay
    one colour."""
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return weights_path(SPEAKER).exists() and weights_path(SEGMENTER).exists()


def load(name: str = SPEAKER):
    """A model's session. Cached: loading costs a tenth of a second, a
    video's clips all use the one."""
    with _load_lock:
        if name not in _sessions:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1
            opts.inter_op_num_threads = 1
            # From bytes, not a path: the install folder can hold any
            # character a Windows user name can.
            _sessions[name] = ort.InferenceSession(
                weights_path(name).read_bytes(), sess_options=opts, providers=["CPUExecutionProvider"]
            )
        return _sessions[name]


_banks = None


def _mel(hz):
    import numpy as np

    return 1127.0 * np.log(1.0 + hz / 700.0)


def _filterbank():
    """Kaldi's triangular mel filters, 20 Hz to Nyquist: (BINS, NFFT/2)."""
    global _banks
    if _banks is None:
        import numpy as np

        low, high = _mel(20.0), _mel(SAMPLE_RATE / 2.0)
        step = (high - low) / (BINS + 1)
        left = low + np.arange(BINS, dtype=np.float64)[:, None] * step
        mel = _mel((SAMPLE_RATE / NFFT) * np.arange(NFFT // 2, dtype=np.float64))[None, :]
        _banks = np.maximum(0.0, np.minimum((mel - left) / step, (left + 2 * step - mel) / step))
    return _banks


def fbank(wave, normalize: bool = True):
    """Kaldi log-mel filterbank features, (frames, 80), as the speaker model
    was trained on: 25 ms Hamming frames every 10 ms, each with its mean
    removed and pre-emphasised, then each mel bin's mean over time taken off.
    `wave` is mono float32 in [-1, 1] at 16 kHz, at least one frame long."""
    import numpy as np

    x = np.asarray(wave, dtype=np.float64) * 32768.0
    frames = 1 + (x.size - WIN) // HOP
    f = x[np.arange(WIN)[None, :] + HOP * np.arange(frames)[:, None]]
    f = f - f.mean(axis=1, keepdims=True)
    f = f - 0.97 * np.concatenate([f[:, :1], f[:, :-1]], axis=1)
    power = np.abs(np.fft.rfft(f * np.hamming(WIN), n=NFFT, axis=1)) ** 2
    feats = np.log(np.maximum(power[:, : NFFT // 2] @ _filterbank().T, np.finfo(np.float32).eps))
    if normalize:
        feats = feats - feats.mean(axis=0, keepdims=True)
    return feats.astype(np.float32)


def embed(waves: list):
    """One unit-length voice vector per wave, (n, 256). A wave too short to
    hold a frame gets zeros, which is like no voice at all."""
    import numpy as np

    session = load(SPEAKER)
    name = session.get_inputs()[0].name
    out = np.zeros((len(waves), DIM), dtype=np.float32)
    for i, wave in enumerate(waves):
        if len(wave) < WIN + HOP:
            continue
        feats = fbank(wave)[None, :, :]
        with _run_lock:
            vector = session.run(None, {name: feats})[0][0]
        norm = float(np.linalg.norm(vector))
        if norm > 0:
            out[i] = vector / norm
    return out


def chunk_starts(samples: int) -> list[int]:
    """The first sample of each chunk over a sound `samples` long: half a
    chunk apart, the last one ending with the sound."""
    starts = list(range(0, max(1, samples - CHUNK + 1), CHUNK_SHIFT))
    if starts[-1] + CHUNK < samples:
        starts.append(samples - CHUNK)
    return starts


def segment(wave) -> list[tuple]:
    """Who is talking when: [(first sample, marks)], one per 10 s chunk, the
    chunks overlapping by half. `marks` is (frames, 3) of 0/1: for each 17 ms
    frame, which of the chunk's three speakers are talking. A speaker's
    number means nothing outside its own chunk."""
    import numpy as np

    session = load(SEGMENTER)
    name = session.get_inputs()[0].name
    powerset = np.asarray(_POWERSET, dtype=np.int8)
    wave = np.asarray(wave, dtype=np.float32)
    chunks = []
    for first in chunk_starts(wave.size):
        piece = wave[first:first + CHUNK]
        if piece.size < CHUNK:
            piece = np.pad(piece, (0, CHUNK - piece.size))
        with _run_lock:
            scores = session.run(None, {name: piece[None, None, :]})[0][0]
        chunks.append((first, powerset[np.argmax(scores, axis=-1)]))
    return chunks


_models = (embed, segment)     # the functions below take stand-ins for these, for tests


def _pcm(source: Path, start: float, seconds: float):
    """`seconds` of the video's sound from `start`, mono float32 at 16 kHz.
    Empty when it has none."""
    import numpy as np

    from core.binaries import ffmpeg

    out = subprocess.run(
        [ffmpeg(), "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{seconds:.3f}",
         "-i", str(source), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True, timeout=300,
    )
    if out.returncode != 0 or not out.stdout:
        return np.zeros(0, dtype=np.float32)
    raw = out.stdout[: len(out.stdout) // 2 * 2]
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


# ---- the speakers of a stretch ---------------------------------------------------


def _samples(frame: int) -> int:
    """The sample a frame's mark starts to apply at (the middle of what the
    frame looked at, less half a step)."""
    return frame * FRAME + (FRAME_SPAN - FRAME) // 2


def local_voices(wave, chunks: list[tuple], embed) -> list[dict]:
    """Each chunk's speakers that said enough to be known by: [{"chunk",
    "speaker", "seconds", "vector"}]. The vector is taken from what a speaker
    said with nobody talking over them."""
    import numpy as np

    found, sounds = [], []
    for c, (first, marks) in enumerate(chunks):
        alone = marks * (marks.sum(axis=1, keepdims=True) == 1)
        for k in range(marks.shape[1]):
            on = np.flatnonzero(alone[:, k])
            if on.size * FRAME < SOLO_SECONDS * SAMPLE_RATE:
                continue
            # runs of consecutive frames, the longest first, up to the cap
            breaks = np.flatnonzero(np.diff(on) > 1)
            runs = sorted(zip(np.r_[on[0], on[breaks + 1]], np.r_[on[breaks], on[-1]] + 1),
                          key=lambda r: r[0] - r[1])
            pieces, kept = [], 0
            for a, b in runs:
                piece = wave[first + _samples(int(a)): first + _samples(int(b))]
                pieces.append(piece)
                kept += piece.size
                if kept >= EMBED_SECONDS * SAMPLE_RATE:
                    break
            found.append({"chunk": c, "speaker": k, "seconds": round(on.size * FRAME / SAMPLE_RATE, 2)})
            sounds.append(np.concatenate(pieces))
    for voice, vector in zip(found, embed(sounds) if sounds else []):
        voice["vector"] = vector
    return found


def _unit(vectors, weights=None):
    """The mean direction of some voice vectors, unit length."""
    import numpy as np

    mean = np.average(np.asarray(vectors, dtype=np.float64), axis=0, weights=weights)
    norm = float(np.linalg.norm(mean))
    return mean / norm if norm > 0 else mean


def cluster(E, distance: float) -> list[int]:
    """A group number for each row of E: average-linkage clustering on
    cosine distance, merging until the two closest groups are further apart
    than `distance`. In numpy because scipy.cluster is not in the installed
    app."""
    import numpy as np

    n = len(E)
    if n == 0:
        return []
    E = np.asarray(E, dtype=np.float64)
    D = 1.0 - E @ E.T
    np.fill_diagonal(D, np.inf)
    size = np.ones(n)
    members: list[list[int]] = [[i] for i in range(n)]
    while True:
        i, j = divmod(int(np.argmin(D)), n)
        if not np.isfinite(D[i, j]) or D[i, j] > distance:
            break
        merged = (size[i] * D[i] + size[j] * D[j]) / (size[i] + size[j])
        D[i, :] = merged
        D[:, i] = merged
        D[i, i] = np.inf
        D[j, :] = np.inf
        D[:, j] = np.inf
        size[i] += size[j]
        members[i] += members[j]
        members[j] = []
    labels = [0] * n
    for group, rows in enumerate(m for m in members if m):
        for row in rows:
            labels[row] = group
    return labels


# ---- the video's voices ----------------------------------------------------------


def voices_of(E, seconds: list[float], where: list | None = None) -> list[dict]:
    """The voices among some voice vectors, the main speaker's first:
    [{"share", "heard", "spread", "beside", "centroid"}].

    `seconds` is how much speech each vector stands for and `where` the
    (stretch, chunk) it was heard in. The main speaker is the voice heard in
    the most stretches, and only then the one that talks most: a streamer is
    there from start to finish, while what they watch and who they call come
    and go, and a video being reacted to often out-talks the person reacting.
    `beside` counts the chunks a voice shares with the main one. Two voices
    heard side by side are two people; a voice never heard beside the main
    one may be the main speaker again, shouting or on another microphone."""
    import numpy as np

    E = np.asarray(E, dtype=np.float64)
    where = list(where) if where is not None else [(i, 0) for i in range(len(E))]
    heard = [i for i in range(len(E)) if float(np.linalg.norm(E[i])) > 0]
    if not heard:
        return []
    groups: dict[int, list[int]] = {}
    for row, label in zip(heard, cluster(E[heard], LINK)):
        groups.setdefault(label, []).append(row)
    found = list(groups.values())

    def centre(group):
        return _unit(E[group], [seconds[i] for i in group])

    # One voice often lands in two groups (talking, then shouting): groups
    # whose centres are this alike are put back together.
    while len(found) > 1:
        centres = np.stack([centre(g) for g in found])
        alike = centres @ centres.T
        np.fill_diagonal(alike, -1.0)
        a, b = divmod(int(np.argmax(alike)), len(found))
        if alike[a, b] < MERGE:
            break
        found[a] = found[a] + found[b]
        del found[b]

    total = sum(seconds[i] for i in heard) or 1.0
    found.sort(key=lambda g: (len({where[i][0] for i in g}), sum(seconds[i] for i in g)), reverse=True)
    with_main = {where[i] for i in found[0]}
    return [
        {"share": round(sum(seconds[i] for i in g) / total, 3), "heard": len(g),
         "spread": len({where[i][0] for i in g}),
         "beside": len({where[i] for i in g} & with_main) if n else 0,
         "centroid": [round(float(v), 5) for v in centre(g)]}
        for n, g in enumerate(found)
    ]


def _fingerprint(source: Path) -> dict:
    stat = source.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def in_library(source: Path, data_dir: Path) -> bool:
    """A video the app holds whole, under its own id. A piece cut for another
    PC to render is neither, and must never be mistaken for one."""
    try:
        here = os.path.normcase(str(Path(source).resolve().parent))
        return here == os.path.normcase(str((Path(data_dir) / "downloads").resolve()))
    except OSError:
        return False


def profile_path(data_dir: Path, video_id: str) -> Path:
    return Path(data_dir) / "voice_profiles" / f"{video_id}.json"


def profile_for(source: Path, segments: list, data_dir: Path, *,
                embed=None, segment=None, pcm=None) -> dict | None:
    """The voices of a video, the main speaker's first: {"voices": [...]},
    empty when it holds too little speech to say. Listened to once per video
    and kept beside its other files, so every clip of it agrees on who the
    main speaker is. None for a source that is not a library video.

    `embed`, `segment` and `pcm` stand in for the models and the decoder in
    tests."""
    source = Path(source)
    if not in_library(source, data_dir):
        return None
    video_id = source.stem
    with _locks_lock:
        lock = _profile_locks.setdefault(video_id, threading.Lock())
    with lock:   # a video's clips render side by side: the first one listens, the rest wait
        mark = _fingerprint(source)
        held = _profiles.get(video_id)
        if held and held[0] == mark:
            return held[1]
        path = profile_path(data_dir, video_id)
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = None
        if (isinstance(saved, dict) and saved.get("version") == VERSION
                and saved.get("model") == MODEL and saved.get("source") == mark):
            _profiles[video_id] = (mark, saved)
            return saved

        print("      Listening for the voices in this video (once per video)")
        profile = {"version": VERSION, "model": MODEL, "source": mark,
                   **_listen(source, segments, embed or _models[0], segment or _models[1], pcm or _pcm)}
        path.parent.mkdir(parents=True, exist_ok=True)
        scratch = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        scratch.write_text(json.dumps(profile), encoding="utf-8")
        os.replace(scratch, path)
        _profiles[video_id] = (mark, profile)
        return profile


def sample(source: Path, segments: list, embed, segment, pcm) -> tuple[list, list, list]:
    """Speech from across the video, as voices: (vectors, seconds of speech
    each stands for, the (stretch, chunk) each was heard in)."""
    from core import cancel
    from video.captions import _words_in_window

    words = _words_in_window(segments, 0.0, float("inf"))
    vectors, seconds, where = [], [], []
    reached = -1.0
    for k in range(STRETCHES):
        if not words:
            break
        cancel.check_active()
        start = words[int((k + 0.5) * len(words) / STRETCHES)]["start"]
        if start < reached:      # a short video: the stretches would overlap
            continue
        reached = start + STRETCH_SECONDS
        wave = pcm(source, start, STRETCH_SECONDS)
        if len(wave) < SOLO_SECONDS * SAMPLE_RATE:
            continue
        for voice in local_voices(wave, segment(wave), embed):
            vectors.append(voice["vector"])
            seconds.append(voice["seconds"])
            where.append((k, voice["chunk"]))
    return vectors, seconds, where


def _listen(source: Path, segments: list, embed, segment, pcm) -> dict:
    """Sample the video's speech and group it by voice."""
    vectors, seconds, where = sample(source, segments, embed, segment, pcm)
    voices = voices_of(vectors, seconds, where) if vectors else []
    if not voices or voices[0]["heard"] < MAIN_VOICES:
        voices = []
    return {"heard": len(vectors), "voices": voices[:KEEP_VOICES]}


def others_of(profile: dict) -> list:
    """The voices of a video, after the main one, that were heard beside it
    often enough to be somebody else for certain."""
    return [v["centroid"] for v in profile["voices"][1:]
            if v.get("heard", 0) >= MAIN_VOICES and v.get("beside", 0) >= BESIDE]


# ---- a clip ----------------------------------------------------------------------


def label_voices(voices: list[dict], main, others=()) -> list[bool]:
    """Which of a clip's local speakers are someone other than the main
    speaker: True for those, one per voice. All False unless somebody else
    is clearly there, and the main speaker too: with only one of them in the
    clip there is nobody to tell apart.

    `main` is the main speaker's voice and `others` the video's other known
    voices (others_of)."""
    import numpy as np

    plain = [False] * len(voices)
    if not voices:
        return plain
    main = np.asarray(main, dtype=np.float64)
    E = np.stack([np.asarray(v["vector"], dtype=np.float64) for v in voices])
    spoke = [v["seconds"] for v in voices]
    heard = [i for i in range(len(voices)) if float(np.linalg.norm(E[i])) > 0]
    score = E @ main
    ours = {i for i in heard if score[i] >= MAIN_LIKE}
    theirs = {i for i in heard if score[i] <= OTHER_LIKE}
    # Between the two lines sits a friend who sounds like the main speaker
    # (two of them scored 0.5 against each other, where strangers score
    # under 0.2), and so does the main speaker on a bad microphone. What
    # tells them apart is whether the voice is nearer to somebody else the
    # video is known to hold.
    if len(others):
        rival = (E @ np.asarray(others, dtype=np.float64).T).max(axis=1)
        theirs.update(i for i in heard if i not in ours and rival[i] - score[i] >= APART)
    if not theirs or not ours:
        return plain

    def centre(group):
        return _unit(E[sorted(group)], [spoke[i] for i in sorted(group)])

    # Still unsure: the other voice only when clearly nearer to it than to
    # the main speaker as each sounds in this clip.
    main_here, other_here = centre(ours), centre(theirs)
    for i in heard:
        if i not in ours and i not in theirs:
            (theirs if float(E[i] @ other_here) - float(E[i] @ main_here) >= MARGIN else ours).add(i)
    return [i in theirs for i in range(len(voices))]


def word_labels(words: list[dict], chunks: list[tuple], voices: list[dict], other: list[bool],
                origin: float, samples: int) -> list[bool]:
    """For each word, whether it is someone other than the main speaker
    saying it. Every moment is heard by two chunks, and a word goes to
    whoever was heard for more of it."""
    import numpy as np

    frames = samples // FRAME + 2
    theirs = np.zeros(frames)
    ours = np.zeros(frames)
    whose = {(v["chunk"], v["speaker"]): o for v, o in zip(voices, other)}
    for c, (first, marks) in enumerate(chunks):
        at = int(round((first + (FRAME_SPAN - FRAME) // 2) / FRAME))
        n = min(marks.shape[0], frames - at)
        if n <= 0:
            continue
        for k in range(marks.shape[1]):
            # A speaker heard too little to be known counts as the main one.
            (theirs if whose.get((c, k), False) else ours)[at:at + n] += marks[:n, k]
    labels = []
    for w in words:
        a = max(0, int((w["start"] - origin) * SAMPLE_RATE / FRAME))
        b = max(a + 1, int(np.ceil((w["end"] - origin) * SAMPLE_RATE / FRAME)))
        labels.append(float(theirs[a:b].sum()) > float(ours[a:b].sum()))
    return labels


def tidy(words: list[dict], labels: list[bool]) -> list[bool]:
    """Word labels with the turns made whole. A word or two cut loose in the
    middle of someone's sentence goes back to that sentence, and a turn's
    edge moves the word or two it takes to land where a sentence ends."""
    labels = list(labels)
    count = len(labels)
    if count < 2:
        return labels
    # How good a place each gap is to change speaker: gap i is before word i.
    good = [1.0]
    for i in range(1, count):
        pause = max(0.0, words[i]["start"] - words[i - 1]["end"])
        good.append((1.0 if _SENTENCE_END.search(str(words[i - 1].get("word", ""))) else 0.0) + min(pause, 0.5))
    good.append(1.0)

    def runs() -> list[tuple[int, int]]:
        out, first = [], 0
        for i in range(1, count + 1):
            if i == count or labels[i] != labels[first]:
                out.append((first, i))
                first = i
        return out

    # A short turn stays only as a remark of its own ("Yeah." "What?").
    while True:
        loose = next(
            ((a, b) for a, b in runs()
             if b - a < count and words[b - 1]["end"] - words[a]["start"] < TURN_SECONDS
             and not (good[a] >= BREATH and good[b] >= BREATH)),
            None,
        )
        if loose is None:
            break
        for i in range(*loose):
            labels[i] = not labels[i]

    edges = [a for a, _ in runs()][1:]
    for n, edge in enumerate(edges):
        low = (edges[n - 1] if n else 0) + 1
        high = (edges[n + 1] if n + 1 < len(edges) else count) - 1
        reach = range(max(low, edge - REACH), min(high, edge + REACH) + 1)
        best = max(reach, key=lambda q: (good[q] - 0.2 * abs(q - edge), -abs(q - edge)))
        if best != edge and good[best] >= BREATH:
            for i in range(min(best, edge), max(best, edge)):
                labels[i] = labels[edge] if best < edge else labels[edge - 1]
            edges[n] = best
    return labels


def spans(words: list[dict], other: list[bool], origin: float, duration: float) -> list[list]:
    """Words said by the other voice, joined into turns: [[start, end, 1]] in
    seconds from `origin`, within 0..duration."""
    turns: list[list] = []
    running = False      # a turn of theirs is open: their next word carries it on
    for word, theirs in zip(words, other):
        start = max(0.0, word["start"] - origin)
        end = min(duration, word["end"] - origin)
        if not theirs:
            running = False
        elif end > start:
            if running:
                # max: of two words said at once, the later to start can be the first to end
                turns[-1][1] = max(turns[-1][1], round(end, 2))
            else:
                turns.append([round(start, 2), round(end, 2), 1])
            running = True
        # A word of theirs with no length neither opens a turn nor carries one
        # on: taken as carrying on, it joined their next turn to their last
        # one, across everything the main speaker said in between.
    return turns


def turns_for(source: Path, candidate, segments: list, config: dict, *,
              embed=None, segment=None, pcm=None) -> list[list]:
    """Where in a clip someone other than the video's main speaker talks:
    [[start, end, 1], ...] in clip seconds, before any editor cuts. Empty for
    a clip with one voice, and whenever it is not clear there are two.

    `embed`, `segment` and `pcm` stand in for the models and the decoder in
    tests."""
    global _told_missing
    from video.captions import _words_in_window

    if (embed is None or segment is None) and not available():
        if not _told_missing:
            _told_missing = True
            print("      (the voice models are missing: captions stay one colour. "
                  "Run scripts/fetch_voice_model.py)")
        return []
    embed, segment, pcm = embed or _models[0], segment or _models[1], pcm or _pcm
    words = _words_in_window(segments, candidate.start, candidate.end)
    if not words:
        return []
    profile = profile_for(source, segments, Path(config["paths"]["data_dir"]),
                          embed=embed, segment=segment, pcm=pcm)
    if not profile or not profile.get("voices"):
        return []

    duration = candidate.end - candidate.start
    wave = pcm(source, candidate.start, duration)
    if len(wave) < SOLO_SECONDS * SAMPLE_RATE:
        return []
    chunks = segment(wave)
    voices = local_voices(wave, chunks, embed)
    other = label_voices(voices, profile["voices"][0]["centroid"], others_of(profile))
    if not any(other):
        return []
    labels = tidy(words, word_labels(words, chunks, voices, other, candidate.start, len(wave)))
    turns = spans(words, labels, candidate.start, duration)
    # A word or two in another voice is as likely the main speaker muttering.
    return turns if sum(end - start for start, end, _ in turns) >= SAID_SECONDS else []
