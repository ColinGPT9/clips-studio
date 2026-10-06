"""Whose voice it is: the turns behind the second speaker's caption colour (#126).

analysis/voice_turns.py hears who is talking when with two models. Neither
runs here: a made-up video says who talks when, and stands in for the
decoder (every sample of its "sound" is its own time), the segmentation
model (marks read off the same schedule) and the speaker model (a fixed
vector per speaker). What is tested is everything in between:

- the main speaker is the voice heard throughout the video, in every clip of
  it, even in a clip where someone else talks more;
- the other speaker's words come back as turns, to the word;
- a clip with one voice, or without the main speaker, or with too little of
  anyone else, comes back with none;
- the video is listened to once, kept beside its files, and never for a
  piece that is not a library video.
"""

import json
import threading
from pathlib import Path

import pytest

from analysis import voice_turns as vt
from core.models import ClipCandidate, Segment


@pytest.fixture
def np():
    return pytest.importorskip("numpy")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """Profiles are held in memory per video id; no test sees another's."""
    monkeypatch.setattr(vt, "_profiles", {})
    monkeypatch.setattr(vt, "_profile_locks", {})


# ---- a made-up video ----------------------------------------------------------------


class Video:
    """Who talks when: turns of (start, end, speaker), nobody in between."""

    def __init__(self, np, turns, alike: float = 0.0):
        self.np = np
        self.turns = sorted(turns)
        self.starts = np.array([t[0] for t in self.turns])
        self.ends = np.array([t[1] for t in self.turns])
        self.names = sorted({t[2] for t in self.turns})
        self.who_at = np.array([self.names.index(t[2]) for t in self.turns])
        self.reads: list[tuple[float, float]] = []
        # One direction per speaker. `alike` is how like "a" everyone else sounds.
        self.voice = np.eye(len(self.names), vt.DIM)
        for k in range(1, len(self.names)):
            self.voice[k] = alike * self.voice[0] + (1 - alike ** 2) ** 0.5 * self.voice[k]

    def who(self, times):
        """Speaker number at each time, -1 for nobody."""
        np = self.np
        i = np.searchsorted(self.starts, times, side="right") - 1
        talking = (i >= 0) & (times < self.ends[np.maximum(i, 0)])
        return np.where(talking, self.who_at[np.maximum(i, 0)], -1)

    def pcm(self, _source, start, seconds):
        self.reads.append((start, seconds))
        return start + self.np.arange(int(seconds * vt.SAMPLE_RATE)) / vt.SAMPLE_RATE

    def segment(self, wave):
        np = self.np
        frames = (vt.CHUNK - vt.FRAME_SPAN) // vt.FRAME + 1
        chunks = []
        for first in vt.chunk_starts(len(wave)):
            at = first + np.arange(frames) * vt.FRAME + vt.FRAME_SPAN // 2
            heard = np.full(frames, -1)
            inside = at < len(wave)
            heard[inside] = self.who(wave[at[inside]])
            marks = np.zeros((frames, 3), dtype=np.int8)
            # A chunk numbers its speakers as it meets them, like the model.
            for local, speaker in enumerate(dict.fromkeys(int(s) for s in heard if s >= 0)):
                if local < 3:
                    marks[heard == speaker, local] = 1
            chunks.append((first, marks))
        return chunks

    def embed(self, waves):
        np = self.np
        out = np.zeros((len(waves), vt.DIM), dtype=np.float32)
        for i, wave in enumerate(waves):
            heard = self.who(wave[::200])
            heard = heard[heard >= 0]
            if heard.size:
                out[i] = self.voice[np.bincount(heard).argmax()]
        return out

    def segments(self) -> list[Segment]:
        """A word every quarter second of each turn, the last ending its sentence."""
        out = []
        for start, end, speaker in self.turns:
            count = int(round((end - start) / 0.25))
            words = [{"start": start + i * 0.25, "end": start + (i + 1) * 0.25,
                      "word": f"{speaker}{'.' if i == count - 1 else ''}"} for i in range(count)]
            out.append(Segment(start=start, end=end, text=" ".join(w["word"] for w in words), words=words))
        return out

    def stand_ins(self) -> dict:
        return {"embed": self.embed, "segment": self.segment, "pcm": self.pcm}


def _talk(a_seconds=6.0, b_seconds=3.0, b_from=200, b_until=320, extra=()):
    """Ten minutes: "a" talks at the top of every ten seconds, and from
    `b_from` to `b_until` "b" answers. `extra` turns are added as given."""
    turns = []
    for t in range(0, 600, 10):
        there = b_from <= t < b_until
        turns.append((t, t + (a_seconds if there else 6.0), "a"))
        if there:
            turns.append((t + a_seconds + 0.5, t + a_seconds + 0.5 + b_seconds, "b"))
    return turns + list(extra)


@pytest.fixture
def library(tmp_path):
    """A data folder with one video in it, and the config that points there."""
    (tmp_path / "downloads").mkdir()
    source = tmp_path / "downloads" / "v1.mp4"
    source.write_bytes(b"video")
    return source, {"paths": {"data_dir": str(tmp_path)}}


def _turns(video, library, start, end):
    source, config = library
    return vt.turns_for(source, ClipCandidate(start=start, end=end, score=0), video.segments(), config,
                        **video.stand_ins())


# ---- a clip's turns -----------------------------------------------------------------


def test_the_other_speakers_words_come_back_as_turns(np, library):
    video = Video(np, _talk())

    turns = _turns(video, library, 240.0, 270.0)

    # "b" answers at 6.5-9.5 of every ten seconds.
    assert turns == [[6.5, 9.5, 1], [16.5, 19.5, 1], [26.5, 29.5, 1]]


def test_the_main_speaker_stays_main_in_a_clip_where_the_other_talks_more(np, library):
    """Who is main is decided over the whole video, where "a" is always
    there, not in the clip, where "b" has twice the say."""
    video = Video(np, _talk(a_seconds=3.0, b_seconds=6.0))

    turns = _turns(video, library, 240.0, 270.0)

    assert turns == [[3.5, 9.5, 1], [13.5, 19.5, 1], [23.5, 29.5, 1]]


def test_a_clip_with_one_voice_has_no_turns(np, library):
    video = Video(np, _talk())

    assert _turns(video, library, 100.0, 130.0) == []


def test_a_clip_without_the_main_speaker_has_no_turns(np, library):
    """Somebody else's monologue is one voice too: nobody to tell them from."""
    video = Video(np, [t for t in _talk() if not 400 <= t[0] < 430] + [(400.0, 428.0, "b")])

    assert _turns(video, library, 400.0, 430.0) == []


def test_a_few_words_from_someone_else_are_not_a_second_speaker(np, library):
    video = Video(np, _talk(extra=[(506.5, 507.5, "b")]))

    assert _turns(video, library, 500.0, 530.0) == []


def test_a_friend_who_sounds_alike_is_told_apart_by_being_known_to_the_video(np, library):
    """Against the main voice alone a similar one is neither clearly the same
    nor clearly different. Heard beside the main speaker elsewhere in the
    video, it is known as somebody else."""
    video = Video(np, _talk(), alike=0.35)

    assert _turns(video, library, 240.0, 270.0) == [[6.5, 9.5, 1], [16.5, 19.5, 1], [26.5, 29.5, 1]]

    profile = json.loads((Path(library[1]["paths"]["data_dir"]) / "voice_profiles" / "v1.json").read_text())
    main = profile["voices"][0]["centroid"]
    clip = ClipCandidate(start=240.0, end=270.0, score=0)
    wave = video.pcm(None, clip.start, 30.0)
    voices = vt.local_voices(wave, video.segment(wave), video.embed)
    assert not any(vt.label_voices(voices, main))                       # on its own: unsure, so plain
    assert any(vt.label_voices(voices, main, vt.others_of(profile)))


def test_no_video_no_models_no_turns(np, library, monkeypatch, capsys):
    monkeypatch.setattr(vt, "available", lambda: False)
    monkeypatch.setattr(vt, "_told_missing", False)
    source, config = library
    clip = ClipCandidate(start=240.0, end=270.0, score=0)
    segments = Video(np, _talk()).segments()

    assert vt.turns_for(source, clip, segments, config) == []
    assert vt.turns_for(source, clip, segments, config) == []
    assert capsys.readouterr().out.count("voice models are missing") == 1     # said once


def test_cancelling_the_video_stops_the_listening(np, library):
    from core import cancel

    video = Video(np, _talk())

    def cancelled(*_a):
        raise cancel.CancelledError("v1")

    with pytest.raises(cancel.CancelledError):
        vt.turns_for(library[0], ClipCandidate(start=240.0, end=270.0, score=0), video.segments(), library[1],
                     embed=video.embed, segment=video.segment, pcm=cancelled)


# ---- the video's voices -------------------------------------------------------------


def test_the_video_is_listened_to_once_and_kept_beside_its_files(np, library, monkeypatch):
    video = Video(np, _talk())
    source, config = library
    saved = Path(config["paths"]["data_dir"]) / "voice_profiles" / "v1.json"

    _turns(video, library, 240.0, 270.0)
    stretches = [r for r in video.reads if r[1] == vt.STRETCH_SECONDS]
    assert 10 <= len(stretches) <= vt.STRETCHES and len(video.reads) == len(stretches) + 1
    profile = json.loads(saved.read_text(encoding="utf-8"))
    assert profile["version"] == vt.VERSION and profile["model"] == vt.MODEL
    assert [v["heard"] >= vt.MAIN_VOICES for v in profile["voices"][:2]] == [True, True]
    assert profile["voices"][0]["spread"] > profile["voices"][1]["spread"]      # "a" is everywhere
    assert profile["voices"][1]["beside"] >= vt.BESIDE                          # "b" was heard beside "a"

    # Another clip: only the clip is read. From memory, then from the file.
    video.reads.clear()
    _turns(video, library, 100.0, 130.0)
    monkeypatch.setattr(vt, "_profiles", {})
    _turns(video, library, 250.0, 280.0)
    assert video.reads == [(100.0, 30.0), (250.0, 30.0)]

    # A different file under the same id is listened to afresh.
    source.write_bytes(b"another video altogether")
    video.reads.clear()
    _turns(video, library, 240.0, 270.0)
    assert len(video.reads) == len(stretches) + 1


def test_a_piece_of_a_video_is_never_listened_to_as_one(np, tmp_path):
    """A render worker is handed a cut called piece.mp4: no profile is made
    from it, and none is saved for the next job's piece to find."""
    video = Video(np, _talk())
    (tmp_path / "remote_render" / "jobs" / "j1").mkdir(parents=True)
    piece = tmp_path / "remote_render" / "jobs" / "j1" / "piece.mp4"
    piece.write_bytes(b"piece")
    config = {"paths": {"data_dir": str(tmp_path)}}

    assert vt.profile_for(piece, video.segments(), tmp_path, **video.stand_ins()) is None
    assert vt.turns_for(piece, ClipCandidate(start=240.0, end=270.0, score=0), video.segments(), config,
                        **video.stand_ins()) == []
    assert video.reads == [] and not (tmp_path / "voice_profiles").exists()


def test_clips_rendering_side_by_side_listen_to_the_video_once(np, library):
    video = Video(np, _talk())
    results: dict = {}

    def render(start):
        results[start] = _turns(video, library, float(start), float(start) + 30.0)

    threads = [threading.Thread(target=render, args=(s,)) for s in (200, 220, 240, 260, 280, 100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len([r for r in video.reads if r[1] == vt.STRETCH_SECONDS]) <= vt.STRETCHES
    assert results[240] == [[6.5, 9.5, 1], [16.5, 19.5, 1], [26.5, 29.5, 1]] and results[100] == []


def test_the_main_speaker_is_whoever_is_there_throughout_not_whoever_talks_most(np):
    """A video being reacted to out-talks the person reacting, in the few
    stretches it plays."""
    a, b = np.eye(2, vt.DIM)
    vectors = [b, b, b] + [a] * 6
    seconds = [9.0, 9.0, 9.0] + [2.0] * 6
    where = [(0, 0), (0, 1), (1, 0)] + [(k, 0) for k in range(6)]

    voices = vt.voices_of(vectors, seconds, where)

    assert np.allclose(voices[0]["centroid"], a) and voices[0]["spread"] == 6
    assert voices[1]["share"] > voices[0]["share"]
    assert voices[1]["beside"] == 2            # chunks (0, 0) and (1, 0) held both


def test_a_voice_heard_twice_is_one_voice(np):
    rng = np.random.default_rng(0)
    a, b = np.eye(2, vt.DIM)

    def near(v):
        v = v + 0.05 * rng.standard_normal(vt.DIM) / vt.DIM ** 0.5 * 4
        return v / np.linalg.norm(v)

    E = np.stack([near(a) for _ in range(5)] + [near(b) for _ in range(4)])

    labels = vt.cluster(E, vt.LINK)

    assert len(set(labels[:5])) == 1 and len(set(labels[5:])) == 1 and labels[0] != labels[5]
    assert vt.cluster(np.zeros((0, vt.DIM)), vt.LINK) == []


# ---- the speaker model's features ---------------------------------------------------


def test_the_filterbank_is_the_one_the_speaker_model_was_trained_on(np):
    """Values from torchaudio's Kaldi fbank (Hamming window, no dither) for
    the same second of two sine waves. A changed window or mel scale still
    gives vectors, only worse ones, so this is the one place it shows."""
    n = np.arange(16000)
    wave = (0.3 * np.sin(2 * np.pi * 220 * n / 16000) + 0.1 * np.sin(2 * np.pi * 1800 * n / 16000))

    feats = vt.fbank(wave.astype(np.float32), normalize=False)

    bins = [0, 5, 20, 40, 60, 79]
    assert feats.shape == (98, 80)
    assert np.allclose(feats[0, bins], [14.9384, 20.2913, 12.1748, 24.5778, 12.2971, 11.9248], atol=2e-3)
    assert np.allclose(feats[97, bins], [15.1242, 20.2986, 12.2330, 24.5777, 11.9969, 11.4663], atol=2e-3)
    assert np.allclose(vt.fbank(wave.astype(np.float32)).mean(axis=0), 0.0, atol=1e-4)


# ---- words --------------------------------------------------------------------------


def _words(text: str, step: float = 0.3) -> list[dict]:
    return [{"start": i * step, "end": (i + 1) * step, "word": w} for i, w in enumerate(text.split())]


def _theirs(words, labels) -> str:
    return " ".join(w["word"] for w, o in zip(words, labels) if o)


def test_a_word_cut_loose_mid_sentence_goes_back_to_its_sentence():
    words = _words("No, I get it. You don't really want it.")
    labels = [False, True, False, False, False, False, False, False, False]

    assert not any(vt.tidy(words, labels))


def test_a_short_remark_of_its_own_stays():
    words = _words("You're going to sacrifice your form. Yeah. Maybe your shoulders.")
    labels = [False] * 6 + [True] + [False] * 3

    assert _theirs(words, vt.tidy(words, labels)) == "Yeah."


def test_a_turns_edge_moves_to_the_end_of_the_sentence():
    words = _words("I can tell. Like, you're putting on the size. What do you mean you can tell?")
    late = [True] * 10 + [False] * 6           # one word late: "What" went with the answer
    early = [True] * 8 + [False] * 8           # two words early

    for labels in (late, early):
        assert _theirs(words, vt.tidy(words, labels)) == "I can tell. Like, you're putting on the size."


def test_with_no_sentences_to_go_by_a_turn_is_left_where_it_was_heard():
    words = _words("so i was a veterinarian you're a veterinarian yeah i did not know what else to say")
    labels = [False] * 5 + [True] * 4 + [False] * 8

    assert vt.tidy(words, labels) == labels
    # Too short to be a turn, with nothing to say it is a remark of its own.
    assert not any(vt.tidy(words, [False] * 5 + [True] * 2 + [False] * 10))


def test_words_join_into_turns_inside_the_clip():
    words = [{"start": 9.8, "end": 10.2, "word": "a"}, {"start": 10.2, "end": 10.6, "word": "b"},
             {"start": 11.0, "end": 11.4, "word": "c"}, {"start": 12.0, "end": 12.5, "word": "d"},
             {"start": 19.8, "end": 20.3, "word": "e"}]

    turns = vt.spans(words, [True, True, False, True, True], origin=10.0, duration=10.0)

    assert turns == [[0.0, 0.6, 1], [2.0, 10.0, 1]]      # cut at the clip's edges; d and e are one turn


def test_a_turn_that_starts_on_a_word_of_no_length_is_not_joined_to_the_last_one():
    """Transcripts hold words that start and end on one instant. One of
    theirs opening their turn used to carry their LAST turn on, across
    everything the main speaker had said in between."""
    words = [{"start": 0.0, "end": 0.5, "word": "them1"}, {"start": 0.5, "end": 1.0, "word": "me1"},
             {"start": 1.0, "end": 1.5, "word": "me2"}, {"start": 1.5, "end": 1.5, "word": "them2"},
             {"start": 1.5, "end": 2.0, "word": "them3"}]

    turns = vt.spans(words, [True, False, False, True, True], origin=0.0, duration=2.0)

    assert turns == [[0.0, 0.5, 1], [1.5, 2.0, 1]]
    # One in the middle of their turn changes nothing either.
    assert vt.spans(words, [True, True, True, True, True], origin=0.0, duration=2.0) == [[0.0, 2.0, 1]]
    assert vt.spans(words[3:4], [True], origin=0.0, duration=2.0) == []


def test_of_two_words_said_at_once_the_turn_ends_with_the_later_to_finish():
    words = [{"start": 0.5, "end": 1.4, "word": "long"}, {"start": 0.56, "end": 0.64, "word": "short"}]

    assert vt.spans(words, [True, True], origin=0.0, duration=5.0) == [[0.5, 1.4, 1]]
