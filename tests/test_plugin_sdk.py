"""The plugin SDK (sdk/python/clipskitty_sdk): the contract and its helpers.

Standard library only, like the SDK itself, so these run in CI's bare
environment. The SDK is put on the path the way Clips Kitty puts it on a
plugin's: as a folder, not an installed package.
"""

import io
import json
import sys
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parent.parent / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import ContractError, check_job, check_result, contract, host, read_job, run  # noqa: E402


def _job(tmp_path, **extra):
    data = {"plugin": {"id": "example-dev/example", "version": "1.0.0"},
            "video": {"path": str(tmp_path / "v.mp4"), "id": "v1", "title": "T", "duration": 120.0},
            "settings": {"k": 1}, "limits": {"max_clips": 3, "min_duration": 10, "max_duration": 60},
            **extra}
    host.write_job(tmp_path / "job", data, {"language": "en", "segments": [
        {"start": 0, "end": 2, "text": "hi", "words": None}]})
    return tmp_path / "job"


# ---- the files ------------------------------------------------------------------


def test_a_written_job_reads_back(tmp_path):
    out = io.StringIO()
    job = read_job(_job(tmp_path), out=out)
    assert job.plugin_id == "example-dev/example" and job.plugin_version == "1.0.0"
    assert job.video.path == tmp_path / "v.mp4" and job.video.duration == 120.0
    assert job.transcript.language == "en" and job.transcript.segments()[0]["text"] == "hi"
    assert job.limits.max_clips == 3 and job.settings == {"k": 1}
    assert job.output_dir.is_dir()
    assert job.models == {} and job.tools.ffmpeg is None


def test_job_problems_are_all_listed():
    problems = check_job({"plugin_api": 9, "video": {"duration": "long"}, "settings": []})
    assert any("plugin_api" in p for p in problems)
    assert any("plugin.id" in p for p in problems)
    assert any("video.path" in p for p in problems)
    assert any("settings must be an object" in p for p in problems)
    assert any("output_dir" in p for p in problems)
    assert check_job("not a mapping") == ["job.json must be a JSON object"]


@pytest.mark.parametrize("result, fragment", [
    ({"plugin_api": 1, "ranges": [{"start": 5, "end": 5}]}, "0 <= start < end"),
    ({"plugin_api": 1, "ranges": [{"start": -1, "end": 5}]}, "0 <= start < end"),
    ({"plugin_api": 1, "ranges": [{"start": "a", "end": 5}]}, "numbers of seconds"),
    ({"plugin_api": 1, "ranges": [{"start": 1, "end": 5, "score": 101}]}, "0 to 100"),
    ({"plugin_api": 1, "ranges": [{"start": 1, "end": 5, "label": "x" * 65}]}, "label"),
    ({"plugin_api": 1, "ranges": "all of them"}, "must be a list"),
    ({"plugin_api": 2, "ranges": []}, "plugin_api"),
    ({"plugin_api": 1, "ranges": [], "clips": [{"path": "a.mp4"}]}, "planned"),
    ({"plugin_api": 1, "ranges": [{"start": float("nan"), "end": 5}]}, "numbers of seconds"),
])
def test_bad_results_are_refused_with_a_reason(result, fragment):
    problems = check_result(result)
    assert problems and any(fragment in p for p in problems), problems


def test_a_good_result_passes():
    assert check_result({"plugin_api": 1, "ranges": [
        {"start": 0, "end": 12.5, "score": 80, "label": "goal", "title": "t", "reason": "r"},
        {"start": 20, "end": 30}], "notes": "fine"}) == []


# ---- the lines ------------------------------------------------------------------


def test_progress_lines_are_clamped_and_anything_else_is_a_log_line():
    assert json.loads(contract.progress_line(1.7, "x")) == {"type": "progress", "fraction": 1.0, "message": "x"}
    assert contract.parse_line('{"type": "progress", "fraction": -3}\n')["fraction"] == 0.0
    assert contract.parse_line('{"type": "progress", "fraction": "half"}')["fraction"] is None
    assert contract.parse_line("plain text\n") == {"type": "log", "message": "plain text"}
    assert contract.parse_line('{"type": "shout", "message": "hi"}')["type"] == "log"
    assert contract.parse_line('[1, 2]')["type"] == "log"
    assert contract.parse_line(contract.error_line("broke")) == {"type": "error", "message": "broke"}


# ---- the Job --------------------------------------------------------------------


def test_progress_and_log_write_one_json_line_each(tmp_path):
    out = io.StringIO()
    job = read_job(_job(tmp_path), out=out)
    job.progress(0.5, "half way")
    job.log("a note")
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert lines == [{"type": "progress", "fraction": 0.5, "message": "half way"},
                     {"type": "log", "message": "a note"}]


def test_ranges_are_checked_as_they_are_added(tmp_path):
    job = read_job(_job(tmp_path), out=io.StringIO())
    with pytest.raises(ContractError, match="0 <= start < end"):
        job.add_range(10, 5)
    with pytest.raises(ContractError, match="0 to 100"):
        job.add_range(0, 5, score=300)


def test_a_range_outside_the_jobs_lengths_is_logged_not_refused(tmp_path):
    out = io.StringIO()
    job = read_job(_job(tmp_path), out=out)
    job.add_range(0, 90)
    job.add_range(0, 4)
    assert "longer than this job's 60s limit" in out.getvalue()
    assert "shorter than this job's 10s minimum" in out.getvalue()
    assert len(job.ranges) == 2


def test_finish_writes_the_result_atomically(tmp_path):
    job = read_job(_job(tmp_path), out=io.StringIO())
    job.add_range(1, 20, score=70, label="goal", title="Goal", reason="net bulges")
    path = job.finish(notes="one goal")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"plugin_api": 1, "notes": "one goal", "ranges": [
        {"start": 1.0, "end": 20.0, "score": 70.0, "label": "goal", "title": "Goal", "reason": "net bulges"}]}
    assert not list(path.parent.glob("*.partial"))


def test_fail_says_why_and_exits(tmp_path):
    out = io.StringIO()
    job = read_job(_job(tmp_path), out=out)
    with pytest.raises(SystemExit) as stop:
        job.fail("no kill feed in this video")
    assert stop.value.code == 1
    assert json.loads(out.getvalue()) == {"type": "error", "message": "no kill feed in this video"}


def test_secrets_come_from_the_environment(tmp_path, monkeypatch):
    job = read_job(_job(tmp_path), out=io.StringIO())
    monkeypatch.setenv("CLIPSKITTY_SECRET_API_KEY", "k")
    assert job.secret("api_key") == job.secret("api-key") == "k"
    assert job.secret("other") is None


def test_the_job_folder_can_come_from_the_environment(tmp_path, monkeypatch):
    folder = _job(tmp_path)
    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.setenv("CLIPSKITTY_JOB", str(folder))
    assert read_job().plugin_id == "example-dev/example"
    monkeypatch.delenv("CLIPSKITTY_JOB")
    with pytest.raises(ContractError, match="no job folder"):
        read_job()


def test_run_finishes_the_job_and_turns_errors_into_words(tmp_path, monkeypatch, capsys):
    folder = _job(tmp_path)
    run(lambda job: job.add_range(0, 15), folder)
    assert json.loads((folder / "result.json").read_text())["ranges"][0]["end"] == 15.0

    def broken(job):
        raise ValueError("the model file is missing")

    with pytest.raises(SystemExit):
        run(broken, folder)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[-1] == {"type": "error", "message": "the model file is missing"}
    assert any("Traceback" in line["message"] for line in lines if line["type"] == "log")


# ---- the host side --------------------------------------------------------------


def test_read_result_fits_ranges_to_the_video(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "result.json").write_text(json.dumps({"plugin_api": 1, "ranges": [
        {"start": 0, "end": 10}, {"start": 50, "end": 200, "score": 60}, {"start": 119.5, "end": 130},
        {"start": 20, "end": 40, "score": 90}]}))
    result = host.read_result(folder, duration=120, max_clips=2)
    assert [(r["start"], r["end"]) for r in result["ranges"]] == [(20, 40), (50, 120)]


def test_read_result_refuses_what_it_cannot_use(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    with pytest.raises(ContractError, match=r"without writing result\.json"):
        host.read_result(folder)
    (folder / "result.json").write_text("{not json")
    with pytest.raises(ContractError, match="not valid JSON"):
        host.read_result(folder)


def test_the_plugin_environment_leaves_out_settings_and_credentials(tmp_path):
    env = host.plugin_env({"PATH": "/bin", "CLIPS_STUDIO_X": "1", "CLIPSKITTY_SECRET_OLD": "s",
                           "GITHUB_TOKEN": "t", "MY_PASSWORD": "p", "CUDA_PATH": "/cuda"},
                          job_folder=tmp_path, secrets={"api_key": "k", "empty": ""})
    assert env["PATH"] == "/bin" and env["CUDA_PATH"] == "/cuda"
    assert "CLIPS_STUDIO_X" not in env and "GITHUB_TOKEN" not in env and "MY_PASSWORD" not in env
    assert "CLIPSKITTY_SECRET_OLD" not in env and env["CLIPSKITTY_SECRET_API_KEY"] == "k"
    assert "CLIPSKITTY_SECRET_EMPTY" not in env
    assert env["CLIPSKITTY_JOB"] == str(tmp_path) and env["PYTHONPATH"] == str(SDK)


def test_the_python_placeholder_is_replaced():
    assert host.resolve_command(["{python}", "src/main.py", "{python}x"], "/usr/bin/python3") == \
        ["/usr/bin/python3", "src/main.py", "{python}x"]
    assert host.find_python("C:/Python312/python.exe") == "C:/Python312/python.exe"


# ---- the job a manifest's permissions allow ----------------------------------------


def test_build_job_hands_over_only_what_the_permissions_cover(tmp_path):
    manifest = {"id": "example-dev/example", "version": "1.0.0", "permissions": [],
                "settings": {"level": {"type": "integer", "default": 3}, "key": {"type": "secret"}}}
    kwargs = {"video": {"path": "v.mp4", "id": "v"}, "transcript": {"language": "en", "segments": []},
                  "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "ollama": {"host": "http://localhost:11434", "model": ""},
                  "output_dir": tmp_path / "out"}
    job, transcript = host.build_job(manifest, **kwargs)
    assert "video" not in job and job["tools"] == {} and transcript is None
    assert job["settings"] == {"level": 3}  # defaults, and never a secret
    assert not check_job(job)
    manifest["permissions"] = ["video.read", "transcript.read", "ffmpeg", "ollama"]
    job, transcript = host.build_job(manifest, settings={"level": 5}, **kwargs)
    assert job["video"]["id"] == "v" and transcript == {"language": "en", "segments": []}
    assert set(job["tools"]) == {"ffmpeg", "ffprobe", "ollama"} and job["settings"] == {"level": 5}
    with pytest.raises(ValueError, match="no setting called 'colour'"):
        host.build_job(manifest, settings={"colour": "red"}, **kwargs)
    with pytest.raises(ValueError, match="'key' is a secret"):
        host.build_job(manifest, settings={"key": "abc"}, **kwargs)


# ---- python -m clipskitty_sdk run ----------------------------------------------------


ECHO = Path(__file__).resolve().parent / "fixtures" / "plugins" / "echo"


def test_the_dev_runner_runs_a_plugin_the_way_the_app_does(tmp_path, capsys):
    pytest.importorskip("yaml")
    from clipskitty_sdk.__main__ import main

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not decoded by the echo plugin")
    transcript = tmp_path / "t.json"
    transcript.write_text(json.dumps({"language": "en", "segments": [{"start": 0, "end": 3, "text": "go"}]}))
    code = main(["run", str(ECHO), "--video", str(video), "--transcript", str(transcript),
                 "--set", "ranges=5-20:60,30-55:95", "--secret", "api_key=abc", "--job-dir", str(tmp_path / "job"),
                 "--ffprobe", str(tmp_path / "no-ffprobe")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "2 moment(s)" in out and out.index("30.0s") < out.index("5.0s")  # best first
    seen = json.loads((tmp_path / "job" / "out" / "seen.json").read_text())
    assert seen["secret"] == "abc" and "abc" not in (tmp_path / "job" / "job.json").read_text()
    assert seen["transcript"][0]["text"] == "go"
    assert seen["job"]["limits"] == {"max_clips": None, "min_duration": 10.0, "max_duration": 60.0}


@pytest.mark.parametrize("args, code, words", [
    (["--set", "mode=fail"], 1, "the kill feed could not be read"),
    (["--set", "mode=bad"], 1, "would refuse this answer"),
    (["--set", "colour=red"], 2, "no setting called 'colour'"),
])
def test_the_dev_runner_says_why_a_run_would_fail_in_the_app(tmp_path, capsys, args, code, words):
    pytest.importorskip("yaml")
    from clipskitty_sdk.__main__ import main

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    assert main(["run", str(ECHO), "--video", str(video), "--job-dir", str(tmp_path / "job"), *args]) == code
    assert words in capsys.readouterr().err


def test_the_dev_runner_needs_a_manifest(tmp_path, capsys):
    pytest.importorskip("yaml")
    from clipskitty_sdk.__main__ import main

    (tmp_path / "clip.mp4").write_bytes(b"x")
    assert main(["run", str(tmp_path), "--video", str(tmp_path / "clip.mp4")]) == 2
    assert "no clipskitty.yaml" in capsys.readouterr().err


# ---- the local API client -----------------------------------------------------------


@pytest.fixture
def fake_api():
    """A stand-in for the app's API on a free local port, recording each request."""
    import http.server
    import threading

    seen = []
    replies = {"/health": (200, {"ok": True, "app_version": "2.0.0", "api_version": 1})}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            seen.append((self.command, self.path, body))
            status, reply = replies.get(self.path.split("?")[0], (404, {"detail": "Not Found"}))
            data = json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PATCH = do_DELETE = _answer

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", replies, seen
    server.shutdown()
    server.server_close()


def test_the_client_calls_the_stable_routes(fake_api):
    from clipskitty_sdk.client import LocalAPI

    url, replies, seen = fake_api
    api = LocalAPI(url)
    assert api.health()["api_version"] == 1
    replies["/jobs"] = (200, {"job_id": 7})
    assert api.add_job("https://www.youtube.com/watch?v=aB3dEfGhIjK", max_clips=3) == {"job_id": 7}
    assert seen[-1] == ("POST", "/jobs", {"url": "https://www.youtube.com/watch?v=aB3dEfGhIjK", "max_clips": 3})
    replies["/videos/a%2Fb/clips"] = (200, [])
    assert api.clips("a/b") == [] and seen[-1][1] == "/videos/a%2Fb/clips"


def test_the_client_turns_refusals_into_errors_with_the_apis_words(fake_api):
    from clipskitty_sdk.client import APIError, LocalAPI

    url, replies, _ = fake_api
    replies["/jobs"] = (400, {"detail": "max_clips must be between 1 and 50"})
    with pytest.raises(APIError) as e:
        LocalAPI(url).add_job("https://www.youtube.com/watch?v=aB3dEfGhIjK", max_clips=99)
    assert e.value.status == 400 and e.value.detail == "max_clips must be between 1 and 50"
    replies["/health"] = (200, {"ok": True, "api_version": 2})
    with pytest.raises(APIError, match="speaks API 2"):
        LocalAPI(url).health()


def test_the_client_says_when_the_app_is_not_running():
    import socket

    from clipskitty_sdk.client import APIError, LocalAPI

    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(APIError) as e:
        LocalAPI(f"http://127.0.0.1:{port}", timeout=2).health()
    assert e.value.status == 0 and "not answering" in str(e.value)


def test_a_program_command_is_resolved_inside_the_plugin(tmp_path):
    assert host.resolve_command(["bin/detect", "--fast"], "py", tmp_path) == [str(tmp_path / "bin/detect"), "--fast"]
    assert host.resolve_command(["{python}", "src/main.py"], "py", tmp_path) == ["py", "src/main.py"]
