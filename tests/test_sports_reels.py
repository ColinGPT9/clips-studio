"""A match's story reels (sports/core/reels.py): which clips each reel holds,
in match order, and the join that makes them. Neutral names only."""

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("yaml")

import sports
from sports.core import reels


def _clip(t, label="Goal", event="goal", team="", player="", minute=None, replay=False, path="x.mp4"):
    scores = {"sport_event": event, "sport_label": label, "sport_t": float(t)}
    if team:
        scores["sport_team"] = team
    if player:
        scores["sport_player"] = player
    if minute is not None:
        scores["sport_minute"] = minute
    if replay:
        scores["sport_replay"] = True
    return SimpleNamespace(candidate=SimpleNamespace(start=float(t) - 14, subscores=scores), path=Path(path))


def test_the_recap_is_every_named_moment_in_match_order():
    clips = [_clip(3000, team="AWO"), _clip(1000, team="HOM"), _clip(2000, "Big moment", "big_moment"),
             _clip(2500, "Yellow card", "yellow_card"), _clip(1100, replay=True)]
    planned = reels.plan(clips, ["recap"], "HOM 1-1 AWO")
    assert [(r.kind, r.subject, r.title) for r in planned] == [("recap", "", "HOM 1-1 AWO: match recap")]
    assert [c.candidate.subscores["sport_t"] for c in planned[0].parts] == [1000.0, 2500.0, 3000.0]


def test_a_reel_per_team_and_per_player_with_two_moments_or_more():
    clips = [_clip(100, team="HOM", player="Player A"), _clip(200, team="HOM", player="Player A"),
             _clip(300, team="HOM", player="Player A"), _clip(400, team="AWO", player="Player B"),
             _clip(500, team="HOM", player="Player C"), _clip(600, team="HOM", player="Player C")]
    planned = reels.plan(clips, ["teams", "players"])
    assert [(r.kind, r.subject, r.title, len(r.parts)) for r in planned] == [
        ("team", "HOM", "HOM: the match", 5),
        ("player", "Player A", "Player A: the most moments", 3),
        ("player", "Player C", "Player C: the match", 2)]
    # One file each, whatever its length; two names that read alike stay apart.
    assert [reels.file_name(r)[:-9] for r in planned] == ["reel_team_hom", "reel_player_player-a", "reel_player_player-c"]
    assert reels.file_name(reels.Reel("player", "Player-A", "", [])) != reels.file_name(planned[1])
    # Nothing asked, nothing made; one moment isn't a reel.
    assert reels.plan(clips, []) == []
    assert reels.plan(clips[:1], ["recap"]) == []


def test_the_names_typed_and_how_the_commentary_says_them():
    assert reels.names("Player A, Player B & Player C and Player D / HOM vs AWO, player a") == [
        "Player A", "Player B", "Player C", "Player D", "HOM", "AWO"]
    assert reels.says("What a finish from Player Joel!", "Player Joël")  # accents aside
    assert reels.says("Surname again!", "First Surname")                 # the surname alone
    assert not reels.says("Kaneda runs", "Kane")                         # whole words only
    assert not reels.says("First runs", "First Surname")                 # a first name isn't enough


def test_a_player_typed_in_teams_or_players_gets_the_moments_the_commentary_names():
    clips = [_clip(100, team="HOM"), _clip(200, "Yellow card", "yellow_card"), _clip(300, team="AWO"),
             _clip(400, team="HOM", player="Player B"), _clip(500, "Big moment", "big_moment")]
    commentary = {100: "and Surname scores for HOM", 200: "a booking for surname",
                  300: "HOM concede", 400: "Player B, and Surname in the box", 500: "Surname again"}

    def said(c):
        return commentary[int(c.candidate.subscores["sport_t"])]

    commentary[300] = "Home United concede"
    planned = reels.plan(clips, ["players"], typed="First Surname, HOM, Home United, Player B", said=said)
    # Surname: named in three moments (a big moment isn't one), and more than
    # anyone else. HOM is a team the score box read, by its code or its name:
    # its team reel covers it. Player B: the match events' moment, and the
    # commentary's none besides.
    assert [(r.title, [c.candidate.subscores["sport_t"] for c in r.parts]) for r in planned] == [
        ("First Surname: the most moments", [100.0, 200.0, 400.0])]
    # Without a transcript, only the match events name players.
    assert reels.plan(clips, ["players"], typed="First Surname", said=None) == []


def test_the_description_lists_each_moment_where_it_starts():
    parts = [_clip(100, team="HOM", minute=18), _clip(200, "Yellow card", "yellow_card", player="Player B")]
    assert reels.chapters(parts, [24.0, 21.5]) == "0:00 Goal 18' HOM\n0:24 Yellow card Player B"


def test_the_option_takes_the_reels_it_knows():
    assert sports.clean({"name": "soccer", "reels": ["players", "recap"]})["reels"] == ["recap", "players"]
    assert sports.clean({"name": "soccer", "reels": "recap, teams"})["reels"] == ["recap", "teams"]
    assert "reels" not in sports.clean({"name": "soccer", "reels": []})
    with pytest.raises(ValueError, match="reels must be some of"):
        sports.clean({"name": "soccer", "reels": ["bloopers"]})


def _ffmpeg():
    try:
        from core.binaries import ffmpeg

        exe = ffmpeg()
    except Exception:
        exe = None
    if not exe or not (Path(exe).exists() or shutil.which(exe)):
        pytest.skip("ffmpeg isn't installed here")
    return exe


class _Ran:
    def __init__(self, stdout=""):
        self.returncode, self.stdout, self.stderr = 0, stdout, ""


def _video_packets(frames, card, rate=(30000, 1001), keyframes=(), gap=0):
    """ffprobe's JSON for a finished clip's video: `frames` of the clip, then
    `card` frames of the end card, `gap` ticks after the clip's last frame
    ends. The card opens on a keyframe (the clip's first frame and
    `keyframes` are keyframes too)."""
    tick = rate[1]
    packets = [{"pts": i * tick + (gap if i >= frames else 0), "duration": tick,
                "flags": "K__" if i in (0, frames, *keyframes) else "___"} for i in range(frames + card)]
    return json.dumps({"streams": [{"time_base": f"1/{rate[0]}"}], "packets": packets})


def _fake_probe(monkeypatch, length, video="", joined_at="0.000000\n"):
    """FFmpeg and ffprobe as reels.join sees them: every part is `length`
    seconds long (its sound may run past its last frame) and its video
    packets read as `video` says. Returns the joins run, with the listing."""
    joins = []

    def run(cmd, *a, **k):
        cmd = [str(c) for c in cmd]
        if "format=duration" in cmd:
            return _Ran(f"{length:.6f}\n")
        if "stream=time_base:packet=pts,duration,flags" in cmd:
            return _Ran(video)
        if "-read_intervals" in cmd:            # where the joined video starts
            return _Ran(joined_at)
        joins.append((cmd, Path(cmd[cmd.index("-i") + 1]).read_text(encoding="utf-8")))
        Path(cmd[-1]).write_bytes(b"reel")
        return _Ran()

    monkeypatch.setattr(subprocess, "run", run)
    return joins


def test_the_reels_join_starts_the_video_at_0(monkeypatch, tmp_path):
    """-copyts keeps the first clip's video at 0 rather than behind an empty
    edit, which phones show as a black first frame, and the final writes'
    flags keep it at 0 for an app that ignores edit lists. Each clip's card
    is still left off with an outpoint. (No numpy or Pillow needed: the join
    is in video/encoding.py.)"""
    joins = _fake_probe(monkeypatch, 4.0)
    out = tmp_path / "reel.mp4"
    assert reels.join([tmp_path / "a.mp4", tmp_path / "b.mp4"], out, [1.0, 0.0]) == [3.0, 4.0]
    [(cmd, listing)] = joins
    at = cmd.index("-i")
    assert cmd[1:at] == ["-y", "-v", "error", "-f", "concat", "-safe", "0"]
    assert cmd[at + 2:] == ["-copyts", "-c", "copy", "-movflags", "+faststart+negative_cts_offsets", str(out)]
    # Packets that can't be read: the cut is made as before, to the millisecond.
    assert listing.splitlines()[1] == "outpoint 3.000" and "outpoint" not in listing.splitlines()[-1]


def test_each_clip_is_cut_where_its_card_starts_on_a_whole_frame(monkeypatch, tmp_path):
    """91 frames at 29.97 fps, then the card: 86 frames (2.9 s to its frames,
    less the one its sound cut), and the clip's sound runs 25 ms past its
    last frame, so the file is 5.939 s long. The cut is the card's first
    frame, 91 x 1001/30000 = 3.0363666 s, floored to the microsecond: never
    past it. As before it was the file's length less 2.9 s, to the
    millisecond, 3.039: past it, and the card's first frame went in."""
    joins = _fake_probe(monkeypatch, 5.939, _video_packets(91, 86, keyframes=(30, 60)))
    out = tmp_path / "reel.mp4"

    kept = reels.join([tmp_path / "a.mp4", tmp_path / "b.mp4"], out, [2.9, 2.9])
    [(_cmd, listing)] = joins
    assert listing.splitlines()[1] == "outpoint 3.036366" == listing.splitlines()[3]
    assert kept == [3.036366, 3.036366]
    assert reels.lengths([tmp_path / "a.mp4"], [2.9]) == [3.036366]


def test_the_card_is_found_by_the_latest_keyframe_near_where_it_should_start(monkeypatch, tmp_path):
    """The clip has keyframes of its own (frames 30 and 60, its cuts) and
    the card one more, 29 frames in: the card's first is still the one."""
    joins = _fake_probe(monkeypatch, 5.939, _video_packets(91, 86, keyframes=(30, 60, 120)))
    assert reels.join([tmp_path / "a.mp4"], tmp_path / "reel.mp4", [2.9]) == [3.036366]
    assert joins[0][1].splitlines()[1] == "outpoint 3.036366"


@pytest.mark.parametrize(("length", "gap"), [(5.9059, 0), (5.9309, 750)])
def test_a_keyframe_on_the_clips_last_frame_is_not_taken_for_the_card(monkeypatch, tmp_path, length, gap):
    """A card a frame short of 2.9 s (86 frames at 29.97 fps) puts where it
    should start a frame before it, and the clip's last frame (90) is a
    keyframe too: a cut on it, or a periodic one. That keyframe is the
    nearer, and taken for the card it cut the clip a frame short (3.003),
    so its last frame was lost at the seam. The card's is the later one.
    Also with the card joined on 25 ms after the clip, its sound past it."""
    joins = _fake_probe(monkeypatch, length, _video_packets(91, 86, keyframes=(30, 60, 90), gap=gap))
    assert reels.join([tmp_path / "a.mp4"], tmp_path / "reel.mp4", [2.9]) == [3.036366]
    assert joins[0][1].splitlines()[1] == "outpoint 3.036366"


def test_a_card_joined_on_after_the_clips_sound_is_cut_where_the_clip_ends(monkeypatch, tmp_path):
    """A clip whose sound runs 25 ms past its last frame has its card joined
    on after that sound, 25 ms after the last frame. The cut is still the
    end of the clip's last frame, so the next clip follows it a frame later,
    not a frame and 25 ms."""
    joins = _fake_probe(monkeypatch, 5.964, _video_packets(91, 86, keyframes=(30,), gap=750))
    assert reels.join([tmp_path / "a.mp4"], tmp_path / "reel.mp4", [2.9]) == [3.036366]
    assert joins[0][1].splitlines()[1] == "outpoint 3.036366"


@pytest.mark.parametrize(("video", "why"), [
    (_video_packets(91, 86, keyframes=(30,))[:-2], "unreadable"),
    (json.dumps({"streams": [{"time_base": "1/30000"}], "packets": []}), "no packets"),
    # The card was skipped: the clip's last keyframe is nowhere near 2.9 s
    # from its end.
    (_video_packets(177, 0, keyframes=(30,)), "no card"),
    # A card that starts 0.2 s away from where 2.9 s says.
    (_video_packets(85, 92, keyframes=(30,)), "too far"),
    # Nothing of the clip before the card.
    (_video_packets(0, 86), "only a card"),
])
def test_a_clip_whose_card_cant_be_found_is_cut_as_before(monkeypatch, tmp_path, video, why):
    joins = _fake_probe(monkeypatch, 5.931, video)
    reels.join([tmp_path / "a.mp4"], tmp_path / "reel.mp4", [2.9])
    [(_cmd, listing)] = joins
    assert listing.splitlines()[1] == "outpoint 3.031", why


def test_a_clip_without_a_card_goes_in_whole(monkeypatch, tmp_path):
    joins = _fake_probe(monkeypatch, 5.931, _video_packets(91, 86))
    assert reels.join([tmp_path / "a.mp4"], tmp_path / "reel.mp4", [0.0]) == [5.931]
    [(_cmd, listing)] = joins
    assert "outpoint" not in listing


def test_clips_are_joined_without_their_end_cards(tmp_path):
    exe = _ffmpeg()
    parts = []
    for i in range(2):
        p = tmp_path / f"part{i}.mp4"
        subprocess.run([exe, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=108x192:rate=30:duration=4",
                        "-f", "lavfi", "-i", "sine=duration=4", "-c:v", "libx264", "-g", "30", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(p)], check=True)
        parts.append(p)
    out = tmp_path / "reel.mp4"
    # The first has a 1 s card to leave off; the second has none, and keeps it all.
    lengths = reels.join(parts, out, [1.0, 0.0])
    assert out.exists() and abs(lengths[0] - 3.0) < 0.2 and abs(lengths[1] - 4.0) < 0.2
    assert abs(reels.duration(out) - 7.0) < 0.6
    # The reel's video starts at 0, not after the audio's priming, and not
    # only through the edit list (the parts have B-frames).
    from core.binaries import ffprobe

    for ignore in ([], ["-ignore_editlist", "1"]):
        first = subprocess.run([ffprobe(), "-v", "error", *ignore, "-select_streams", "v:0",
                                "-read_intervals", "%+#5", "-show_entries", "frame=pts_time",
                                "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.split()
        assert abs(min(float(f.strip(",")) for f in first)) < 0.0005, (ignore, first)


def _packet_hashes(path, stream="v:0") -> list[str]:
    from core.binaries import ffprobe

    out = subprocess.run([ffprobe(), "-v", "error", "-select_streams", stream, "-show_entries",
                          "packet=data_hash", "-show_data_hash", "md5", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout
    return [line.strip().strip(",") for line in out.splitlines() if line.strip()]


@pytest.mark.parametrize(("rate", "sample_rate", "past", "key_last"), [
    # The sound runs 25 ms past the last frame (loudness levelling off), so
    # the card was joined on 25 ms after it: the cut leaves all of the card out.
    (Fraction(30000, 1001), 48000, 0.025, False),
    # The sound ends with the picture: the card follows the clip at once.
    (Fraction(60000, 1001), 96000, 0.0, False),
    # Each clip's last frame is a keyframe (a cut on it, or a periodic one),
    # a frame before where the 86-frame card should start: with the sound
    # past the picture and without.
    (Fraction(30000, 1001), 48000, 0.025, True),
    (Fraction(30000, 1001), 48000, 0.0, True),
])
def test_a_reel_has_every_frame_of_each_clip_and_none_of_its_card(tmp_path, rate, sample_rate, past, key_last):
    """Real clips as the app finishes them: B-frames, and an end card (one
    keyframe, no B-frames, a frame short of 2.9 s) joined on with
    video.encoding.concat. Every frame of each clip is in the reel once,
    none of a card's is, and the frames follow one another exactly a frame
    apart across every seam. (Cut to the millisecond, a card frame went in
    at a seam, or the clip's last frame was held 25 ms too long, or the next
    clip came a fraction of a millisecond early. Cut at the clip's own last
    keyframe, taken for the card's, its last frame was lost and the next
    clip's first took its time.) Of a card's sound, only the 1024 samples of
    silence its encoder starts with may go in, when the card follows the
    clip at once, as it always did."""
    exe = _ffmpeg()
    from core.binaries import ffprobe
    from video.encoding import concat

    final = ["-movflags", "+faststart+negative_cts_offsets"]
    audio = ["-c:a", "aac", "-b:a", "128k", "-ar", str(sample_rate), "-ac", "2"]

    def make(out, frames, seed, *video, seconds=None):
        """`frames` of H.264, then muxed with pink noise as long as they are
        (and `seconds` more)."""
        pic, sound = out.with_suffix(".video.mp4"), out.with_suffix(".m4a")
        subprocess.run([exe, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=160x284:rate={rate}",
                        "-frames:v", str(frames), *video, "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", "-an", str(pic)], check=True, capture_output=True)
        subprocess.run([exe, "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"anoisesrc=d={float(frames / rate) + (seconds or 0):.6f}:c=pink:r={sample_rate}:a=0.2:seed={seed}",
                        *audio, str(sound)], check=True, capture_output=True)
        subprocess.run([exe, "-v", "error", "-y", "-i", str(pic), "-i", str(sound), "-map", "0:v", "-map", "1:a",
                        "-c", "copy", *final, str(out)], check=True, capture_output=True)
        return out

    card = make(tmp_path / "card.mp4", round(2.9 * rate) - 1, 9, "-bf", "0", "-g", "1000",
                "-x264-params", "scenecut=0")
    parts, clip_frames, clip_sound = [], [], set()
    for i, frames in enumerate((91, 120, 75)):
        key = ["-force_key_frames", f"expr:eq(n,{frames - 1})"] if key_last else []
        clip = make(tmp_path / f"clip{i}.pre-card.mp4", frames, i, "-vf", f"hue=h={i * 120}", *key, seconds=past)
        clip_sound |= set(_packet_hashes(clip, "a:0"))
        part = tmp_path / f"clip{i}.mp4"
        listing = tmp_path / f"clip{i}.txt"
        listing.write_text(f"file '{clip.as_posix()}'\nfile '{card.as_posix()}'\n", encoding="utf-8")
        assert concat(listing, part).returncode == 0
        if key_last:        # the clip's last frame, last decoded of the clip, is a keyframe
            flags = subprocess.run([ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                                    "packet=flags", "-of", "csv=p=0", str(part)],
                                   capture_output=True, text=True).stdout.split()
            assert "K" in flags[frames - 1] and "K" in flags[frames], flags[frames - 3:frames + 2]
        parts.append(part)
        clip_frames.append(_packet_hashes(part)[:frames])      # the clip's, in decoding order
    out = tmp_path / "reel.mp4"

    kept = reels.join(parts, out, [2.9] * 3)
    assert [round(k * rate, 3) for k in kept] == [91.0, 120.0, 75.0]
    video = _packet_hashes(out)
    assert video == [h for frames in clip_frames for h in frames], "a frame of a clip is missing, doubled or a card's"
    # (A packet of silence can be byte for byte the same in the card and a clip.)
    card_sound = _packet_hashes(card, "a:0")
    went_in = [h for h in _packet_hashes(out, "a:0") if h in set(card_sound) - clip_sound]
    assert went_in == ([card_sound[0]] * 3 if not past else []), "the card's sound went in"
    shown = subprocess.run([ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pts_time",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.split()
    times = [Fraction(t.strip(",")) for t in shown]
    assert len(times) == 286 and times[0] == 0
    steps = [round((b - a) * rate, 2) for a, b in zip(times, times[1:])]
    assert set(steps) == {1}, [(i, s) for i, s in enumerate(steps) if s != 1]


def test_a_reel_is_made_again_into_its_own_row(tmp_path, db, monkeypatch):
    """The reels are joined from every clip of the run, the re-rendered ones
    too. A reel is known by what it is, not by its length: a re-run makes it
    again into its own row and file, with one end card, and a reel as long
    as another never takes its row."""
    exe = _ffmpeg()
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from core import pipeline
    from core.models import ClipCandidate, Segment
    from video import outro

    monkeypatch.setattr(outro, "DURATION", 1.0)
    cards = []

    def finish(src, dst, _config):
        cards.append(Path(dst).name)
        shutil.copyfile(src, dst)
        Path(src).unlink()
        return True

    monkeypatch.setattr(outro, "finish", finish)
    db.upsert_video("vid", title="A match")
    clips = []
    for i, t in enumerate((1000, 2000)):
        p = tmp_path / f"clip{i}.mp4"
        # 3 s of window and a 1 s card: the file is 4 s long.
        subprocess.run([exe, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=108x192:rate=30:duration=4",
                        "-f", "lavfi", "-i", "sine=duration=4", "-c:v", "libx264", "-g", "30", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(p)], check=True)
        scores = {"sport_event": "goal", "sport_label": "Goal", "sport_t": float(t), "sport_team": "HOM"}
        clips.append(SimpleNamespace(candidate=ClipCandidate(start=t - 2.0, end=t + 1.0, score=80, hook="",
                                                             subscores=scores), path=p))
    recap = SimpleNamespace(option={"reels": ["recap"]}, report_data={"score": "HOM 2-0 AWO"})

    def rows():
        return {Path(r["path"]).relative_to(tmp_path).as_posix(): r for r in db.clips_for_video("vid")}

    made = pipeline._sport_reels(db, "vid", recap, clips, tmp_path, {})
    assert [m.path.name for m in made] == ["reel_recap.mp4"] and abs(made[0].candidate.end - 6.0) < 0.2
    assert cards == ["reel_recap.mp4"] and not list(tmp_path.glob("*.pre-card.mp4"))
    assert abs(reels.duration(made[0].path) - 6.0) < 0.6

    # The same clips again: made again into its row and file, one more card.
    db.set_clip(rows()["reel_recap.mp4"]["id"], title="My title")
    assert pipeline._sport_reels(db, "vid", recap, clips, tmp_path, {}) == []
    assert list(rows()) == ["reel_recap.mp4"] and rows()["reel_recap.mp4"]["title"] == "My title"
    assert cards == ["reel_recap.mp4"] * 2

    # Longform's 16:9 reel: a row of its own, its profile marking it Longform.
    wide = pipeline._sport_reels(db, "vid", recap, clips, tmp_path / "Longform", {},
                                 start=0.011, opts={"profile": "short_clips"})
    assert len(wide) == 1 and wide[0].candidate.start == 0.011
    assert json.loads(rows()["Longform/reel_recap.mp4"]["render_opts"]) == {"profile": "short_clips", "reel": "recap"}

    # A name typed in Teams or players, said in both clips' commentary (a
    # segment overlapping the window counts; one after it doesn't). Its reel
    # is as long as the recap, and gets a row of its own beside it.
    said = [Segment(start=995.0, end=999.5, text="Surname with the ball"),
            Segment(start=2000.5, end=2004.0, text="and SURNAME scores"),
            Segment(start=2002.0, end=2010.0, text="Player C, after the whistle")]
    named = SimpleNamespace(option={"reels": ["players"], "teams": "Surname, Player C"}, report_data={})
    made = pipeline._sport_reels(db, "vid", named, clips, tmp_path, {}, said)
    assert [m.candidate.hook for m in made] == ["Surname: the match"]
    player = rows()[made[0].path.name]
    assert json.loads(player["render_opts"]) == {"reel": "player", "of": "Surname"}
    assert len(player["description"].splitlines()) == 2    # both moments, as chapters
    assert rows()["reel_recap.mp4"]["title"] == "My title" and len(rows()) == 3
    assert player["end_s"] != rows()["reel_recap.mp4"]["end_s"]
