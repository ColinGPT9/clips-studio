"""Remote rendering (remote_render/): a second PC renders clips for this one.

Off by default and invisible until switched on. When on: workers pair with a
one-time code, every request after that is authenticated, jobs are data (never
commands) matched to what a worker can do, transfers resume and are checked,
a worker that goes silent gives its jobs back, a failure retries, and in
Automatic anything that can't go remote renders here.
"""

import json
import threading
import time
from pathlib import Path

import pytest

from remote_render import protocol, settings

# ---- protocol and settings (pure) ----------------------------------------------------


def test_a_job_carries_the_render_settings_and_no_secrets():
    config = {"clips": {"captions": True, "vertical_live": True}, "tracking": {"detector": "y.pt"},
              "video": {"encoder": "auto"}, "llm": {"backend": "openrouter/x", "data_dir": "d"},
              "upload": {"client_secret": "s"}, "channel": "c"}
    assert set(protocol.render_config(config)) == {"clips", "tracking", "video"}
    a = protocol.job_id("v", 10, 30, {"crop": "center"}, config)
    assert a == protocol.job_id("v", 10.0, 30.0, {"crop": "center"}, config)       # stable
    assert a != protocol.job_id("v", 10, 30, {"crop": "track"}, config)            # opts matter
    assert a != protocol.job_id("v", 10, 31, {"crop": "center"}, config)


def test_edit_choices_do_not_travel_or_change_the_job_hash():
    """The plugins a job chose to suggest edits (clips.edit, a list of plugins,
    not an edit list) stay on the main PC, like Rate & understand's: a render
    PC gets the same config, and the job hash is the same, with or without them."""
    plain = {"clips": {"vertical": True, "captions": True}, "tracking": {}, "video": {}}
    with_edit = {**plain, "clips": {**plain["clips"], "edit": [
        {"id": "example-dev/quarkbloom-trimmer", "settings": {"mute_words": "round one"}}]}}
    assert protocol.render_config(with_edit) == protocol.render_config(plain)
    assert "edit" not in protocol.render_config(with_edit)["clips"]
    assert "edit" in with_edit["clips"]  # the job's own config is left as it was
    assert protocol.job_id("v", 10, 30, None, with_edit) == protocol.job_id("v", 10, 30, None, plain)
    # A clip's own edit list is a render option, and still makes it a different job.
    opts = {"edit": {"keep": [[0, 5], [8, 20]]}}
    assert protocol.job_id("v", 10, 30, opts, with_edit) != protocol.job_id("v", 10, 30, None, with_edit)


def test_what_needs_the_face_tracking_models():
    assert protocol.needs_framing({"clips": {}}, None)
    assert not protocol.needs_framing({"clips": {"vertical_live": True}}, None)     # whole frame kept
    assert not protocol.needs_framing({"clips": {}}, {"profile": "16:9"})           # longform
    assert not protocol.needs_framing({"clips": {}}, {"vertical_live": True})


def test_the_expected_length_follows_the_clips_edit():
    """The main PC checks a returned clip against how long it plays: its
    window, or what the clip's saved edit keeps of it at its speed (D33)."""
    assert protocol.expected_seconds({"start": 10, "end": 40}) == 30.0
    assert protocol.expected_seconds({"start": 10, "end": 40, "render_opts": None}) == 30.0
    edit = {"keep": [[0, 5], [10, 20]], "speed": 1.5}
    assert protocol.expected_seconds({"start": 10, "end": 40, "render_opts": {"edit": edit}}) == 10.0
    for broken in ({"keep": "nope"}, ["not", "an", "edit"], {"keep": [[0, 5]], "hook": {"text": "x", "seconds": "y"}}):
        assert protocol.expected_seconds({"start": 10, "end": 40, "render_opts": {"edit": broken}}) == 30.0, broken


def test_a_worker_is_matched_on_what_it_can_actually_do():
    ok = {"protocol": protocol.PROTOCOL, "encoders": ["cpu"], "framing": True}
    assert protocol.compatible(True, ok) == (True, "")
    assert not protocol.compatible(True, {**ok, "framing": False})[0]
    assert protocol.compatible(False, {**ok, "framing": False})[0]
    assert protocol.compatible(True, {**ok, "protocol": 99}) == (False, "worker update required")


def test_settings_default_off_and_never_show_the_worker_secret(tmp_path):
    s = settings.load(tmp_path)
    assert s["enabled"] is False and s["mode"] == "local"
    settings.update(tmp_path, enabled=True, this_pc={"worker_id": "w1", "secret": "hush"})
    s = settings.load(tmp_path)
    assert s["enabled"] and s["this_pc"]["secret"] == "hush" and s["this_pc"]["max_jobs"] == 1
    public = settings.public(s)
    assert "secret" not in public["this_pc"] and public["this_pc"]["paired"] is True
    assert settings.valid_mode("auto") and settings.valid_mode("worker:abc")
    assert not settings.valid_mode("worker:") and not settings.valid_mode("cloud")


def test_off_means_the_pipeline_renders_here_as_always(tmp_path):
    from remote_render.dispatch import renderer_for

    assert renderer_for({"paths": {"data_dir": str(tmp_path)}}) is None
    settings.update(tmp_path, enabled=True)                       # on, but "This computer"
    assert renderer_for({"paths": {"data_dir": str(tmp_path)}}) is None
    assert not (tmp_path / "remote_render").exists()              # nothing was even started


# ---- the queue ---------------------------------------------------------------------------

CAPS = {"protocol": protocol.PROTOCOL, "encoders": ["cpu"], "framing": True, "name": "Render PC"}


def _paired(q, caps=CAPS):
    return q.redeem(q.new_pairing_code(), "Render PC", caps)


def _job(q, tmp_path, jid="a" * 32, video="v1", target="", framing=True, start=10.0, end=30.0):
    p = tmp_path / f"{jid}.mp4"
    p.write_bytes(b"piece")
    q.submit({"id": jid, "video_id": video, "label": f"{int(start)}s", "target": target, "needs_framing": framing,
              "spec": {"start": start, "end": end}, "piece_path": str(p), "piece_sha": "x", "piece_size": 5})
    return jid


def test_a_pairing_code_works_once_and_guessing_is_capped(tmp_path):
    from remote_render.queue import RenderQueue

    q = RenderQueue(tmp_path)
    code = q.new_pairing_code()
    assert len(code) == 9 and code[4] == "-"
    wid, secret = q.redeem(code.lower(), "PC", CAPS)                  # case and dash don't matter
    assert q.authenticate(wid, secret) and not q.authenticate(wid, "nope")
    assert q.redeem(code, "PC", CAPS) is None                         # used up
    live = q.new_pairing_code()
    for _ in range(5):
        assert q.redeem("WRONG-CODE", "PC", CAPS) is None
    assert q.redeem(live, "PC", CAPS) is None                         # five wrong tries void it


def test_an_expired_code_is_refused(tmp_path, monkeypatch):
    from remote_render import queue as queue_mod

    q = queue_mod.RenderQueue(tmp_path)
    code = q.new_pairing_code()
    real = time.time
    monkeypatch.setattr(queue_mod.time, "time", lambda: real() + 601)
    assert q.redeem(code, "PC", CAPS) is None


def test_jobs_go_only_where_they_can_be_rendered(tmp_path):
    from remote_render.queue import RenderQueue

    q = RenderQueue(tmp_path)
    tracker, _s1 = _paired(q)
    plain, _s2 = _paired(q, {**CAPS, "framing": False})
    _job(q, tmp_path, "a" * 32, framing=True)
    assert q.claim(plain) is None                                     # can't track faces
    got = q.claim(tracker)
    assert got["id"] == "a" * 32 and got["attempt"] == 1
    assert q.claim(tracker) is None                                   # one at a time by default
    _job(q, tmp_path, "b" * 32, framing=False, target=tracker)
    assert q.claim(plain) is None                                     # meant for the other one
    q.set_held(tracker, True)
    q.complete("a" * 32, tracker, tmp_path / "r.mp4", "")
    assert q.claim(tracker) is None                                   # "Stop accepting jobs"
    q.set_held(tracker, False)
    assert q.claim(tracker)["id"] == "b" * 32


def test_a_result_counts_once_and_failures_retry_then_stop(tmp_path):
    from remote_render.queue import MAX_ATTEMPTS, RenderQueue

    q = RenderQueue(tmp_path)
    wid, _s = _paired(q)
    jid = _job(q, tmp_path)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        assert q.claim(wid)["attempt"] == attempt
        assert q.fail(jid, wid, "ffmpeg exit 1", "log") == ("queued" if attempt < MAX_ATTEMPTS else "failed")
    assert q.job(jid)["error"] == "ffmpeg exit 1"
    assert q.retry(jid) and q.claim(wid)["id"] == jid
    assert q.complete(jid, wid, tmp_path / "r.mp4", "{}")
    assert not q.complete(jid, wid, tmp_path / "r2.mp4", "{}")        # a duplicate is ignored
    assert q.submit({"id": jid, "video_id": "v1", "spec": {}, "piece_path": "p", "piece_sha": "x",
                     "piece_size": 1}) == "completed"                  # the same job, recognised


def test_a_silent_worker_gives_its_jobs_back(tmp_path, monkeypatch):
    from remote_render import queue as queue_mod

    q = queue_mod.RenderQueue(tmp_path)
    wid, _s = _paired(q)
    jid = _job(q, tmp_path)
    q.claim(wid)
    q.heartbeat(wid, {}, False, 1, [{"id": jid, "stage": "rendering", "progress": 0.4}])
    assert q.job(jid)["stage"] == "rendering"
    real = time.time
    monkeypatch.setattr(queue_mod.time, "time", lambda: real() + 60)
    assert q.sweep() == 1
    assert q.job(jid)["state"] == "queued" and q.workers()[0]["status"] == "offline"


def test_cancel_and_render_locally_reach_the_worker_at_its_heartbeat(tmp_path):
    from remote_render.queue import RenderQueue

    q = RenderQueue(tmp_path)
    wid, _s = _paired(q)
    jid = _job(q, tmp_path, video="v1")
    q.claim(wid)
    assert q.cancel_video("v1") == 1
    assert q.heartbeat(wid, {}, False, 1, [{"id": jid}])["stop"] == [jid]
    other = _job(q, tmp_path, "c" * 32, video="v2")
    assert q.release_to_local("v2") == 1 and q.job(other)["state"] == "local"
    assert q.remove_worker(wid) and not q.authenticate(wid, _s)


# ---- the gateway -------------------------------------------------------------------------


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from remote_render import gateway as gw_mod
    from remote_render import piece
    from remote_render.queue import RenderQueue

    q = RenderQueue(tmp_path)
    client = TestClient(gw_mod.create_app(q, tmp_path))
    monkeypatch.setattr(piece, "duration", lambda _p: 20.0)
    return q, client, tmp_path


def _auth(wid, secret):
    return {"X-Worker-Id": wid, "Authorization": f"Bearer {secret}"}


def test_pairing_and_every_route_after_it_need_the_workers_credential(gateway):
    q, client, _tmp = gateway
    assert client.post("/v1/pair", json={"code": "NOPE-NOPE", "caps": CAPS}).status_code == 403
    assert client.post("/v1/pair", json={"code": q.new_pairing_code(),
                                         "caps": {**CAPS, "protocol": 99}}).status_code == 409
    r = client.post("/v1/pair", json={"code": q.new_pairing_code(), "caps": CAPS, "name": "PC"})
    assert r.status_code == 200
    wid, secret = r.json()["worker_id"], r.json()["secret"]
    assert client.post("/v1/claim").status_code == 401
    assert client.post("/v1/claim", headers=_auth(wid, "wrong")).status_code == 401
    assert client.post("/v1/claim", headers=_auth(wid, secret)).status_code == 204     # nothing queued


def test_a_job_its_piece_resumes_and_its_result_is_checked(gateway):
    from remote_render import piece

    q, client, tmp = gateway
    wid, secret = _paired(q)
    h = _auth(wid, secret)
    jid = "d" * 32
    p = tmp / "piece.mp4"
    p.write_bytes(bytes(range(256)) * 40)
    q.submit({"id": jid, "video_id": "v", "spec": {"start": 0, "end": 20}, "piece_path": str(p),
              "piece_sha": piece.sha256(p), "piece_size": p.stat().st_size, "assets": {"logo.png": True}})
    job = client.post("/v1/claim", headers=h).json()
    assert job["id"] == jid and job["spec"]["end"] == 20
    part = client.get(f"/v1/jobs/{jid}/piece", headers={**h, "Range": "bytes=100-"})
    assert part.status_code == 206 and part.content == p.read_bytes()[100:]          # resumes
    assert client.get(f"/v1/jobs/{jid}/assets/..%2Fstate.db", headers=h).status_code == 404
    assert client.get(f"/v1/jobs/{jid}/assets/other.png", headers=h).status_code == 404

    out = b"clip-bytes" * 1000
    assert client.put(f"/v1/jobs/{jid}/result", params={"offset": 0}, content=out[:4000], headers=h).json() \
        == {"received": 4000}
    wrong = client.put(f"/v1/jobs/{jid}/result", params={"offset": 0}, content=out[4000:], headers=h)
    assert wrong.status_code == 409 and json.loads(wrong.json()["detail"]) == {"received": 4000}
    client.put(f"/v1/jobs/{jid}/result", params={"offset": 4000}, content=out[4000:], headers=h)
    bad = client.post(f"/v1/jobs/{jid}/complete", json={"sha256": "0" * 64, "size": len(out)}, headers=h).json()
    assert bad["ok"] is False and bad["state"] == "queued"                           # damaged: retried
    q.claim(wid)
    client.put(f"/v1/jobs/{jid}/result", params={"offset": 0}, content=out, headers=h)
    import hashlib

    good = client.post(f"/v1/jobs/{jid}/complete", headers=h,
                       json={"sha256": hashlib.sha256(out).hexdigest(), "size": len(out), "render_opts": "{}"})
    assert good.json() == {"ok": True}
    assert Path(q.job(jid)["result_path"]).read_bytes() == out
    assert client.post(f"/v1/jobs/{jid}/progress", json={"stage": "x"}, headers=h).status_code == 409


# ---- the pipeline's side -------------------------------------------------------------------


class _FakeGateway:
    def __init__(self, tmp_path):
        from remote_render.queue import RenderQueue

        self.queue = RenderQueue(tmp_path)


@pytest.fixture
def dispatching(tmp_path, monkeypatch):
    from remote_render import dispatch, piece

    def fake_cut(_src, start, end, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"piece")
        return start - 3.0

    monkeypatch.setattr(piece, "cut", fake_cut)
    monkeypatch.setattr(piece, "sha256", lambda _p: "x")
    return dispatch, _FakeGateway(tmp_path), tmp_path


def _items():
    from core.models import ClipCandidate

    return [(ClipCandidate(start=10.0, end=30.0, score=80), "m1"), (ClipCandidate(start=50.0, end=70.0, score=80), "m2")]


CONFIG = {"clips": {}, "tracking": {}, "video": {}}


def _local(calls):
    def render(candidate):
        calls.append(candidate.start)
        return Path(f"local_{int(candidate.start)}.mp4"), ""
    return render


def test_automatic_with_no_worker_renders_here(dispatching):
    dispatch, gw, tmp = dispatching
    calls = []
    rr = dispatch.RemoteRenderer(gw, "auto", tmp)
    got = [(c.start, get()) for c, _m, get in rr.render_all("v", tmp / "s.mp4", _items(), [], tmp / "clips",
                                                               CONFIG, None, "en", 2, local=_local(calls))]
    assert sorted(calls) == [10.0, 50.0] and len(got) == 2


def test_a_worker_renders_and_the_clip_lands_under_its_usual_name(dispatching):
    dispatch, gw, tmp = dispatching
    q = gw.queue
    wid, _s = _paired(q)

    def fake_worker():
        done = 0
        while done < 2:
            job = q.claim(wid)
            if job is None:
                time.sleep(0.05)
                continue
            assert job["spec"]["offset"] == job["spec"]["start"] - 3.0
            assert set(job["spec"]["config"]) == {"clips", "tracking", "video"}
            result = tmp / f"result_{job['id']}.mp4"
            result.write_bytes(b"rendered")
            q.complete(job["id"], wid, result, '{"crop": "track"}')
            done += 1

    t = threading.Thread(target=fake_worker, daemon=True)
    t.start()
    rr = dispatch.RemoteRenderer(gw, "auto", tmp)
    calls = []
    got = {c.start: get() for c, _m, get in rr.render_all("v", tmp / "s.mp4", _items(), [], tmp / "clips",
                                                             CONFIG, None, "en", 2, local=_local(calls))}
    t.join(5)
    assert calls == []
    path, opts = got[10.0]
    assert path.name == "clip_00010-00030.mp4" and path.read_bytes() == b"rendered" and opts == '{"crop": "track"}'


def test_only_on_a_worker_waits_for_it_until_render_locally(dispatching):
    dispatch, gw, tmp = dispatching
    q = gw.queue
    wid, _s = _paired(q)
    q.heartbeat(wid, {}, False, 1, [])
    rr = dispatch.RemoteRenderer(gw, f"worker:{wid}", tmp)
    calls = []

    def press_render_locally():
        time.sleep(0.3)
        q.release_to_local("v")

    threading.Thread(target=press_render_locally, daemon=True).start()
    got = [c.start for c, _m, _get in rr.render_all("v", tmp / "s.mp4", _items(), [], tmp / "clips",
                                                     CONFIG, None, "en", 2, local=_local(calls))]
    assert sorted(calls) == [10.0, 50.0] and sorted(got) == [10.0, 50.0]


def test_a_failed_clip_renders_here_in_automatic_but_not_on_a_chosen_worker(dispatching):
    dispatch, gw, tmp = dispatching
    q = gw.queue
    wid, _s = _paired(q)
    q.heartbeat(wid, {}, False, 1, [])

    def failing_worker():
        for _ in range(20):
            job = q.claim(wid)
            if job is None:
                time.sleep(0.05)
                continue
            q.fail(job["id"], wid, "the render process exited with code 1")

    for mode, expect_local in (("auto", True), (f"worker:{wid}", False)):
        for j in q.jobs_for("v"):
            q.forget(j["id"])
        threading.Thread(target=failing_worker, daemon=True).start()
        calls = []
        outcomes = []
        for _c, _m, get in dispatch.RemoteRenderer(gw, mode, tmp).render_all(
                "v", tmp / "s.mp4", _items()[:1], [], tmp / "clips", CONFIG, None, "en", 1, local=_local(calls)):
            try:
                outcomes.append(get()[0].name)
            except RuntimeError as e:
                outcomes.append(str(e))
        if expect_local:
            assert calls == [10.0]
        else:
            assert calls == [] and "exited with code 1" in outcomes[0]


def _completing(q, wid, tmp, specs: list, count: int):
    """A render PC that renders `count` jobs, noting each one's spec."""
    def work():
        done = 0
        while done < count:
            job = q.claim(wid)
            if job is None:
                time.sleep(0.05)
                continue
            specs.append(job["spec"])
            result = tmp / f"result_{job['id']}.mp4"
            result.write_bytes(b"rendered")
            q.complete(job["id"], wid, result, "")
            done += 1

    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t


def test_each_clip_goes_with_its_own_options(dispatching):
    """opts_for(candidate, meta): each clip's own options, its title card or,
    on a re-run, the creator's saved choices (D33), travel with its job."""
    dispatch, gw, tmp = dispatching
    q = gw.queue
    wid, _s = _paired(q)
    specs: list = []
    t = _completing(q, wid, tmp, specs, 2)

    def opts_for(candidate, meta):
        return {"headline": meta, **({"edit": {"keep": [[0, 8]]}} if candidate.start == 10.0 else {})}

    calls = []
    got = [c.start for c, _m, _get in dispatch.RemoteRenderer(gw, "auto", tmp).render_all(
        "v", tmp / "s.mp4", _items(), [], tmp / "clips", CONFIG, None, "en", 2, local=_local(calls),
        opts_for=opts_for)]
    t.join(5)
    assert calls == [] and sorted(got) == [10.0, 50.0]
    assert sorted((s["start"], s["render_opts"]) for s in specs) == [
        (10.0, {"headline": "m1", "edit": {"keep": [[0, 8]]}}), (50.0, {"headline": "m2"})]


def test_a_clip_with_music_from_this_pc_renders_here(dispatching, capsys):
    """Music added in the editor is a file on this PC, which a render PC
    can't fetch: that clip renders here, whichever render mode is chosen,
    and the others still go out. The log counts only the clips sent."""
    dispatch, gw, tmp = dispatching
    q = gw.queue
    wid, _s = _paired(q)
    q.heartbeat(wid, {}, False, 1, [])
    music = {"edit": {"music": {"path": "music/quarkbloom-theme.mp3", "volume": 0.4}}}
    assert dispatch._needs_this_pc(music)
    assert not dispatch._needs_this_pc({"edit": {"music": {"path": " "}}})
    assert not dispatch._needs_this_pc({"edit": {"keep": [[0, 8]]}}) and not dispatch._needs_this_pc(None)

    def opts_for(candidate, _meta):
        return music if candidate.start == 10.0 else {"crop": "center"}

    for mode, where in (("auto", "a render worker"), (f"worker:{wid}", "Render PC")):
        video = f"v-{mode[:4]}"
        specs: list = []
        t = _completing(q, wid, tmp, specs, 1)
        calls = []
        capsys.readouterr()
        got = [c.start for c, _m, _get in dispatch.RemoteRenderer(gw, mode, tmp).render_all(
            video, tmp / "s.mp4", _items(), [], tmp / "clips", CONFIG, None, "en", 2, local=_local(calls),
            opts_for=opts_for)]
        t.join(5)
        assert calls == [10.0] and sorted(got) == [10.0, 50.0], mode
        assert [j["label"] for j in q.jobs_for(video)] == ["50s-70s"], mode
        assert [s["start"] for s in specs] == [50.0], mode
        out = capsys.readouterr().out
        assert f"Remote rendering: sending 1 clip(s) to {where}\n" in out, mode
        assert "10s-30s renders here (its music is a file on this computer)" in out, mode
    # Every clip with music: nothing goes out, and the log doesn't say it does.
    calls = []
    got = [c.start for c, _m, _get in dispatch.RemoteRenderer(gw, f"worker:{wid}", tmp).render_all(
        "v-all", tmp / "s.mp4", _items(), [], tmp / "clips", CONFIG, None, "en", 2, local=_local(calls),
        opts_for=lambda _c, _m: music)]
    assert sorted(calls) == [10.0, 50.0] and sorted(got) == [10.0, 50.0]
    assert q.jobs_for("v-all") == [] and "sending" not in capsys.readouterr().out


# ---- the worker's side --------------------------------------------------------------------


def test_cancel_kills_the_render_and_everything_it_started():
    psutil = pytest.importorskip("psutil")
    import subprocess
    import sys

    from remote_render.worker import _kill_tree

    code = ("import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", code])
    deadline = time.time() + 10
    while time.time() < deadline and not psutil.Process(proc.pid).children():
        time.sleep(0.1)
    kids = psutil.Process(proc.pid).children(recursive=True)
    assert kids
    _kill_tree(proc)
    proc.wait(10)
    gone, alive = psutil.wait_procs(kids, timeout=10)
    assert not alive


def test_a_piece_shows_the_same_frames_as_the_source(tmp_path):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    import shutil
    import subprocess

    from core.binaries import ffmpeg

    try:
        ff = ffmpeg()
    except Exception:
        pytest.skip("no FFmpeg")
    if not shutil.which(ff) and not Path(ff).exists():
        pytest.skip("no FFmpeg")
    from remote_render import piece

    src = tmp_path / "src.mp4"
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=40",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=40", "-c:v", "libx264", "-g", "45",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)

    def frame(path, t):
        raw = subprocess.run([ff, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                              "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
        return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR).astype(int)

    for start in (1.0, 17.3):
        dest = tmp_path / f"p{int(start)}.mp4"
        off = piece.cut(src, start, start + 10, dest)
        assert dest.stat().st_size < src.stat().st_size
        for t in (start, start + 4.7):
            assert np.abs(frame(src, t) - frame(dest, t - off)).mean() == 0, (start, t, off)
