"""`python -m clipskitty_sdk install`: a plugin you're writing, put into the
Clips Kitty running on this PC (clipskitty_sdk.installer).

The tests talk to a stand-in for Clips Kitty's API on 127.0.0.1 that answers
the way plugins/api.py does: /health, /plugins, and the plan and install
routes, which need the session header and otherwise answer 403 naming the
session file. Like every tests/test_plugin_sdk_*.py file, this needs only
pytest and PyYAML and imports nothing from Clips Kitty's engine, so it also
runs in CI's SDK (Windows) job. tests/test_plugin_api.py checks the same
"nothing new" rule against the app's real plans.
"""

import importlib
import inspect
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import _loopback, installer, scaffold  # noqa: E402
from clipskitty_sdk.__main__ import main  # noqa: E402
from clipskitty_sdk.client import LocalAPI  # noqa: E402

pytest.importorskip("yaml")  # install checks clipskitty.yaml first

SECRET = "test-session-secret-0123456789"
PLUGIN_ID = "your-github-name/quarkbloom-bursts"
APP_TEXT = ("Install Quarkbloom Bursts 0.1.0?\nyour-github-name/quarkbloom-bursts · the folder ... · MIT\n"
            "Not listed · Clips Kitty has not checked this\n\nIt will\n  Reads its transcript (Clips Kitty hands "
            "this over)")
NOTHING_NEW = {"from": "0.1.0", "direction": "reinstall", "added_permissions": [], "removed_permissions": [],
               "added_hosts": [], "removed_hosts": [], "added_data_warnings": [], "execution_changed": False,
               "added_steps": []}


def _plan(**changes) -> dict:
    """A plan as Clips Kitty's POST /plugins/plan answers it (plugins/manager.py plan)."""
    plan = {"plan_id": "0123456789abcdef", "ok": True, "errors": [], "warnings": [], "technical": [],
            "plugin": {"id": PLUGIN_ID, "name": "Quarkbloom Bursts", "version": "0.1.0"},
            "source": {"kind": "folder"}, "source_text": "the folder ...",
            "details": {"permissions": [{"id": "transcript.read", "label": "Reads its transcript",
                                         "enforcement": "Clips Kitty hands this over"}],
                        "data_warnings": []},
            "update": None, "text": APP_TEXT}
    plan.update(changes)
    return plan


class FakeApp:
    """Clips Kitty's API on 127.0.0.1, as far as install uses it. It records
    every request, with its headers. `secrets` are the session secrets it
    accepts; `session_path` is the file its 403 answer names; `plans` are
    the answers to POST /plugins/plan, in turn (the last one repeats);
    `redirects` maps a path to the address its 302 answer sends on to."""

    def __init__(self, *, version="2.1.0", plugins=True, secrets=(SECRET,), session_path="", plans=None,
                 redirects=None):
        self.version, self.plugins, self.secrets = version, plugins, set(secrets)
        self.session_path = str(session_path)
        self.plans = list(plans or [_plan()])
        self.redirects = dict(redirects or {})
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.loads(raw) if raw else None
                outer.requests.append({"method": self.command, "path": self.path, "body": body,
                                       "secret": self.headers.get("X-Clips-Kitty-Session")})
                status, reply = outer.answer(self.command, self.path, body, self.headers)
                data = json.dumps(reply).encode("utf-8")
                self.send_response(status)
                if outer.redirects.get(self.path):
                    self.send_header("Location", outer.redirects[self.path])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _handle

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, method, path, body, headers):
        if self.redirects.get(path):
            return 302, None
        if (method, path) == ("GET", "/health"):
            return 200, {"ok": True, "app_version": self.version, "api_version": 1}
        if (method, path) == ("GET", "/plugins"):
            return (200, {"plugins": [], "builtin": []}) if self.plugins else (404, {"detail": "Not Found"})
        if method == "POST" and path in ("/plugins/plan", "/plugins/install"):
            if headers.get("X-Clips-Kitty-Session") not in self.secrets:
                return 403, {"detail": "This needs the X-Clips-Kitty-Session header. The desktop app sends it; a "
                                       f"script finds it in {self.session_path}."}
            if path == "/plugins/plan":
                return 200, self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
            return 200, {"id": PLUGIN_ID, "version": "0.1.0", "enabled": True}
        return 404, {"detail": "Not Found"}

    def calls(self, with_secret=None):
        """(method, path) of each request; with_secret=True only those that
        carried the header, False only those that didn't."""
        return [(r["method"], r["path"]) for r in self.requests
                if with_secret is None or bool(r["secret"]) == with_secret]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _session_file(folder: Path, secret: str = SECRET) -> Path:
    path = folder / "plugins" / "session.secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret + "\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _no_installed_app(tmp_path, monkeypatch):
    """No installed Clips Kitty on the test's PC: LOCALAPPDATA is an empty folder."""
    empty = tmp_path / "localappdata"
    empty.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(empty))
    return empty


@pytest.fixture
def apps():
    made = []

    def make(**kwargs):
        app = FakeApp(**kwargs)
        made.append(app)
        return app

    yield make
    for app in made:
        app.close()


@pytest.fixture
def data_dir(tmp_path):
    """Clips Kitty's data folder, with the session file it writes for scripts."""
    folder = tmp_path / "Clips Studio" / "data"
    _session_file(folder)
    return folder


@pytest.fixture
def app(apps, data_dir):
    return apps(session_path=data_dir / "plugins" / "session.secret")


@pytest.fixture
def plugin(tmp_path):
    """A plugin made from the transcript template, as a developer would."""
    folder = tmp_path / "work" / "quarkbloom-bursts"
    scaffold.make(folder, "transcript", publisher="your-github-name")
    return folder


def _install(capsys, monkeypatch, *args, answers=()):
    """Run the command; returns (exit code, stdout, stderr, the questions asked)."""
    answers, asked = list(answers), []

    def answer(question):
        asked.append(question)
        if not answers:
            raise EOFError
        return answers.pop(0)

    monkeypatch.setattr("builtins.input", answer)
    code = main(["install", *map(str, args)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err, asked


def _closed_port() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


# ---- this PC, a running Clips Kitty, one that can install plugins ---------------------


def test_install_refuses_an_api_off_this_pc(capsys, monkeypatch, plugin):
    for api in ("http://192.168.1.20:8765", "https://example.com", "http://127.0.0.2:8765", "http://my-pc:8765"):
        code, out, err, _ = _install(capsys, monkeypatch, plugin, "--api", api)
        assert code == 2, api
        assert err.splitlines()[-1] == ("error: install only talks to Clips Kitty on this PC (127.0.0.1, localhost "
                                        "or ::1)"), api
    # The one function that sends anything refuses too, before any request.
    with pytest.raises(installer.InstallRefused, match="only talks to Clips Kitty on this PC"):
        installer._call("http://example.com", "GET", "/health")
    for api in ("http://127.0.0.1:8765", "http://localhost:8765", "http://[::1]:8765"):
        assert _loopback.is_this_pc(api)


def test_install_says_when_clips_kitty_isnt_running(capsys, monkeypatch, plugin, data_dir):
    api = _closed_port()
    code, out, err, asked = _install(capsys, monkeypatch, plugin, "--api", api, "--data-dir", data_dir)
    assert code == 2 and asked == []
    assert err.splitlines()[-1] == (f"error: Clips Kitty isn't running: nothing answered at {api}. Open the app, "
                                    "then run this again.")


def test_install_says_when_the_app_is_older_than_plugin_support(capsys, monkeypatch, apps, plugin, data_dir):
    old = apps(version="2.0.0", plugins=False)

    def looked(*args, **kwargs):
        raise AssertionError("install looked for the session file")

    monkeypatch.setattr(installer, "session_file", looked)
    monkeypatch.setattr(installer, "_secret_in", looked)
    code, out, err, asked = _install(capsys, monkeypatch, plugin, "--api", old.url, "--data-dir", data_dir)
    assert code == 2 and asked == []
    assert err.splitlines()[-1] == (
        "error: This Clips Kitty (2.0.0) can't install plugins: it came out before plugin support. Use a newer "
        "Clips Kitty, or run it from source: https://github.com/ColinGPT9/clips-studio#from-source")
    assert old.calls() == [("GET", "/health"), ("GET", "/plugins")]


def test_the_from_source_link_points_at_a_real_heading():
    assert installer.FROM_SOURCE == "https://github.com/ColinGPT9/clips-studio#from-source"
    assert installer.FROM_SOURCE in installer.TOO_OLD


def _read_request(conn) -> None:
    """Read one HTTP request, its body included, so hanging up after the
    answer doesn't reset the connection before the answer is read."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            return
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    length = re.search(rb"(?im)^content-length:\s*(\d+)", head)
    while length and len(body) < int(length.group(1)):
        chunk = conn.recv(65536)
        if not chunk:
            return
        body += chunk


@pytest.fixture
def raw_servers():
    """Servers on 127.0.0.1 that answer every request with the same bytes,
    then hang up: what something that isn't Clips Kitty, or a Clips Kitty
    closing part way through an answer, sends."""
    made = []

    def make(reply: bytes) -> str:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.2)

        def serve():
            while listener.fileno() != -1:
                try:
                    conn, _ = listener.accept()
                except OSError:
                    continue
                with conn:
                    try:
                        conn.settimeout(10)
                        _read_request(conn)
                        conn.sendall(reply)
                        conn.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass

        threading.Thread(target=serve, daemon=True).start()
        made.append(listener)
        return f"http://127.0.0.1:{listener.getsockname()[1]}"

    yield make
    for listener in made:
        listener.close()


def test_install_says_what_is_wrong_with_an_answer_that_isnt_clips_kittys(capsys, monkeypatch, plugin, data_dir,
                                                                           raw_servers):
    """http.client's own errors, which urllib passes on, are error lines with
    exit code 2, not tracebacks."""
    ssh = raw_servers(b"SSH-2.0-OpenSSH_9.6\r\n")
    for api, said in (
            (ssh, installer.NOT_CLIPS_KITTY.format(api=ssh)),
            ("http://localhost:87x5", ("--api http://localhost:87x5 isn't an address install can use: give one "
                                       "such as http://127.0.0.1:8765")),
            ("http://127.0.0.1:99999", installer.BAD_API.format(api="http://127.0.0.1:99999"))):
        code, out, err, asked = _install(capsys, monkeypatch, plugin, "--api", api, "--data-dir", data_dir)
        assert (code, asked) == (2, []), err
        assert err.splitlines()[-1] == f"error: {said}"

    # An answer cut off part way: on its own, and in a --watch round, which goes on watching.
    cut = raw_servers(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n"
                      b'{"ok": 1}')
    for path in ("/health", "/plugins/plan"):
        with pytest.raises(installer.InstallRefused) as refused:
            installer._call(cut, "POST", path, {})
        assert str(refused.value) == f"Clips Kitty's answer to POST {path} stopped part way: is it still running?"
    out, err = io.StringIO(), io.StringIO()
    session = data_dir / "plugins" / "session.secret"
    watching = installer._reinstall(cut, plugin, session, out=out, err=err)
    assert watching is True
    assert err.getvalue().splitlines() == [
        "error: Clips Kitty's answer to POST /plugins/plan stopped part way: is it still running?",
        "Still watching: save again to try again."]


def test_install_follows_no_redirect_to_another_address(capsys, monkeypatch, apps, plugin, data_dir):
    """A redirect to another address, even one on this PC, isn't followed,
    so the session header goes nowhere but the address install was given."""
    elsewhere = apps(session_path=data_dir / "plugins" / "session.secret")
    app = apps(session_path=data_dir / "plugins" / "session.secret",
               redirects={"/plugins/plan": elsewhere.url + "/plugins/plan"})
    code, out, err, asked = _install(capsys, monkeypatch, plugin, "--api", app.url, "--data-dir", data_dir,
                                     answers=["y"])
    assert (code, asked) == (1, [])
    assert err.splitlines()[-1] == "error: Clips Kitty answered 302 to the plan"
    assert app.calls() == [("GET", "/health"), ("GET", "/plugins"), ("POST", "/plugins/plan")]
    assert elsewhere.requests == []
    # One to the same address (a framework's added or removed slash) is followed.
    app.redirects = {"/plugins/": app.url + "/plugins"}
    status, answer = installer._call(app.url, "GET", "/plugins/")
    assert (status, answer) == (200, {"plugins": [], "builtin": []})
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    anchors = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-")
               for h in re.findall(r"^#{1,6} +(.+?) *#*$", readme, re.M)}
    assert installer.FROM_SOURCE.split("#", 1)[1] in anchors
    assert re.search(r"^### From source$", readme, re.M)


def test_install_checks_the_plugin_before_talking_to_clips_kitty(capsys, monkeypatch, app, plugin, data_dir):
    manifest = plugin / "clipskitty.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("version: 0.1.0", "version: 0.1"),
                        encoding="utf-8")
    code, out, err, _ = _install(capsys, monkeypatch, plugin, "--api", app.url, "--data-dir", data_dir)
    assert code == 2
    assert "error: version: YAML read this as the number 0.1; write a version like 0.1.0" in err
    assert err.splitlines()[-1] == "error: fix the manifest first; Clips Kitty would refuse to install this plugin"
    assert app.requests == []
    code, _, err, _ = _install(capsys, monkeypatch, plugin.parent, "--api", app.url)
    assert code == 2 and "is this the plugin's folder?" in err and app.requests == []


# ---- the session file ------------------------------------------------------------------


def test_the_secret_comes_from_data_dir_then_the_403_then_localappdata(capsys, monkeypatch, apps, plugin, tmp_path,
                                                                        _no_installed_app):
    given = _session_file(tmp_path / "given", "secret-from-data-dir-0123")
    named = _session_file(tmp_path / "named by the app", "secret-the-app-names-0123")
    installed = _session_file(_no_installed_app / "Clips Studio" / "data", "secret-of-the-installed-app")
    assert installer.installed_app_file() == installed

    def used(app, *args):
        code, out, err, _ = _install(capsys, monkeypatch, plugin, "--api", app.url, *args, answers=["y"])
        assert code == 0, err
        secrets = {r["secret"] for r in app.requests if r["secret"]}
        assert len(secrets) == 1
        return secrets.pop()

    everything = ("secret-from-data-dir-0123", "secret-the-app-names-0123", "secret-of-the-installed-app")
    # 1. --data-dir, when it holds the file: nothing is asked without the header.
    app = apps(secrets=everything, session_path=named)
    assert used(app, "--data-dir", given.parent.parent) == "secret-from-data-dir-0123"
    assert app.calls(with_secret=False) == [("GET", "/health"), ("GET", "/plugins")]
    # 2. Else the file the app's 403 answer names, asked with a plan that doesn't exist.
    app = apps(secrets=everything, session_path=named)
    assert used(app, "--data-dir", tmp_path / "no such folder") == "secret-the-app-names-0123"
    assert app.calls(with_secret=False) == [("GET", "/health"), ("GET", "/plugins"), ("POST", "/plugins/install")]
    assert app.requests[2]["body"] == {"plan_id": "0" * 16}
    # 3. Else the installed app's, under LOCALAPPDATA.
    app = apps(secrets=everything, session_path=tmp_path / "gone" / "plugins" / "session.secret")
    assert used(app) == "secret-of-the-installed-app"
    # 4. Else it says where to look.
    installed.unlink()
    app = apps(secrets=everything, session_path=tmp_path / "gone" / "plugins" / "session.secret")
    code, out, err, asked = _install(capsys, monkeypatch, plugin, "--api", app.url)
    assert code == 2 and asked == []
    assert err.splitlines()[-1] == ("error: couldn't find Clips Kitty's session file. Pass --data-dir with Clips "
                                    "Kitty's data folder (the one holding plugins/session.secret)")
    assert ("POST", "/plugins/plan") not in app.calls()


def test_a_403_path_that_isnt_a_session_file_is_ignored(capsys, monkeypatch, apps, plugin, tmp_path,
                                                         _no_installed_app):
    real = _session_file(tmp_path / "Clips Studio" / "data")
    other = tmp_path / "data" / "plugins" / "other.secret"
    other.parent.mkdir(parents=True)
    other.write_text(SECRET, encoding="utf-8")
    wrong_folder = tmp_path / "data" / "notes" / "session.secret"
    wrong_folder.parent.mkdir(parents=True)
    wrong_folder.write_text(SECRET, encoding="utf-8")

    def detail(path):
        return f"This needs the X-Clips-Kitty-Session header. The desktop app sends it; a script finds it in {path}."

    assert installer.path_from_403(detail(real)) == real  # a folder name with a space in it
    for path in (other, wrong_folder, tmp_path / "missing" / "plugins" / "session.secret", tmp_path):
        assert installer.path_from_403(detail(path)) is None, path
    assert installer.path_from_403(None) is None and installer.path_from_403({"detail": str(real)}) is None

    # Through the command: a 403 naming the wrong file falls through to the installed app's.
    installed = _session_file(_no_installed_app / "Clips Studio" / "data", "secret-of-the-installed-app")
    app = apps(secrets=(SECRET, "secret-of-the-installed-app"), session_path=other)
    code, _, err, _ = _install(capsys, monkeypatch, plugin, "--api", app.url, answers=["y"])
    assert code == 0, err
    assert {r["secret"] for r in app.requests if r["secret"]} == {"secret-of-the-installed-app"}
    assert installed.is_file()


def test_the_secret_is_never_printed_or_on_a_command_line(capsys, monkeypatch, apps, app, plugin, data_dir,
                                                         tmp_path):
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a) or pytest.fail("started a program"))
    environ_before = dict(os.environ)

    code, out, err, _ = _install(capsys, monkeypatch, plugin, "--api", app.url, "--data-dir", data_dir,
                                 answers=["y"])
    assert code == 0
    assert [r["secret"] for r in app.requests if r["path"] in ("/plugins/plan", "/plugins/install")] == [SECRET] * 2
    # A secret the app doesn't accept is refused without being shown either.
    stale = apps(secrets=("a-different-secret-0123",), session_path=data_dir / "plugins" / "session.secret")
    code, out2, err2, _ = _install(capsys, monkeypatch, plugin, "--api", stale.url, "--data-dir", data_dir)
    assert code == 2
    assert err2.splitlines()[-1].startswith("error: Clips Kitty didn't accept the session file ")
    # A file holding something that isn't a secret Clips Kitty writes is never sent or shown.
    odd = tmp_path / "odd"
    _session_file(odd, "not a secret\nwith two lines")
    code, out3, err3, _ = _install(capsys, monkeypatch, plugin, "--api", app.url, "--data-dir", odd)
    assert code == 2 and "couldn't find Clips Kitty's session file" in err3

    shown = out + err + out2 + err2 + out3 + err3
    assert SECRET not in shown and "with two lines" not in shown
    assert started == [] and SECRET not in " ".join(sys.argv)
    assert dict(os.environ) == environ_before
    written = [p for p in tmp_path.rglob("*") if p.is_file() and p != data_dir / "plugins" / "session.secret"
               and SECRET.encode() in p.read_bytes()]
    assert written == []


@pytest.fixture
def proxy_settings(monkeypatch, apps):
    """http_proxy, HTTP_PROXY and HTTPS_PROXY pointing at a server that
    records what reaches it."""
    proxy = apps()
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    for name in ("http_proxy", "HTTP_PROXY", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, proxy.url)
    monkeypatch.setattr(urllib.request, "_opener", None)
    importlib.reload(_loopback)
    return proxy


def test_install_ignores_proxy_settings(capsys, monkeypatch, app, plugin, data_dir, proxy_settings):
    # Without the SDK's own route, urllib sends a request for this PC to the proxy.
    with pytest.raises(urllib.error.HTTPError):  # the stand-in proxy answers 404
        urllib.request.build_opener().open(app.url + "/health", timeout=10)
    assert proxy_settings.calls() == [("GET", app.url + "/health")]
    proxy_settings.requests.clear()
    app.requests.clear()

    code, out, err, _ = _install(capsys, monkeypatch, plugin, "--api", app.url, "--data-dir", data_dir,
                                 answers=["y"])
    assert code == 0, err
    assert app.calls() == [("GET", "/health"), ("GET", "/plugins"), ("POST", "/plugins/plan"),
                           ("POST", "/plugins/install")]
    assert [r["secret"] for r in app.requests] == [None, None, SECRET, SECRET]
    assert proxy_settings.requests == []


# ---- the plan, the question, --yes and --watch -------------------------------------------


def test_install_shows_the_apps_text_and_asks(capsys, monkeypatch, apps, app, plugin, data_dir):
    code, out, err, asked = _install(capsys, monkeypatch, plugin, "--api", app.url, "--data-dir", data_dir,
                                     answers=["y"])
    assert code == 0, err
    assert asked == ["Install it? [y/N] "]
    assert out == (APP_TEXT + "\nInstalled your-github-name/quarkbloom-bursts 0.1.0. Choose it in Clips Kitty: "
                   "Pipeline (to find moments) or Rate & understand.\n")
    assert app.requests[2]["body"] == {"source": {"kind": "folder", "path": str(plugin.resolve())}}
    assert app.requests[3]["body"] == {"plan_id": "0123456789abcdef"}

    # No, or no answer at all: nothing is installed.
    for answers in (["n"], ["no"], [""], []):
        app.requests.clear()
        code, out, err, asked = _install(capsys, monkeypatch, plugin, "--api", app.url, "--data-dir", data_dir,
                                         answers=answers)
        assert code == 1 and out.endswith("Not installed.\n") and asked == ["Install it? [y/N] "]
        assert ("POST", "/plugins/install") not in app.calls()

    # An app that doesn't send the text gets a short one.
    plain = apps(plans=[_plan(text=None, warnings=["No longer listed: test"])])
    code, out, err, _ = _install(capsys, monkeypatch, plugin, "--api", plain.url, "--data-dir", data_dir,
                                 answers=["n"])
    assert out.splitlines()[:4] == ["Install Quarkbloom Bursts 0.1.0?", "It will", "  Reads its transcript",
                                    "! No longer listed: test"]

    # A plan Clips Kitty refuses is shown, and nothing is asked.
    refused = apps(plans=[_plan(ok=False, plan_id=None, errors=["Can't install: it needs Clips Kitty >=9.0"],
                                text="Install Quarkbloom Bursts 0.1.0?\n✗ Can't install: it needs Clips Kitty "
                                     ">=9.0")])
    code, out, err, asked = _install(capsys, monkeypatch, plugin, "--api", refused.url, "--data-dir", data_dir)
    assert code == 1 and asked == []
    assert "✗ Can't install: it needs Clips Kitty >=9.0" in out
    assert err.splitlines()[-1] == "error: Clips Kitty won't install it, for the reasons above"
    assert ("POST", "/plugins/install") not in refused.calls()


def test_yes_refuses_when_something_is_new(capsys, monkeypatch, apps, plugin, data_dir):
    def yes(update, **changes):
        app = apps(plans=[_plan(update=update, **changes)])
        code, out, err, asked = _install(capsys, monkeypatch, plugin, "--api", app.url, "--data-dir", data_dir,
                                         "--yes")
        assert asked == []
        return code, err, ("POST", "/plugins/install") in app.calls(with_secret=True)

    # Nothing new: the same plugin, and an update that adds nothing.
    assert installer.nothing_new(_plan(update=NOTHING_NEW))
    code, err, installed = yes(NOTHING_NEW)
    assert (code, installed) == (0, True), err
    assert yes({**NOTHING_NEW, "direction": "update", "from": "0.0.9", "removed_permissions": ["ffmpeg"]})[::2] \
        == (0, True)

    # Each kind of new thing, in turn.
    for key, value, words in (
            ("added_permissions", ["ffmpeg"], "permissions: ffmpeg"),
            ("added_hosts", ["api.example.com"], "network hosts: api.example.com"),
            ("added_data_warnings", ["⚠ Sends your video's transcript off this computer"],
             "data sent off the PC: ⚠ Sends your video's transcript off this computer"),
            ("execution_changed", True, "a change to where it runs"),
            ("added_steps", ["Rates moments"], "steps: Rates moments")):
        update = {**NOTHING_NEW, key: value}
        assert installer.what_is_new(_plan(update=update)) == [words]
        code, err, installed = yes(update)
        assert (code, installed) == (1, False), key
        assert err.splitlines()[-1] == (f"error: --yes only skips the question when nothing is new; this adds: "
                                        f"{words}. Run without --yes and answer the question.")

    # A key the plan leaves out counts as new, and so does a first install.
    for key, what in (("added_permissions", "permissions"), ("added_hosts", "network hosts"),
                      ("added_data_warnings", "data sent off the PC"), ("execution_changed", "where it runs"),
                      ("added_steps", "steps")):
        update = {k: v for k, v in NOTHING_NEW.items() if k != key}
        assert installer.what_is_new(_plan(update=update)) == [
            f"{what} (this Clips Kitty's plan doesn't say, so it counts as new)"]
        assert yes(update)[::2] == (1, False), key
    assert installer.what_is_new(_plan(update={**NOTHING_NEW, "execution_changed": None})) == [
        "where it runs (this Clips Kitty's plan doesn't say, so it counts as new)"]
    code, err, installed = yes(None)
    assert (code, installed) == (1, False)
    assert "this adds: a plugin that isn't installed in this Clips Kitty yet." in err
    assert installer.what_is_new({"ok": True}) == ["a plugin that isn't installed in this Clips Kitty yet"]


def test_watch_reinstalls_on_save_and_stops_when_something_is_new(apps, plugin, data_dir):
    app = apps(plans=[_plan(), _plan(update=NOTHING_NEW),
                      _plan(update={**NOTHING_NEW, "added_permissions": ["network"], "added_hosts": ["a.example.com"]})])
    main_py = plugin / "src" / "main.py"
    saves = iter([
        lambda: main_py.write_text(main_py.read_text(encoding="utf-8") + "\n# a save\n", encoding="utf-8"),
        None,  # a second later: nothing more, so it reinstalls
        lambda: (plugin / "src" / "__pycache__").mkdir() or (plugin / "src" / "__pycache__" / "main.pyc").write_bytes(
            b"\0"),  # not watched
        lambda: (plugin / "README.md").write_text("# Quarkbloom Bursts\n", encoding="utf-8"),
        lambda: (plugin / "README.md").write_text("# Quarkbloom Bursts, saved again\n", encoding="utf-8"),
        None,
    ])
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        action = next(saves, "done")
        if action == "done":
            raise AssertionError("still watching after the save that adds something")
        if action:
            action()

    out, err = io.StringIO(), io.StringIO()
    code = installer.run(plugin, api=app.url, data_dir=data_dir, watch=True, ask=lambda q: "y", out=out, err=err,
                         sleep=sleep)
    assert code == 1, err.getvalue()
    lines = out.getvalue().splitlines()
    assert lines[-3] == "Installed your-github-name/quarkbloom-bursts 0.1.0. Choose it in Clips Kitty: Pipeline " \
                        "(to find moments) or Rate & understand."
    assert lines[-2] == f"Watching {plugin.resolve()} for saves; Ctrl+C stops."
    assert re.fullmatch(r"Reinstalled your-github-name/quarkbloom-bursts 0\.1\.0 at \d\d:\d\d:\d\d", lines[-1])
    assert err.getvalue().splitlines()[-1] == (
        "Stopped watching: this change adds permissions: network; network hosts: a.example.com. Run install again "
        "to see it and answer.")
    assert app.calls(with_secret=True) == [("POST", "/plugins/plan"), ("POST", "/plugins/install"),
                                           ("POST", "/plugins/plan"), ("POST", "/plugins/install"),
                                           ("POST", "/plugins/plan")]
    assert slept == [1.0] * 6

    # Ctrl+C stops it.
    def interrupted(seconds):
        raise KeyboardInterrupt

    out = io.StringIO()
    app = apps(plans=[_plan(update=NOTHING_NEW)])
    assert installer.run(plugin, api=app.url, data_dir=data_dir, watch=True, yes=True, out=out, err=io.StringIO(),
                         sleep=interrupted) == 0
    assert out.getvalue().splitlines()[-1] == "Stopped watching."


def test_watch_keeps_watching_after_a_save_clips_kitty_refuses(apps, plugin, data_dir):
    app = apps(plans=[_plan(update=NOTHING_NEW),
                      _plan(ok=False, plan_id=None, errors=["run.command: src/missing.py is not in the plugin's folder"]),
                      _plan(update=NOTHING_NEW)])
    readme = plugin / "README.md"
    saves = iter([lambda: readme.write_text("one\n", encoding="utf-8"), None,
                  lambda: readme.write_text("two, longer\n", encoding="utf-8"), None])

    def sleep(seconds):
        action = next(saves, "done")
        if action == "done":
            raise KeyboardInterrupt
        if action:
            action()

    out, err = io.StringIO(), io.StringIO()
    assert installer.run(plugin, api=app.url, data_dir=data_dir, watch=True, yes=True, out=out, err=err,
                         sleep=sleep) == 0
    assert err.getvalue().splitlines() == [
        "error: run.command: src/missing.py is not in the plugin's folder",
        "error: Clips Kitty won't install it, for the reasons above",
        "Still watching: save again to try again."]
    assert [line.split(" at ")[0] for line in out.getvalue().splitlines()[-2:]] == [
        "Reinstalled your-github-name/quarkbloom-bursts 0.1.0", "Stopped watching."]


# ---- development files -------------------------------------------------------------------


def test_install_refuses_dev_folders_and_symlinks(capsys, monkeypatch, app, plugin, data_dir, tmp_path):
    for name in (".venv", "venv", "node_modules", "src/node_modules"):
        (plugin / name).mkdir(parents=True)
        (plugin / name / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
        code, out, err, _ = _install(capsys, monkeypatch, plugin, "--api", app.url, "--data-dir", data_dir)
        assert code == 2
        assert err.splitlines()[-1] == (f"error: {name} is inside the plugin's folder, and Clips Kitty would copy "
                                        "it into every install. Move it next to the folder, then run this again.")
        (plugin / name / "pyvenv.cfg").unlink()
        (plugin / name).rmdir()
    assert app.requests == []

    # .clipskitty and a video over 50 MB are copied: a warning, and the install goes on.
    (plugin / ".clipskitty").mkdir()
    big = plugin / "media" / "my-match.mp4"
    big.parent.mkdir()
    with open(big, "wb") as f:
        f.truncate(installer.BIG_VIDEO_BYTES + 1)
    (plugin / "media" / "short.mp4").write_bytes(b"\0" * 1024)
    code, out, err, _ = _install(capsys, monkeypatch, plugin, "--api", app.url, "--data-dir", data_dir,
                                 answers=["y"])
    assert code == 0, err
    assert [line for line in err.splitlines() if line.startswith("warning:")] == [
        ("warning: .clipskitty is inside the plugin's folder; Clips Kitty copies everything there except .git and "
         "__pycache__."),
        ("warning: media/my-match.mp4 is inside the plugin's folder; Clips Kitty copies everything there except "
         ".git and __pycache__.")]
    big.unlink()
    app.requests.clear()

    # A symbolic link anywhere, or the folder itself as one.
    target = tmp_path / "elsewhere.json"
    target.write_text("{}", encoding="utf-8")
    try:
        os.symlink(target, plugin / "src" / "data.json")
    except (OSError, NotImplementedError):
        pytest.skip("this PC can't make symbolic links")
    code, out, err, _ = _install(capsys, monkeypatch, plugin, "--api", app.url, "--data-dir", data_dir)
    assert code == 2
    assert err.splitlines()[-1] == ("error: src/data.json is a symbolic link, and Clips Kitty refuses to install a "
                                    "folder that has one")
    (plugin / "src" / "data.json").unlink()
    linked = tmp_path / "linked-plugin"
    os.symlink(plugin, linked, target_is_directory=True)
    code, out, err, _ = _install(capsys, monkeypatch, linked, "--api", app.url, "--data-dir", data_dir)
    assert code == 2 and err.splitlines()[-1] == (f"error: {linked} is a symbolic link, and Clips Kitty refuses to "
                                                  "install a folder that has one")
    # A virtual environment holds symbolic links: it is named for what it is.
    (plugin / ".venv" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, plugin / ".venv" / "bin" / "python")
    code, out, err, _ = _install(capsys, monkeypatch, plugin, "--api", app.url, "--data-dir", data_dir)
    assert code == 2 and err.splitlines()[-1].startswith("error: .venv is inside the plugin's folder")
    assert app.requests == []


def test_localapi_has_no_session_parameter():
    for function in (LocalAPI.__init__, LocalAPI.request, LocalAPI.get, LocalAPI.post, LocalAPI.patch,
                     LocalAPI.delete):
        names = set(inspect.signature(function).parameters)
        assert not {"session", "secret", "headers"} & names, function.__name__
    # Only the installer's private helper sends the session header.
    senders = sorted(p.name for p in (SDK / "clipskitty_sdk").rglob("*.py")
                     if "X-Clips-Kitty-Session" in p.read_text(encoding="utf-8"))
    assert senders == ["installer.py"]
    source = inspect.getsource(installer)
    assert source.count("headers[HEADER]") == 1
    assert "headers[HEADER]" in inspect.getsource(installer._call)
