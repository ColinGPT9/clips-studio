"""Closing Clips Kitty at any moment of a Make clips again loses no clip's
video (core/remake.py JOURNAL and recover()).

Closing the app ends the engine with taskkill /F, and a power cut or Windows
shutting down end it the same way: nothing after that moment runs. A run
moves the old clips' files to put-aside names and back, and points their
rows there and back; a kill between a file's move and its row's commit used
to leave a row on a name with no file, and then the next Make clips again or
Storage's clean-up deleted the real file.

Each test runs a Make clips again in a child process
(tests/remake_crash_child.py) and kills it right after one point, each in
turn: every rename, copy, hard link and delete core/remake.py makes, every
file the run writes, and every commit on the worker's connection. Then a new
process starts as the engine does and checks that every clip from before
still plays its own video: after Storage's clean-up run before the start-up
recovery, after the recovery (every row on a file with its own video, the
video's own copy back, no journal left), after the next Make clips again,
and after Storage's clean-up once more.

Standard library and core/ only, so it runs on CI's light install. The
children run a few at a time to keep it quick.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
CHILD = HERE / "remake_crash_child.py"
KILLED = 3
AT_ONCE = 6


def _start(*args) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(CHILD), *map(str, args)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8")


def _finish(proc: subprocess.Popen) -> tuple[int, dict, str]:
    out, err = proc.communicate(timeout=180)
    lines = [line for line in out.splitlines() if line.startswith("{")]
    return proc.returncode, (json.loads(lines[-1]) if lines else {}), out + err


def _all(starts: list) -> list:
    """Each command started, AT_ONCE at a time; their results in order."""
    results = []
    for i in range(0, len(starts), AT_ONCE):
        procs = [start() for start in starts[i:i + AT_ONCE]]
        results += [_finish(p) for p in procs]
    return results


def _whole_run(tmp_path: Path, scenario: str) -> list[str]:
    """The points of the whole run, in order: run once without a kill."""
    code, said, text = _finish(_start("run", tmp_path / "whole" / "data", scenario, 0))
    assert code == 0, text
    assert said["points"] == len(said["names"])
    return said["names"]


def _killed(datas: dict[int, Path], scenario: str) -> None:
    """Each library's run, killed right after its point."""
    results = _all([lambda d=d, n=n: _start("run", d, scenario, n) for n, d in datas.items()])
    for n, (code, said, text) in zip(datas, results):
        assert code == KILLED and said.get("point") == n, (n, text)


def _what_is_wrong(said: dict, text: str) -> dict:
    if not said or "after_recovery" not in said:
        return {"failed": text[-1500:]}
    wrong = {k: said[k] for k in ("cleanup_before_recovery", "after_recovery", "after_next_remake",
                                  "after_cleanup") if said[k]}
    if said["journal_left"]:
        wrong["journal_left"] = said["journal_left"]
    if not said["copy"] or said["downloads"] != ["abcdefghijk.mp4"]:
        wrong["downloads"] = said["downloads"]
    return wrong


@pytest.mark.parametrize("scenario", ["finish", "stopped"])
def test_closing_the_app_at_any_moment_keeps_every_clips_video(tmp_path, scenario):
    """`finish`: a run that finishes, deletes a clip nobody used, makes one
    again in place, and leaves a kept clip aside because a new clip took its
    first name. `stopped`: a run that deletes the video's own copy (its first
    step does when FFprobe can't read it) and fails."""
    names = _whole_run(tmp_path, scenario)
    # Every kind of point is there: the files put aside and back, the run's
    # renders, the video's own copy, and the commits between them.
    assert {"rename", "commit", "link", "render written"} <= set(names), names
    datas = {n: tmp_path / f"kill{n}" / "data" for n in range(1, len(names) + 1)}
    _killed(datas, scenario)
    results = _all([lambda d=d: _start("restart", d) for d in datas.values()])
    wrong = {f"{n} (after {names[n - 1]})": _what_is_wrong(said, text)
             for n, (code, said, text) in zip(datas, results)}
    assert not {k: v for k, v in wrong.items() if v}, json.dumps(wrong, indent=1)


def test_closing_the_app_while_the_start_up_recovery_works_is_recovered_too(tmp_path):
    """The recovery moves files and commits too. Killed after any of its
    points, the next start finishes it. The run is killed once all its
    renders are made, every old clip's file put aside: the recovery has the
    most to do then."""
    names = _whole_run(tmp_path, "finish")
    at = len(names) - names[::-1].index("render written") + 1      # the last render's commit
    assert names[at - 1] == "commit"
    whole = tmp_path / "whole-recovery" / "data"
    _killed({at: whole}, "finish")
    code, said, text = _finish(_start("restart", whole, 10_000))
    assert code == 0 and said["recovered_whole"], text
    steps = said["points"]
    assert steps >= 5, text                 # it put files back and committed
    datas = {n: tmp_path / f"recovery{n}" / "data" for n in range(1, steps + 1)}
    results = _all([lambda d=d: _start("run", d, "finish", at) for d in datas.values()])     # the run, killed
    assert all(code == KILLED for code, _, _ in results), [text[-800:] for _, _, text in results]
    results = _all([lambda d=d, n=n: _start("restart", d, n) for n, d in datas.items()])     # its recovery, killed
    assert all(code == KILLED for code, _, _ in results), [text[-800:] for _, _, text in results]
    results = _all([lambda d=d: _start("restart", d) for d in datas.values()])
    wrong = {n: _what_is_wrong(said, text) for n, (code, said, text) in zip(datas, results)}
    assert not {k: v for k, v in wrong.items() if v}, json.dumps(wrong, indent=1)
