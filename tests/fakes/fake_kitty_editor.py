"""A stand-in for Clips Kitty Editor's automation, for tests.

Run as `python fake_kitty_editor.py` it speaks what the editor speaks when
started with `--mcp headless`: MCP (JSON-RPC 2.0, one message a line) on
standard input and output, with the tools kitty_editor/build.py uses. It
keeps each project in memory as lanes and clips, does the same arithmetic
the editor does for a cut, a removal that closes the lane up, a speed
change and a trim, and writes the project as JSON when it is saved:

    <project folder>/concat.json    {"video", "media", "tracks", "clips"}
    <project folder>/concat.lock    while the project is open

Like the editor, it only writes inside the folders CONCAT_MCP_ROOTS names.

Environment, for tests:
    FAKE_KITTY_EDITOR_SECONDS   the length it reports for a video or sound file (default 30)
    FAKE_KITTY_EDITOR_FAIL      a tool's name: that tool answers with an error

Standard library only; nothing here is shipped.
"""

import json
import os
import sys
from pathlib import Path

STILLS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
MIN = 1 / 60

projects: dict[str, dict] = {}
counter = [0]


def key(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def allowed(path) -> bool:
    roots = [r for r in os.environ.get("CONCAT_MCP_ROOTS", "").split(os.pathsep) if r]
    here = key(path)
    return any(here == key(r) or here.startswith(key(r).rstrip("\\/") + os.sep) for r in roots)


def mint(prefix: str) -> str:
    counter[0] += 1
    return f"{prefix}{counter[0]}"


def summary(p: dict, created=None) -> dict:
    clips = sorted(p["clips"], key=lambda c: (c["start"], c["id"]))
    shown = []
    for c in clips:
        item = {k: v for k, v in c.items() if v is not None}
        item["end"] = round(c["start"] + c["duration"], 6)
        shown.append(item)
    out = {
        "canUndo": True, "canRedo": False, "media": list(p["media"]),
        "project": {"name": p["name"], "width": p["video"]["width"], "height": p["video"]["height"],
                    "fps": p["video"]["rateNum"] / p["video"]["rateDen"]},
        "timeline": {"id": "TL1", "name": "Timeline 1", "clips": shown,
                     "duration": max((c["end"] for c in shown), default=0.0),
                     "tracks": [{"id": t, "index": i} for i, t in enumerate(p["tracks"])]},
    }
    if created is not None:
        out["createdId"] = created
    return out


def project(args) -> dict:
    p = projects.get(key(args["path"]))
    if p is None:
        raise ValueError("That project is not open.")
    return p


def clip_of(p, clip_id) -> dict | None:
    return next((c for c in p["clips"] if c["id"] == clip_id), None)


def later(p, clip, by: float) -> None:
    """Every clip after this one on its lane moves by `by` seconds."""
    for other in p["clips"]:
        if other is not clip and other["track"] == clip["track"] and other["start"] >= clip["start"] - 1e-9:
            other["start"] = round(other["start"] + by, 6)


def write(p: dict) -> None:
    Path(p["path"], "concat.json").write_text(json.dumps(
        {"name": p["name"], "video": p["video"], "media": p["media"], "tracks": p["tracks"],
         "clips": sorted(p["clips"], key=lambda c: (c["start"], c["id"]))}, indent=1), encoding="utf-8")


def tool(name: str, args: dict):
    if os.environ.get("FAKE_KITTY_EDITOR_FAIL") == name:
        raise ValueError(f"{name} was made to fail.")
    if name == "concat_version":
        return {"concat": "0.0.0-fake", "mcp": {"mode": "headless"}}
    if name == "project_create":
        location = Path(args["location"])
        if not allowed(location):
            raise ValueError("That folder is outside the folders this may write in.")
        if not location.is_dir():
            raise ValueError("That folder does not exist.")
        home = location / args["name"]
        if (home / "concat.json").exists():
            raise ValueError("That folder already holds a project.")
        home.mkdir()
        (home / "concat.lock").write_text("fake", encoding="utf-8")
        video = args.get("video") or {"width": 1920, "height": 1080, "rateNum": 30, "rateDen": 1}
        p = {"path": str(home), "name": args["name"], "video": video, "media": [], "clips": [],
             "tracks": ["T1", "T2", "T3", "T4"]}
        projects[key(home)] = p
        write(p)
        return summary(p)
    if name == "project_open":
        home = Path(args["path"])
        if (home / "concat.lock").exists():
            raise ValueError("That project is open somewhere else.")
        saved = json.loads((home / "concat.json").read_text(encoding="utf-8"))
        p = {"path": str(home), **saved}
        (home / "concat.lock").write_text("fake", encoding="utf-8")
        projects[key(home)] = p
        return summary(p)
    p = project(args)
    if name in ("timeline_read", "project_get"):
        return summary(p)
    if name == "project_save":
        write(p)
        return {}
    if name == "project_close":
        if args.get("save"):
            write(p)
        Path(p["path"], "concat.lock").unlink()
        del projects[key(p["path"])]
        return {}
    if name == "media_import":
        file = args["file"]
        if not os.path.isfile(file):
            raise ValueError("That file does not exist.")
        if not allowed(file):
            raise ValueError("That file is outside the folders this may read.")
        known = next((m for m in p["media"] if key(m["path"]) == key(file)), None)
        if known is not None:
            return summary(p)
        still = file.lower().endswith(STILLS)
        item = {"id": mint("m"), "name": os.path.basename(file), "path": file.replace("\\", "/"),
                "kind": "image" if still else "video", "hasAudio": not still,
                "duration": 5.0 if still else float(os.environ.get("FAKE_KITTY_EDITOR_SECONDS", "30")),
                "width": 400 if still else p["video"]["width"], "height": 200 if still else p["video"]["height"]}
        p["media"].append(item)
        return summary(p, item["id"])
    if name == "track_add":
        lane = mint("t")
        if args.get("bottom"):
            p["tracks"].insert(0, lane)
        else:
            p["tracks"].append(lane)
        return summary(p, lane)
    if name == "clip_add":
        media = next((m for m in p["media"] if m["id"] == args["mediaId"]), None)
        if media is None or args["trackId"] not in p["tracks"]:
            raise ValueError("No such media or lane.")
        clip = {"id": mint("c"), "kind": media["kind"], "media": media["id"], "name": media["name"],
                "track": args["trackId"], "start": max(0.0, float(args["start"])),
                "duration": media["duration"], "sourceStart": 0.0}
        p["clips"].append(clip)
        return summary(p, clip["id"])
    if name == "clips_split":
        t, made = float(args["time"]), None
        for clip in [c for c in p["clips"] if c["id"] in args["clipIds"]]:
            if clip["start"] + MIN < t < clip["start"] + clip["duration"] - MIN:
                speed = clip.get("speed") or 1.0
                head = t - clip["start"]
                tail = {**clip, "id": mint("c"), "start": round(t, 6), "duration": round(clip["duration"] - head, 6),
                        "sourceStart": round(clip["sourceStart"] + head * speed, 6)}
                clip["duration"] = round(head, 6)
                p["clips"].append(tail)
                made = tail["id"]
        return summary(p, made)
    if name == "clips_remove":
        for clip in sorted((c for c in p["clips"] if c["id"] in args["clipIds"]), key=lambda c: -c["start"]):
            p["clips"].remove(clip)
            if args.get("ripple"):
                later(p, clip, -clip["duration"])
        return summary(p)
    if name == "captions_add":
        lane, made = mint("t"), None
        p["tracks"].append(lane)
        for line in args["captions"]:
            made = mint("c")
            p["clips"].append({"id": made, "kind": "text", "track": lane, "name": line["text"], "text": line["text"],
                               "start": float(line["start"]), "duration": round(line["end"] - line["start"], 6),
                               "offsetY": args.get("offsetY", 0.35), "style": args.get("style") or {}})
        return summary(p, made)
    if name == "title_add":
        lane = args.get("trackId") or mint("t")
        if lane not in p["tracks"]:
            p["tracks"].append(lane)
        style = args.get("style") or {}
        clip = {"id": mint("c"), "kind": "text", "track": lane, "name": style.get("content", ""),
                "text": style.get("content", ""), "start": float(args["start"]),
                "duration": float(args.get("duration") or 4.0), "offsetY": args.get("offsetY"), "style": style}
        p["clips"].append(clip)
        return summary(p, clip["id"])
    clip = clip_of(p, args.get("clipId"))
    if clip is None:
        return summary(p)                   # the editor leaves an unknown clip alone
    if name == "clip_frame":
        clip["framing"] = args["framing"]
    elif name == "clip_update":
        for field, value in (args.get("patch") or {}).items():
            clip[field] = value
    elif name == "clip_transform":
        for field in ("scale", "offsetX", "offsetY", "rotation"):
            if args.get(field) is not None:
                clip[field] = args[field]
    elif name == "clip_speed":
        old = clip.get("speed") or 1.0
        new = min(16.0, max(0.0625, float(args["speed"])))
        length = round(clip["duration"] * old / new, 6)
        if args.get("ripple"):
            later(p, clip, length - clip["duration"])
        clip["duration"], clip["speed"] = length, new
    elif name == "clip_trim":
        delta = float(args["delta"])
        if args["edge"] == "end":
            clip["duration"] = round(max(MIN, clip["duration"] + delta), 6)
        else:
            clip["duration"] = round(max(MIN, clip["duration"] - delta), 6)
            clip["sourceStart"] = round(clip["sourceStart"] + delta * (clip.get("speed") or 1.0), 6)
            if not args.get("ripple"):
                clip["start"] = round(clip["start"] + delta, 6)
    else:
        raise ValueError(f"The stand-in has no tool called {name}.")
    return summary(p)


def main() -> int:
    if "--version" in sys.argv:
        print("clips-kitty-editor 0.0.0-fake")
        return 0
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if "id" not in message:
            continue                        # a notification
        method, params = message.get("method"), message.get("params") or {}
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fake-kitty-editor", "version": "0"}}
        elif method == "tools/call":
            try:
                answer = tool(params.get("name"), params.get("arguments") or {})
                result = {"structuredContent": answer, "content": [{"type": "text", "text": json.dumps(answer)}]}
            except Exception as e:
                result = {"isError": True, "content": [{"type": "text", "text": str(e)}]}
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                                         "error": {"code": -32601, "message": "no such method"}}) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
