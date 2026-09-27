"""Hearing the game (analysis/game_audio.py, analysis/panns.py).

Gunfire, a goal, a crash mark in-game moments on a gaming stream even when
the streamer says nothing, judged against the stream's own sound and
weighted for the kind of game being played.
"""

from pathlib import Path

import pytest

from analysis import gaming

ROOT = Path(__file__).resolve().parent.parent
SR = 32000


def test_the_sound_groups_name_real_audioset_classes():
    names = (ROOT / "config" / "audioset_labels.txt").read_text(encoding="utf-8").splitlines()
    assert len(names) == 527 and names[0] == "Speech"
    k = gaming.knowledge()
    for group, spec in k["sound_groups"].items():
        assert spec["side"] in ("game", "people"), group
        assert set(spec["classes"]) <= set(names), group
    assert set(k["genre_sounds"]) == set(k["genres"])


def test_the_genre_track_follows_the_game_played_in_each_part():
    games = [{"name": "Just Chatting", "start": 0, "end": 600},
             {"name": "Rocket League", "start": 600, "end": 1200},
             {"name": "Apex Legends", "start": 1200, "end": 1800}]
    p = gaming.profile_for({"clips": {"gaming_scoring": True}}, games, "")
    track = p.genre_track(1900)
    assert len(track) == 1900
    for sec in (0, 599, 600, 1199, 1200, 1799, 1850):
        assert track[sec] == p.game_at(sec)[1], sec
    assert (track[0], track[600], track[1200]) == ("reaction", "sports", "battle_royale")


# ---- listening --------------------------------------------------------------------------


def _loudness_tagger(np, label):
    """A stand-in for the network: `label` scores how loud the window is."""
    from analysis import panns

    col = panns.labels().index(label)

    def tag(windows):
        out = np.zeros((len(windows), panns.CLASSES), dtype=np.float32)
        out[:, col] = np.abs(windows).max(axis=1)
        return out
    return tag


def test_listening_hears_each_second_in_the_two_seconds_around_it():
    np = pytest.importorskip("numpy")
    from analysis.game_audio import listen

    audio = np.zeros(10 * SR, dtype=np.float32)
    audio[int(5.0 * SR):int(5.2 * SR)] = 0.8          # a bang at 5.0-5.2 s
    # Decoded in uneven pieces, as a pipe delivers it.
    cuts = [0, 3 * SR + 7, 5 * SR - 3, 9 * SR + 11, audio.size]
    chunks = [audio[a:b] for a, b in zip(cuts, cuts[1:])]
    heard = listen(Path("v.mp4"), {"explosion": {"classes": ["Explosion"]}},
                   tag=_loudness_tagger(np, "Explosion"), chunks=iter(chunks))
    assert heard["explosion"].size == 10
    # Second s is heard over [s - 0.5, s + 1.5): the bang is in seconds 4 and 5.
    assert np.flatnonzero(heard["explosion"]).tolist() == [4, 5]


def test_no_audio_or_no_known_sound_hears_nothing():
    np = pytest.importorskip("numpy")
    from analysis.game_audio import listen

    tag = _loudness_tagger(np, "Explosion")
    assert listen(Path("v.mp4"), {"explosion": {"classes": ["Explosion"]}}, tag=tag, chunks=iter([])) is None
    assert listen(Path("v.mp4"), {"x": {"classes": ["Not a class"]}}, tag=tag,
                  chunks=iter([np.zeros(SR * 3, dtype=np.float32)])) is None


# ---- what it means ----------------------------------------------------------------------


def _heard(np, n=1200, seed=3, **spikes):
    """A stream's heard sounds: every group at its usual low level, with
    spikes {group: [(first second, last second, probability)]}."""
    rng = np.random.default_rng(seed)
    k = gaming.knowledge()
    heard = {g: (0.005 + 0.01 * rng.random(n)).astype(np.float32) for g in k["sound_groups"]}
    for g, runs in spikes.items():
        for lo, hi, p in runs:
            heard[g][lo:hi + 1] = p
    return heard


def _signal(heard, genres):
    from analysis.game_audio import sound_signal

    k = gaming.knowledge()
    return sound_signal(heard, k["sound_groups"], genres, k["genre_sounds"])


def test_a_gunfight_is_heard_as_a_game_moment():
    np = pytest.importorskip("numpy")
    s = _signal(_heard(np, gunfire=[(300, 305, 0.6)]), "shooter")
    assert s.game[302] >= 0.9 and s.game[100] == 0 and s.people.max() == 0
    assert [(t, txt) for t, txt in s.events] == [(300.0, "GAME SOUND: gunfire (6s)")]


def test_a_sound_means_what_it_means_in_this_kind_of_game():
    np = pytest.importorskip("numpy")
    heard = _heard(np, gunfire=[(300, 305, 0.6)])
    assert _signal(heard, "sports").events == []           # gunfire isn't a sports moment
    # A stream that changes game: only the shooter part hears the gunfire.
    both = _heard(np, n=1200, gunfire=[(300, 305, 0.6), (900, 905, 0.6)])
    s = _signal(both, ["sports"] * 600 + ["shooter"] * 600)
    assert [t for t, _ in s.events] == [900.0]


def test_a_stream_with_nothing_happening_does_not_invent_moments():
    np = pytest.importorskip("numpy")
    s = _signal(_heard(np, explosion=[(300, 301, 0.08)]), "shooter")   # the loudest bang, but faint
    assert s.events == [] and s.game.max() == 0


def test_a_goal_and_its_replay_are_one_moment():
    np = pytest.importorskip("numpy")
    s = _signal(_heard(np, explosion=[(100, 100, 0.25), (109, 109, 0.25)]), "sports")
    assert s.events == [(100.0, "GAME SOUND: an explosion (10s)")]


def test_laughter_is_the_people_playing_not_the_game():
    np = pytest.importorskip("numpy")
    s = _signal(_heard(np, laughter=[(200, 202, 0.5)]), "party")
    assert s.people[201] >= 0.9 and s.game.max() == 0
    assert s.events == [(200.0, "SOUND: laughter (3s)")]


def test_the_pipeline_weighs_what_was_heard_for_the_game_played(db):
    np = pytest.importorskip("numpy")
    from core.models import DownloadedVideo
    from core.pipeline import _gaming_scoring_inputs

    video = DownloadedVideo(video_id="v1", title="ranked", path=Path("v.mp4"), duration=1200)
    heard = _heard(np, gunfire=[(300, 305, 0.6)])
    games = [{"name": "Apex Legends", "start": 0, "end": 1200}]
    profile, chat, sounds = _gaming_scoring_inputs({"clips": {"gaming_scoring": True}}, video, db, games,
                                                   {}, heard)
    assert profile.genre == "battle_royale" and chat is None
    assert [t for t, _ in sounds.events] == [300.0]
    assert _gaming_scoring_inputs({"clips": {"gaming_scoring": True}}, video, db, games, {})[2] is None


# ---- the network --------------------------------------------------------------------------


def test_the_network_loads_its_weights_and_hears_a_window():
    np = pytest.importorskip("numpy")
    torch = pytest.importorskip("torch")
    from analysis import panns

    if not panns.weights_path().exists():
        pytest.skip("game-sound weights not fetched (scripts/fetch_panns.py)")
    # torch.stft matches the STFT the checkpoint was trained with (a
    # convolution with the window baked in).
    state = torch.load(panns.weights_path(), map_location="cpu", weights_only=True)["model"]
    x = torch.randn(1, 2 * SR, generator=torch.Generator().manual_seed(0))
    padded = torch.nn.functional.pad(x[:, None, :], (512, 512), mode="reflect")
    conv = sum(torch.nn.functional.conv1d(padded, state[f"spectrogram_extractor.stft.conv_{p}.weight"],
                                          stride=panns.HOP) ** 2 for p in ("real", "imag"))[0]
    spec = torch.stft(x, panns.N_FFT, panns.HOP, panns.N_FFT, torch.hann_window(panns.N_FFT),
                      center=True, pad_mode="reflect", return_complex=True)[0]
    assert torch.allclose(spec.real ** 2 + spec.imag ** 2, conv, rtol=1e-3, atol=1e-3)

    probs = panns.tag(np.zeros((2, 2 * SR), dtype=np.float32))
    assert probs.shape == (2, 527) and 0 <= probs.min() and probs.max() <= 1
