"""The branded end card: that it is the real mascot, and that it costs nothing.

Two kinds of guard here, both from things that actually went wrong while this
was being built.

The cheap ones run always. The ones that shell out to FFmpeg build a few
seconds of test video, so they are skipped when FFmpeg is not resolvable.
"""

import copy
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from core import binaries
from video import outro

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = ROOT / "assets" / "outro"


def _have_ffmpeg() -> bool:
    try:
        return subprocess.run([binaries.ffmpeg(), "-version"],
                              capture_output=True).returncode == 0
    except OSError:
        return False


needs_ffmpeg = pytest.mark.skipif(not _have_ffmpeg(), reason="FFmpeg not available")


# ---------------------------------------------------------------- the artwork
def test_rig_is_the_shipped_mascot_pixel_for_pixel():
    """The animation poses the REAL mascot, not a lookalike.

    video/outro.py splits mascot_art's drawing into layers so they can move
    independently. Split wrongly, the result still looks like a cat and the
    drift is invisible in review — the first attempt composited the eyes last
    and put the whites on top of the blush, 529 pixels off, which nothing but
    this check would have caught.
    """
    assert outro.verify()["pixels_differing"] == 0


def test_mascot_files_still_regenerate_identically():
    """`scripts/make_mascot.py` must keep producing the committed artwork.

    The geometry moved out of scripts/ into video/mascot_art.py so the frozen
    build can import it — scripts/ is not packaged. That move is only safe if
    the PNGs come out byte-identical.
    """
    brand = ROOT / "docs" / "brand"
    before = {p: p.read_bytes() for p in
              (brand / "mascot.png", brand / "mascot-head.png")}
    subprocess.run(["python", str(ROOT / "scripts" / "make_mascot.py")],
                   cwd=ROOT, capture_output=True, check=True)
    for path, original in before.items():
        assert path.read_bytes() == original, f"{path.name} changed"


def test_nothing_enters_a_platform_safe_zone():
    """TikTok covers the bottom 483px, the top 130 and a 140px action rail.

    The tagline used to sit at 1619 — cut off by 182px — and the ball's resting
    position poked 5px under the rail because it was clamped against the ball's
    STARTING radius, and it unravels as it plays.
    """
    import numpy as np

    frames = outro.frames(1080, 1920, 30)
    top, bottom, right = 9999, 0, 0
    for im in frames:
        ink = np.abs(np.asarray(im, dtype=int)
                     - np.array(outro.ORANGE)).sum(axis=2) > 40
        rows, cols = np.nonzero(ink)
        if len(rows):
            top = min(top, rows.min())
            bottom = max(bottom, rows.max())
            right = max(right, cols.max())
    assert top > 130, f"ink at y={top} is under TikTok's status bar"
    assert bottom < 1437, f"ink at y={bottom} is under TikTok's caption band"
    assert right < 940, f"ink at x={right} is under the action rail"


def test_landscape_uses_the_width_instead_of_a_centre_column():
    """16:9 gets its own arrangement, not the portrait one made smaller.

    Scaling uniformly by min(w/1080, h/1920) is 0.5625 at 16:9, which left
    Clippy and the type stacked in a narrow middle column with roughly two
    thirds of the frame empty orange. Landscape puts him on the left with his
    yarn and the branding on the right.
    """
    L = outro._Layout(1920, 1080)
    assert L.landscape

    cat_l, cat_r = L.cat_x + L.cat_bbox[0], L.cat_x + L.cat_bbox[2]
    word_w = L.wb[2] - L.wb[0]
    text_l = L.text_x(word_w)
    text_r = text_l + word_w

    assert cat_r < text_l, "Clippy and the wordmark overlap"
    # Together they must actually occupy the frame, not huddle in the middle.
    assert (cat_r - cat_l) > 0.2 * 1920, "Clippy is too small for a 16:9 frame"
    assert (text_r - text_l) > 0.25 * 1920, "the wordmark is too small"
    assert cat_l > 0 and text_r < 1920, "content runs off the frame"
    # Well clear of a landscape player's controls and title bar.
    assert L.cat_y + L.cat_bbox[1] > 0.05 * 1080
    assert L.cat_y + L.cat_bbox[3] < 0.90 * 1080
    assert L.text_top > 0.05 * 1080
    assert L.text_top + L.text_h < 0.90 * 1080


def test_landscape_ball_never_rolls_under_the_wordmark():
    L = outro._Layout(1920, 1080)
    assert L.ball_limit < L.text_x(L.wb[2] - L.wb[0]), (
        "the ball may come to rest on top of the type")


def test_portrait_layout_is_untouched_by_the_landscape_branch():
    """Portrait is the signed-off design. These are the numbers it shipped
    with; the landscape work must not have moved any of them."""
    L = outro._Layout(1080, 1920)
    assert not L.landscape
    assert L.cat == 864
    assert L.text_x(100) == (1080 - 100) // 2       # the original expression
    # His mass centre on the frame's centre, within the pixel that an integer
    # paste position costs — cat_y truncates, so it lands at 959.02.
    assert abs(L.cat_x + L.com[0] - 540) <= 1
    assert abs(L.cat_y + L.com[1] - 960) <= 1


def test_duration_is_a_whole_number_of_frames():
    """2.9s was chosen over 2.95 so 30fps lands on 87 frames exactly, with no
    half frame at the cut."""
    for fps in (30, 60):
        assert abs(round(fps * outro.DURATION) - fps * outro.DURATION) < 1e-9


# ---------------------------------------------------------------- the cache
def test_fps_key_is_not_stringified_float():
    """probe() divides r_frame_rate, so a 30fps clip arrives as 30.0.

    Keyed naively that is `..._30.0_...`, which never matched the shipped
    `..._30_...` card — so every install rendered its own copy of a file it
    already had, and the bundled assets were dead weight.
    """
    fmt = {"w": 1080, "h": 1920, "fps": 30.0, "pix_fmt": "yuv420p",
           "sample_rate": 48000, "channels": 2}
    assert outro._key(fmt) == "outro_1080x1920_30_yuv420p_48000_2.mp4"
    assert outro._fps_tag(29.97) == "29.97"      # fractional rates keep theirs


@pytest.mark.parametrize("size,fps", [((1080, 1920), 30), ((1080, 1920), 60),
                                      ((1920, 1080), 30), ((1920, 1080), 60)])
def test_every_format_the_pipeline_emits_ships_prebuilt(size, fps):
    """core/pipeline.py only ever produces these two canvases, and clips
    inherit the source's frame rate — 30 and 60 cover nearly all of it.

    If one of these is missing, that format renders for ~20s on first use
    instead of costing nothing.
    """
    fmt = {"w": size[0], "h": size[1], "fps": fps, "pix_fmt": "yuv420p",
           "sample_rate": 48000, "channels": 2}
    assert (SHIPPED / outro._key(fmt)).exists()
    assert outro._bundled(outro._key(fmt)) is not None


def test_prebuilt_cards_are_actually_committed():
    """.gitignore excludes *.mp4 — user content never belongs in the repo.

    These cards are build inputs, not user content, and without an exception
    they are invisible: present on the machine that generated them, absent
    from every clone and from the release build. Nothing else here would
    notice, because the files exist locally.
    """
    tracked = subprocess.run(["git", "ls-files", "assets/outro"], cwd=ROOT,
                             capture_output=True, text=True)
    if tracked.returncode != 0:
        pytest.skip("not a git checkout")
    committed = {line.strip() for line in tracked.stdout.splitlines() if line.strip()}
    on_disk = {f"assets/outro/{p.name}" for p in SHIPPED.glob("*.mp4")}
    assert on_disk, "no prebuilt cards on disk"
    assert on_disk <= committed, (
        f"not committed, so they will be missing from the release: "
        f"{sorted(on_disk - committed)}")


def test_spec_bundles_the_prebuilt_cards():
    spec = (ROOT / "clips-studio.spec").read_text(encoding="utf-8")
    assert 'assets" / "outro"' in spec or "assets/outro" in spec, (
        "clips-studio.spec must bundle assets/outro, or the frozen build "
        "renders every format from scratch")


# ---------------------------------------------------------------- the setting
def test_absent_setting_means_on():
    """Existing users have no `outro:` line. They must get it on upgrade, and
    anyone who turned it off must keep it off."""
    assert outro.enabled({}) is True
    assert outro.enabled({"clips": {}}) is True
    assert outro.enabled({"clips": {"outro": True}}) is True
    assert outro.enabled({"clips": {"outro": False}}) is False


def _patch_yaml(text: str, on: bool) -> tuple[str, int]:
    """The exact two substitutions server/api.py:patch_settings performs."""
    value = "true" if on else "false"
    text, n = re.subn(r"(?m)^(\s*outro:\s*)\S*", rf"\g<1>{value}", text, count=1)
    if n == 0:
        text, n = re.subn(r"(?m)^(clips:[^\S\n]*$)", rf"\g<1>\n  outro: {value}",
                          text, count=1)
    return text, n


def test_toggling_off_works_on_a_file_with_no_outro_line():
    """The case that would have shipped broken.

    patch_settings' flat rewrite is anchored at column 0, and `outro:` is
    indented under `clips:` — so it could never match. Worse, every existing
    user's settings.yaml has no such line at all, so without the insert the
    endpoint would 400 for exactly the people trying to turn the branding off.
    """
    legacy = "clips:\n  min_score: 55\n  vertical: true\n\nscoring:\n  weights: {}\n"
    off, n = _patch_yaml(legacy, False)
    assert n == 1
    assert yaml.safe_load(off)["clips"]["outro"] is False

    back, n = _patch_yaml(off, True)             # now the line exists
    assert n == 1 and back.count("outro:") == 1
    assert yaml.safe_load(back)["clips"]["outro"] is True


def test_shipped_settings_yaml_has_the_key():
    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert cfg["clips"]["outro"] is True


def test_every_longform_mode_gets_a_card_by_construction():
    """All four profiles, checked at their production point.

    Two of them (Highlights, Edited Streams) build their video with
    longform.assemble and never touch _render_files, which is how they once
    shipped with no card at all. Now:

        _render_files  -> Shorts, longform per-clip, editor re-render
        assemble       -> Highlights, Edited Streams

    Both PRODUCE their output with the card already in it.
    """
    pipeline = (ROOT / "core" / "pipeline.py").read_text(encoding="utf-8")
    render = pipeline[pipeline.index("def _render_files("):
                      pipeline.index("def _register_clip(")]
    assert "_outro.finish(" in render

    assemble = (ROOT / "longform" / "assemble.py").read_text(encoding="utf-8")
    assert "outro.ensure_outro(" in assemble

    # A call site that forgets config= silently loses the card for that whole
    # mode, which is precisely how it went unnoticed before.
    process = (ROOT / "longform" / "process.py").read_text(encoding="utf-8")
    calls = process.count("    assemble(")
    assert calls == 2, f"expected two assemble() call sites, found {calls}"
    assert process.count("config=config,") >= calls, (
        "an assemble() call omits config= — that mode gets no end card")


def test_every_register_clip_call_passes_config():
    """`config` is how _register_clip knows whether the outro is on. A call
    site that forgets it silently produces clips with no end card — which is
    exactly how the longform highlight bug presented."""
    import re as _re

    for rel in ("core/pipeline.py", "longform/process.py", "server/jobs.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for m in _re.finditer(r"_register_clip\((.*?)\)\n", text, _re.S):
            call = m.group(1)
            if call.strip().startswith("\n") or "db:" in call:
                continue                                   # the definition
            assert "config" in call, f"{rel}: _register_clip call omits config"


def test_all_four_longform_profiles_reach_the_funnel():
    """Two of the four wrote their video and registered it without ever
    touching the renderer. Every mode must still end at _register_clip."""
    src = (ROOT / "longform" / "process.py").read_text(encoding="utf-8")
    assert src.count("_register_clip(") == 3, (
        "longform has three registration points — per-clip, highlights and "
        "edited_stream; if that changed, check the new one appends an outro")
    for fn in ("def _highlights(", "def _edited_stream("):
        body = src[src.index(fn):]
        body = body[:body.index("\ndef ", 1)] if "\ndef " in body[1:] else body
        assert "_register_clip(" in body, f"{fn} no longer registers its output"


# ---------------------------------------------------------------- end to end
@pytest.fixture
def clip_factory(tmp_path):
    def make(name="clip.mp4", w=1080, h=1920, fps=30, secs=3):
        p = tmp_path / name
        subprocess.run([
            binaries.ffmpeg(), "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r={fps}:d={secs}",
            "-f", "lavfi", "-i", f"sine=f=220:r=48000:d={secs}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-movflags", "+faststart", str(p)], check=True, capture_output=True)
        return p
    return make


def _packets(path: Path) -> int:
    out = subprocess.run([
        binaries.ffprobe(), "-v", "error", "-select_streams", "v:0",
        "-count_packets", "-show_entries", "stream=nb_read_packets",
        "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return int(out.strip())


def _video_hash(path: Path, frames: int) -> str:
    """MD5 of the raw H.264 elementary stream — no container, no timestamps,
    so this compares the coded video and nothing else."""
    r = subprocess.run([
        binaries.ffmpeg(), "-v", "error", "-i", str(path), "-map", "0:v",
        "-frames:v", str(frames), "-c", "copy", "-bsf:v", "h264_mp4toannexb",
        "-f", "h264", "-"], capture_output=True)
    return hashlib.md5(r.stdout).hexdigest()


@pytest.fixture
def cfg(tmp_path):
    base = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    c = copy.deepcopy(base)
    c.setdefault("paths", {})["data_dir"] = str(tmp_path / "data")
    return c


@needs_ffmpeg
def test_append_does_not_re_encode_the_clip(clip_factory, cfg):
    """The whole reason the cache is keyed on the clip's format: `-c copy`
    only concatenates when both files agree, and that agreement is what makes
    the append free and lossless."""
    clip = clip_factory()
    before = _packets(clip)
    original = _video_hash(clip, before)

    assert outro.append(clip, cfg) is True

    assert _packets(clip) == before + round(30 * outro.DURATION)
    assert _video_hash(clip, before) == original, "the clip was re-encoded"


@needs_ffmpeg
def test_a_shipped_format_never_renders(clip_factory, cfg, monkeypatch):
    """Prebuilt cards exist precisely so the common paths cost nothing. If
    this fails, the first clip on every install stalls for ~20 seconds."""
    calls = []
    monkeypatch.setattr(outro, "_render",
                        lambda *a, **k: calls.append(a) or pytest.fail(
                            "rendered a format that ships prebuilt"))
    outro.append(clip_factory(), cfg)
    assert not calls


@needs_ffmpeg
def test_second_clip_reuses_the_cache(clip_factory, cfg, monkeypatch):
    """Built once per format on a machine — not once per video, and certainly
    not once per clip."""
    outro.append(clip_factory("a.mp4"), cfg)
    calls = []
    monkeypatch.setattr(outro, "_render", lambda *a, **k: calls.append(a))
    outro.append(clip_factory("b.mp4"), cfg)
    assert not calls


@needs_ffmpeg
def test_disabled_leaves_the_clip_exactly_as_it_was(clip_factory, cfg):
    clip = clip_factory()
    before, digest = _packets(clip), _video_hash(clip, _packets(clip))
    cfg["clips"]["outro"] = False

    assert outro.append(clip, cfg) is False
    assert _packets(clip) == before
    assert _video_hash(clip, before) == digest


@needs_ffmpeg
def test_a_broken_outro_never_costs_the_clip(clip_factory, cfg, monkeypatch, capsys):
    """A clip without an end card beats no clip. Issue #74 is the precedent:
    a post-render step that raised destroyed work that had already succeeded.
    """
    clip = clip_factory()
    before, digest = _packets(clip), _video_hash(clip, _packets(clip))
    monkeypatch.setattr(outro, "ensure_outro",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    assert outro.append(clip, cfg) is False       # no exception escapes
    assert _packets(clip) == before
    assert _video_hash(clip, before) == digest
    assert "outro skipped" in capsys.readouterr().out


def test_replace_waits_out_a_transient_windows_lock(tmp_path, monkeypatch):
    """A freshly written clip is routinely held for a moment by the antivirus
    scanner. os.replace does not wait — it raises — and that cost exactly one
    clip its end card on the first real run (WinError 5)."""
    src, dst = tmp_path / "new.mp4", tmp_path / "clip.mp4"
    src.write_bytes(b"new")
    dst.write_bytes(b"old")

    real, calls = Path.replace, []

    def flaky(self, target):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(5, "Access is denied")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    monkeypatch.setattr(outro.time, "sleep", lambda _s: None)

    assert outro._replace_with_retry(src, dst) is True
    assert len(calls) == 3
    assert dst.read_bytes() == b"new"


@needs_ffmpeg
def test_a_truly_unwritable_clip_is_left_alone(clip_factory, cfg, monkeypatch, capsys):
    """"Locked" now means BOTH strategies fail — a rename that is refused and a
    write that is refused, which is what a memory-mapped file does.

    The guarantee is unchanged and is the important one: when the end card
    cannot be added, the clip survives byte-for-byte rather than being
    half-written or lost.
    """
    clip = clip_factory()
    before, digest = _packets(clip), _video_hash(clip, _packets(clip))

    monkeypatch.setattr(
        Path, "replace",
        lambda self, target: (_ for _ in ()).throw(PermissionError(5, "denied")))
    monkeypatch.setattr(outro, "_overwrite_in_place", lambda s, d: False)
    monkeypatch.setattr(outro.time, "sleep", lambda _s: None)

    assert outro.append(clip, cfg) is False
    assert _packets(clip) == before
    assert _video_hash(clip, before) == digest
    assert "outro skipped" in capsys.readouterr().out


def test_stale_build_dirs_are_swept(tmp_path):
    """_render cleans up after itself, but only if the process survives. Two
    of these were left in data/outro by runs killed during development."""
    import os
    import time as _t

    cfg = {"paths": {"data_dir": str(tmp_path)}}
    cache = outro._cache_dir(cfg)
    old, fresh = cache / ".build_1_1", cache / ".build_2_2"
    for d in (old, fresh):
        d.mkdir()
        (d / "junk.png").write_bytes(b"x")
    os.utime(old, (_t.time() - 7200, _t.time() - 7200))

    outro._cache_dir(cfg)                       # sweeps on the next call
    assert not old.exists(), "a two-hour-old build directory should be gone"
    assert fresh.exists(), "a build in progress must not be deleted"


@needs_ffmpeg
def test_unprobeable_input_is_skipped_not_fatal(tmp_path, cfg):
    junk = tmp_path / "not-a-video.mp4"
    junk.write_bytes(b"nonsense")
    assert outro.append(junk, cfg) is False
    assert junk.read_bytes() == b"nonsense"


@needs_ffmpeg
def test_an_unshipped_format_still_works(clip_factory, cfg):
    """44.1kHz audio is not one of the prebuilt cards. It must render one and
    still stream-copy, rather than failing or re-encoding the clip."""
    clip = clip_factory(secs=2)
    subprocess.run([binaries.ffmpeg(), "-y", "-v", "error", "-i", str(clip),
                    "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-ac", "2",
                    str(clip.with_name("odd.mp4"))], check=True, capture_output=True)
    odd = clip.with_name("odd.mp4")
    assert outro.probe(odd)["sample_rate"] == 44100
    before = _packets(odd)
    original = _video_hash(odd, before)

    assert outro.append(odd, cfg) is True
    assert _packets(odd) == before + round(30 * outro.DURATION)
    assert _video_hash(odd, before) == original


@needs_ffmpeg
def test_concurrent_appends_build_the_card_once(clip_factory, cfg, monkeypatch):
    """Clips render on threads. Two of them hitting an unbuilt format must not
    render it twice, or race on a half-written file.

    An earlier version deadlocked here instead: ensure_outro held a plain Lock
    across _render, which reaches _parts() and takes the same lock again.
    """
    import threading

    real, calls = outro._render, []
    monkeypatch.setattr(outro, "_render",
                        lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    fmt = {"w": 640, "h": 640, "fps": 30, "pix_fmt": "yuv420p",
           "sample_rate": 48000, "channels": 2}      # deliberately not shipped

    results = []
    threads = [threading.Thread(target=lambda: results.append(
        outro.ensure_outro(fmt, cfg))) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)

    assert not any(t.is_alive() for t in threads), "ensure_outro deadlocked"
    assert len(calls) == 1, f"rendered {len(calls)} times, expected once"
    assert len(set(results)) == 1 and results[0].exists()


# ---------------------------------------------------------------------------
# The lock, reproduced for real. The earlier test only raised PermissionError
# twice from a stub, which proves the retry loop runs but says nothing about
# whether the BUDGET is long enough. 37 clips lost their end card to a budget
# that was — 3.1 seconds against a scanner that holds a 30MB file for longer.
# ---------------------------------------------------------------------------
def test_retry_outlasts_a_real_lock(tmp_path, monkeypatch):
    """Hold the destination the way a scanner does, release it after a few
    seconds, and require the replace to still land."""
    import threading
    import time as _t

    src, dst = tmp_path / "new.bin", tmp_path / "clip.bin"
    src.write_bytes(b"new")
    dst.write_bytes(b"old")

    released = threading.Event()
    real = Path.replace

    def locked_until_released(self, target):
        if not released.is_set():
            raise PermissionError(5, "Access is denied")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", locked_until_released)
    threading.Timer(3.0, released.set).start()

    began = _t.monotonic()
    assert outro._replace_with_retry(src, dst) is True, (
        "the retry gave up before a 3s lock cleared")
    assert dst.read_bytes() == b"new"
    assert _t.monotonic() - began >= 3.0


def test_a_scanner_that_clears_is_still_waited_out(tmp_path, monkeypatch):
    """The rename budget exists for the transient case, and must not shrink to
    nothing.

    A scanner memory-maps a just-written clip, which blocks the rename AND a
    write, then clears in seconds. Waiting is the right answer there, and the
    atomic rename is worth preferring, so the budget has to cover it.
    """
    import threading

    src, dst = tmp_path / "new.bin", tmp_path / "clip.bin"
    src.write_bytes(b"new")
    dst.write_bytes(b"old")
    released = threading.Event()
    real = Path.replace
    monkeypatch.setattr(
        Path, "replace",
        lambda self, t: real(self, t) if released.is_set()
        else (_ for _ in ()).throw(PermissionError(5, "Access is denied")))
    threading.Timer(2.0, released.set).start()

    assert outro._replace_with_retry(src, dst) is True
    assert dst.read_bytes() == b"new"
    assert outro.REPLACE_BUDGET >= 5, (
        f"REPLACE_BUDGET is {outro.REPLACE_BUDGET}s — too short to ride out a "
        f"scanner, and the in-place fallback cannot help there because a "
        f"memory-mapped file refuses writes too")


def test_tally_reports_the_rate(tmp_path, monkeypatch):
    """Without this the only way to know how many clips got a card was to
    probe every file and compare durations."""
    outro.reset_tally()
    assert outro.summary() == ""

    outro._tally["added"], outro._tally["skipped"] = 47, 2
    assert "47 of 49" in outro.summary() and "2 skipped" in outro.summary()

    outro.reset_tally()
    outro._tally["added"] = 5
    assert outro.summary() == "End card added to 5 of 5 clip(s)"


@needs_ffmpeg
def test_backfill_adds_only_what_is_missing(clip_factory, cfg, tmp_path):
    """Repairing a library must not re-render, must not double up, and must
    leave the clip's own video untouched."""
    from core.state import StateDB

    plain = clip_factory("plain.mp4", secs=3)
    done = clip_factory("done.mp4", secs=3)
    outro.append(done, cfg)                       # this one already has a card

    db = StateDB(tmp_path / "state.db")
    db.upsert_video("v1", title="t", channel_name="c", duration=60)
    # Distinct windows: clips is UNIQUE on (video_id, start_s, end_s), so two
    # rows with the same window silently collapse into one.
    for start, p in ((0.0, plain), (10.0, done)):
        db.add_clip("v1", start, start + 3.0, 90, "hook", path=str(p),
                    status="queued", title="t", description="d",
                    hashtags="[]", scores="{}", render_opts="{}")

    before, digest = _packets(plain), _video_hash(plain, _packets(plain))
    stats = outro.backfill(db, cfg)
    assert stats["added"] == 1 and stats["already"] == 1, stats
    assert _packets(plain) == before + round(30 * outro.DURATION)
    assert _video_hash(plain, before) == digest, "backfill re-encoded the clip"

    again = outro.backfill(db, cfg)               # idempotent
    assert again["added"] == 0 and again["already"] == 2, again
    db.conn.close()


@needs_ffmpeg
def test_a_relative_clip_path_still_works(clip_factory, cfg, monkeypatch):
    """The concat demuxer resolves list entries relative to the LIST's own
    directory, so a relative clip path inside it points nowhere.

    settings.yaml's data_dir is relative in a checkout, so the database holds
    both shapes — and this quietly cost 365 of 1098 clips their end card.
    Every relative one failed; every absolute one worked.
    """
    clip = clip_factory(secs=2)
    monkeypatch.chdir(clip.parent.parent)
    relative = Path(clip.parent.name) / clip.name
    assert not relative.is_absolute()

    before = _packets(clip)
    assert outro.append(relative, cfg) is True
    assert _packets(clip) == before + round(30 * outro.DURATION)


# ---------------------------------------------------------------------------
# The cause that survived every earlier fix: the desktop app holds a clip open
# whenever the UI previews it (GET /media/<id> keeps a Chromium media handle).
# A stubbed PermissionError cannot test this, because it cannot tell the rename
# and the in-place write apart -- and the whole fix is that they differ.
# ---------------------------------------------------------------------------
import contextlib


@contextlib.contextmanager
def _held_like_a_player(path: Path):
    """Hold `path` the way a media element does: read access, sharing read and
    write but NOT delete. That combination blocks os.replace and permits a
    write, which is the asymmetry the fallback relies on."""
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    handle = k32.CreateFileW(
        str(path), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)  # GENERIC_READ
    if handle in (None, -1, 2 ** 64 - 1):
        pytest.skip("could not open a reader handle")
    try:
        yield
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))


@pytest.mark.skipif(not hasattr(__import__("sys"), "getwindowsversion"),
                    reason="Windows file sharing semantics")
def test_replace_is_blocked_but_a_write_is_not(tmp_path):
    """The measurement the fix is built on. If this ever stops holding, the
    fallback is pointless and the strategy needs rethinking."""
    dst, src = tmp_path / "clip.mp4", tmp_path / "joined.mp4"
    dst.write_bytes(b"OLD" * 500)
    src.write_bytes(b"NEW" * 500)

    with _held_like_a_player(dst):
        with pytest.raises(PermissionError):
            src.replace(dst)
        assert outro._overwrite_in_place(src, dst) is True
    assert dst.read_bytes() == b"NEW" * 500


@pytest.mark.skipif(not hasattr(__import__("sys"), "getwindowsversion"),
                    reason="Windows file sharing semantics")
def test_end_card_lands_even_while_the_app_previews_the_clip(clip_factory, cfg):
    """The actual bug, end to end: a clip open in the UI still gets its card.

    One clip sat locked for the full 59s budget and lost its end card because
    the app was streaming it. Waiting longer could never have worked.
    """
    clip = clip_factory(secs=2)
    before = _packets(clip)

    with _held_like_a_player(clip):
        assert outro.append(clip, cfg) is True

    assert _packets(clip) == before + round(30 * outro.DURATION)


@pytest.mark.skipif(not hasattr(__import__("sys"), "getwindowsversion"),
                    reason="Windows file sharing semantics")
def test_uncontended_clips_still_use_the_atomic_rename(clip_factory, cfg, monkeypatch):
    """The fallback is a fallback. With nothing holding the file the normal
    path must stay atomic, or every clip gives up its crash-safety for a case
    that is not happening."""
    used = []
    real = outro._overwrite_in_place
    monkeypatch.setattr(outro, "_overwrite_in_place",
                        lambda s, d: used.append(1) or real(s, d))

    assert outro.append(clip_factory(secs=2), cfg) is True
    assert not used, "wrote in place when a plain rename would have worked"


def test_a_short_write_keeps_the_recoverable_copy(tmp_path, monkeypatch):
    """Not atomic, so the failure mode matters: never delete the good bytes
    while the clip is truncated."""
    dst, src = tmp_path / "clip.mp4", tmp_path / "joined.mp4"
    dst.write_bytes(b"OLD" * 500)
    src.write_bytes(b"NEW" * 500)

    real_open = open

    def truncating_open(path, mode="r", *a, **k):
        f = real_open(path, mode, *a, **k)
        if Path(path) == dst and "r+b" in str(mode):
            f.write = lambda data: real_open.__self__ if False else None  # no-op
        return f

    monkeypatch.setattr("builtins.open", truncating_open)
    assert outro._overwrite_in_place(src, dst) is False
    monkeypatch.undo()
    assert src.exists(), "the only intact copy was deleted"
    assert src.read_bytes() == b"NEW" * 500


# ===========================================================================
# The card is part of PRODUCING the clip, not applied to it afterwards -- the
# way CapCut exports an outro. Everything below is about that being true, and
# staying true, because every earlier design failed for the same reason: it
# modified a finished file, and a clip open in the app's preview cannot be
# modified.
# ===========================================================================
@pytest.mark.skipif(not hasattr(__import__("sys"), "getwindowsversion"),
                    reason="Windows file sharing semantics")
@needs_ffmpeg
def test_finish_writes_through_a_file_the_app_is_holding(clip_factory, cfg, tmp_path):
    """The case that beat every previous version.

    A clip held the way the desktop app holds it during a preview refuses to be
    DELETED but allows itself to be WRITTEN. finish() only ever writes, so the
    lock cannot reach it. One real run managed 37 of 49 because of this.
    """
    src = clip_factory("pre-card.mp4", secs=2)
    dst = tmp_path / "final.mp4"
    dst.write_bytes(b"stale")               # a previous run's clip, still there

    with _held_like_a_player(dst):
        with pytest.raises(PermissionError):
            src.replace(dst)                # what every earlier version did
        assert outro.finish(src, dst, cfg) is True

    assert _packets(dst) == round(30 * outro.DURATION) + 60
    assert not src.exists(), "the scratch render was left behind"


@needs_ffmpeg
def test_finish_never_loses_the_clip(clip_factory, cfg, tmp_path, monkeypatch):
    """If the card cannot be made, the clip must still arrive -- without one.
    A clip with no end card is a nuisance; a missing clip is lost work."""
    src = clip_factory("pre-card.mp4", secs=2)
    dst = tmp_path / "final.mp4"
    monkeypatch.setattr(outro, "ensure_outro",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    assert outro.finish(src, dst, cfg) is False
    assert dst.exists() and _packets(dst) == 60, "the clip itself must survive"
    assert not src.exists()


def test_the_render_path_never_replaces_a_finished_clip():
    """The regression guard for the whole two-day detour.

    Every version that modified a finished clip lost cards to whichever ones
    were open in the UI. If a post-hoc replace reappears in the render path,
    this fails rather than the user noticing weeks later.
    """
    src = (ROOT / "core" / "pipeline.py").read_text(encoding="utf-8")
    body = src[src.index("def _render_files("):src.index("def _register_clip(")]
    assert "outro.finish(" in body or "_outro.finish(" in body, (
        "the render must PRODUCE the final clip with the card")
    assert "_outro.append(" not in body, (
        "append() modifies a finished file — the render must not use it")

    reg = src[src.index("def _register_clip("):]
    assert "_outro.append(" not in reg, (
        "_register_clip must not modify the clip either; the render owns it")


def test_longform_joins_the_card_as_a_part():
    """Highlights and edited streams are assembled from parts with one concat.
    The card belongs in that list, not bolted on after the file exists."""
    src = (ROOT / "longform" / "assemble.py").read_text(encoding="utf-8")
    assert "parts.append(outro.ensure_outro(" in src
    join = src.index("listfile.write_text")
    assert src.index("parts.append(outro.ensure_outro(") < join, (
        "the card must be added BEFORE the concat list is written")
