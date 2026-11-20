"""Talking to Clips Kitty Editor with no window.

The editor has its own automation: started with `--mcp headless` it speaks
MCP (JSON-RPC 2.0, one message a line) on standard input and output and
edits projects with the same commands its window uses. Clips Kitty builds a
project this way, saves and closes it (which lets go of the editor's lock on
the folder), and only then is it opened in the window.

`command` is the one place that says how the editor is started: tests put a
small stand-in program there (tests/fakes/fake_kitty_editor.py).

Standard library only.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from pathlib import Path

CALL_TIMEOUT = 120.0        # seconds one call may take
PROTOCOL = "2025-06-18"
VERSION_PREFIX = "clips-kitty-editor "


class EditorError(RuntimeError):
    """The editor refused a call, did not answer in time, or ended."""


def command(exe: str) -> list[str]:
    """How the editor is started for automation."""
    return [exe, "--mcp", "headless"]


def _no_window() -> dict:
    """No console window flashes up on Windows."""
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def version_of(exe: str, timeout: float = 5.0) -> str | None:
    """What `--version` prints after the program's name when the program is
    Clips Kitty Editor ("clips-kitty-editor 0.2.11" gives "0.2.11"); None for
    anything else, and for a program that doesn't run."""
    try:
        done = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, **_no_window())
    except (OSError, subprocess.SubprocessError):
        return None
    line = (done.stdout or "").strip().splitlines()[0].strip() if (done.stdout or "").strip() else ""
    if done.returncode != 0 or not line.startswith(VERSION_PREFIX):
        return None
    return line[len(VERSION_PREFIX):].strip() or None


class Session:
    """One headless editor, from `initialize` to `close`.

    `roots` are the folders it may write in and read media from (it keeps to
    the person's home folder otherwise); what it logs goes to `log_path`.
    Any failure stops the process: nothing is left running."""

    def __init__(self, exe: str, roots, log_path: Path, timeout: float = CALL_TIMEOUT):
        self.timeout = timeout
        self._n = 0
        self._lines: queue.Queue = queue.Queue()
        env = dict(os.environ)
        env["CONCAT_MCP_ROOTS"] = os.pathsep.join(str(Path(r)) for r in roots)
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        self._log = open(log_path, "a", encoding="utf-8", errors="replace")
        try:
            self._proc = subprocess.Popen(
                command(exe), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log,
                text=True, encoding="utf-8", errors="replace", env=env, **_no_window())
        except OSError as e:
            self._log.close()
            raise EditorError(f"The editor could not be started: {e}") from e
        self._reader = threading.Thread(target=self._read, name="kitty-editor-reader", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        try:
            for line in self._proc.stdout:
                self._lines.put(line)
        except (OSError, ValueError):
            pass
        self._lines.put(None)       # it ended

    def _send(self, message: dict) -> None:
        try:
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as e:
            self.kill()
            raise EditorError("The editor stopped answering.") from e

    def _rpc(self, method: str, params: dict):
        import time

        self._n += 1
        mine = self._n
        self._send({"jsonrpc": "2.0", "id": mine, "method": method, "params": params})
        until = time.monotonic() + self.timeout
        while True:
            try:
                line = self._lines.get(timeout=max(0.05, until - time.monotonic()))
            except queue.Empty:
                self.kill()
                raise EditorError("The editor took too long to answer.") from None
            if line is None:
                self.kill()
                raise EditorError("The editor ended before it answered.")
            try:
                message = json.loads(line)
            except ValueError:
                continue                # not protocol: ignored
            if not isinstance(message, dict) or message.get("id") != mine:
                continue                # a notification, or an older answer
            if "error" in message:
                error = message["error"]
                text = error.get("message") if isinstance(error, dict) else str(error)
                raise EditorError(str(text or "The editor refused the request."))
            return message.get("result") or {}

    def initialize(self) -> dict:
        result = self._rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                          "clientInfo": {"name": "clips-kitty", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def tool(self, tool_name: str, **arguments) -> dict:
        """One of the editor's tools, by its name in the editor's docs/MCP.md.
        What it answers as a dict; EditorError with the editor's own sentence
        when it refuses."""
        result = self._rpc("tools/call", {"name": tool_name, "arguments": arguments})
        if isinstance(result.get("structuredContent"), dict):
            out = result["structuredContent"]
        else:
            content = result.get("content") or []
            text = content[0].get("text", "") if content and isinstance(content[0], dict) else ""
            try:
                out = json.loads(text)
            except ValueError:
                out = {"text": text}
            if not isinstance(out, dict):
                out = {"value": out}
        if result.get("isError"):
            raise EditorError(f"{tool_name}: {str(out.get('text') or out)[:300]}")
        return out

    def kill(self) -> None:
        try:
            self._proc.kill()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        self._close_log()

    def close(self) -> None:
        """End the session: closing its input is how the editor is told to
        leave. Killed if it has not gone in 30 seconds."""
        try:
            self._proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self._proc.wait(timeout=30)
        except (OSError, subprocess.SubprocessError):
            self.kill()
        self._close_log()

    def _close_log(self) -> None:
        try:
            self._log.close()
        except (OSError, ValueError):
            pass

    def __enter__(self) -> Session:
        try:
            self.initialize()
        except BaseException:
            self.kill()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.close()
        else:
            self.kill()
