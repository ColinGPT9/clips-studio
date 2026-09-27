"""Reading the screen of a gaming stream (analysis/game_text.py).

A banner (ELIMINATED, VICTORY ROYALE, "X A MARQUÉ") names an in-game moment;
a menu, queue or settings screen marks a moment chat reacted to as not one.
The OCR is stood in for: these tests check what is made of what it reads.
"""

from pathlib import Path

import pytest

from analysis import gaming
from analysis.game_text import Lexicon, ScreenText, read_screen


def test_every_kind_of_game_has_its_screen_words():
    st = gaming.knowledge()["screen_text"]
    assert set(st["events"]) == set(gaming.knowledge()["genres"])
    assert len(st["menu"]) >= 40


def test_words_are_found_the_way_a_banner_is_read():
    lx = Lexicon(["A MARQUÉ", "VICTORY ROYALE", "VICTORY", "ACE", "PLAY", "設定"])
    # A stylised banner loses its spaces and accents, and 1/Q come back as I/O.
    assert lx.find("KIMOU1TAMARQUE") == ["A MARQUÉ"]
    assert lx.find("OAYS6ZAMAROUE") == ["A MARQUÉ"]
    assert set(lx.find("Victory Royale!")) == {"VICTORY ROYALE", "VICTORY"}
    # Short words only on their own.
    assert lx.find("ACE") == ["ACE"] and lx.find("PLACE") == []
    assert lx.find("PLAYER4") == [] and lx.find("[1:06] PLAY") == ["PLAY"]
    assert lx.find("設定") == ["設定"]


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_a_banner_counts_and_the_chat_overlay_does_not():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from analysis.game_text import WIDTH, read_frame

    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    height = round(WIDTH * (0.70 * 1080) / (0.76 * 1920))        # the centre, read at WIDTH

    def ocr(img):
        assert img.shape[1] == WIDTH
        return [
            (_box(400, 200, 900, 300), "ELIMINATED", 0.95),          # big, in the middle
            (_box(20, 40, 220, 58), "ACE", 0.95),                    # chat overlay: small
            (_box(900, 600, 1100, 620), "SETTINGS", 0.9),
            (_box(900, 640, 1100, 660), "SENSITIVITY", 0.9),
            (_box(500, 500, 700, 600), "VICTORY", 0.3),              # not sure what it read
        ]
    st = gaming.knowledge()["screen_text"]
    events = Lexicon([*st["events"]["generic"], *st["events"]["shooter"]])
    found, menus = read_frame(frame, events, Lexicon(st["menu"]), ocr=ocr)
    assert height > 300
    assert found == ["ELIMINATED"] and menus == {"SETTINGS", "SENSITIVITY"}


def _reader(script):
    """A stand-in read_frame: `script` maps a second to (event words, menu words)."""
    def read(img, events, _menu):
        found, menus = script.get(int(img), ([], set()))
        return [w for w in found if events.find(w)], set(menus)
    return read


def test_the_screen_names_moments_and_marks_menus():
    lexicon = gaming.knowledge()["screen_text"]
    script = {
        105: (["GOAL"], set()),                            # a goal in the football part
        109: (["GOAL"], set()),                            # the same banner, still up
        505: (["ELIMINATED"], set()),                      # a shooter word in a sports game
        # Read every 2 s near the start of a window: four of its seven frames
        # are a settings page.
        901: ([], {"SETTINGS", "SENSITIVITY"}),
        903: ([], {"SETTINGS", "BACK"}),
        905: ([], {"SETTINGS", "BACK"}),
        907: ([], {"SETTINGS", "SENSITIVITY"}),
    }
    genre = {100: "sports", 500: "sports", 900: "shooter"}
    out = read_screen(Path("v.mp4"), [(100, 115), (500, 515), (900, 915)],
                      lambda s, e: genre[int(s)], lexicon, grab=lambda t: t, read=_reader(script))
    assert out.events == [(105.0, "ON SCREEN: GOAL")]
    assert out.menus == [(900.0, 915.0, "SETTINGS, BACK, SENSITIVITY")]
    assert out.frames == 21


def test_one_menu_word_on_screen_is_not_a_menu():
    # "PLAY" in a HUD hint on every frame: one word, however often, isn't a menu.
    script = {t: ([], {"PLAY"}) for t in range(101, 115)}
    out = read_screen(Path("v.mp4"), [(100, 115)], lambda s, e: "generic",
                      gaming.knowledge()["screen_text"], grab=lambda t: t, read=_reader(script))
    assert out.menus == []


def test_reading_stops_at_its_frame_budget():
    out = read_screen(Path("v.mp4"), [(0, 100), (200, 300)], lambda s, e: "generic",
                      gaming.knowledge()["screen_text"], grab=lambda t: t, read=_reader({}), max_frames=10)
    assert out.frames == 10 and out.events == [] and out.menus == []


# ---- in the fused score ----------------------------------------------------------------


@pytest.fixture
def fused(monkeypatch):
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from analysis import fusion, highlights
    from core.models import ClipCandidate, Segment

    monkeypatch.setattr(highlights, "find_highlights", lambda *_a, **_k: (
        [ClipCandidate(start=0, end=30, score=60, hook="h", reason="r")], []))
    monkeypatch.setattr(highlights, "score_windows", lambda _s, _l, windows, **_k: [
        ClipCandidate(start=s, end=e, score=60, hook="h", source="signal") for s, e in windows])
    monkeypatch.setattr(fusion, "reaction_for_window", lambda *_a, **_k: 0.5)

    def run(screen):
        from analysis.chat_moments import chat_signal

        monkeypatch.setattr(fusion, "_read_screen", lambda *_a, **_k: screen)
        n = 900
        rng = np.random.default_rng(2)
        msgs = [(float(t), f"u{i}", "hi chat") for i, t in enumerate(rng.uniform(0, n, 600))]
        for at in (206, 606):                                   # chat erupts at 200 and 600
            msgs += [(at + j * 0.1, f"p{at}{j}", "POGGERS") for j in range(40)]
        k = gaming.knowledge()
        chat = chat_signal(msgs, n, k["chat_classes"], k["chat_lag_seconds"])
        audio = {"spike": np.ones(n, dtype=np.float32), "burst": np.zeros(n), "noisiness": np.zeros(n)}
        segments = [Segment(start=5.0, end=8.0, text="hello")]
        profile = gaming.profile_for({"clips": {"gaming_scoring": True}}, [{"name": "VALORANT"}], "")
        cfg = {"clips": {"min_duration": 10, "max_duration": 60, "min_score": 0, "max_clips_per_video": 0},
               "analysis": {"chunk_seconds": 600, "chunk_overlap_seconds": 30,
                            "long_video_threshold_seconds": 3600, "max_overlap": 0.3,
                            "max_text_similarity": 0.8, "max_segment_reuse": 0.5},
               "scoring": {"rerank_pool": 0}, "tracking": {"detector": "yolov8n-pose.pt"}}
        kept, _ = fusion.find_clips("v.mp4", segments, None, cfg, signals=(audio, {"motion": np.zeros(n)}),
                                    gaming=profile, chat=chat)
        return {round(c.start): c for c in kept}
    return run


def test_a_menu_chat_reacted_to_is_marked_down_and_a_banner_backs_chat_up(fused):
    screen = ScreenText(events=[(603.0, "ON SCREEN: ACE")], menus=[(190.0, 215.0, "SETTINGS, BACK")], frames=12)
    base = fused(ScreenText())
    read = fused(screen)
    menu = next(c for s, c in read.items() if s <= 200 <= c.end)
    ace = next(c for s, c in read.items() if s <= 600 <= c.end)
    assert menu.subscores["menu"] == -12 and "game_bonus" not in menu.subscores
    assert "ON SCREEN: a menu" in menu.subscores["game_why"]
    assert ace.subscores["game_bonus"] == 8 and "ON SCREEN: ACE" in ace.subscores["game_why"]
    before = next(c for s, c in base.items() if s <= 600 <= c.end)
    assert "game_bonus" not in before.subscores                 # chat alone, nothing to agree with
    assert ace.score == min(100, before.score + 8)
