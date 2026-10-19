"""Basketball framing bench: fixtures and numbers from the owner's own basketball job.

Everything is written under data/framing_bench/ (gitignored). The footage is a broadcast
processed on the owner's machine: it never enters the repository, and neither do the names
spoken or shown in it, so this file refers to clips by their local database id only.

Subcommands (more are added as the bench grows):

  prepare  Pick the bench clips from the job's saved clips. For each, cut the 16:9
           intermediate exactly as the app does for a clip (window [start, end + 0.4],
           re-encoded with an accurate seek by video.cutter.cut_clip) into clips/<id>.mp4,
           cut a context copy that starts 8 s earlier into context/<id>.mp4, and write
           meta.json.
  frames   Extract the labelling frames of every prepared clip: 3 spread over the clip and
           5 around the basket (sport_t -3.0, -2.0, -1.2, -0.5 and +0.3 s), or 8 spread
           over the clip when it has no basket time. Each is saved as a full-size PNG
           (label/plain/<id>_<ms>.png) and as a 1280x720 JPEG with a coordinate grid
           (label/grid/<id>_<ms>.jpg). label/frames.json lists them.
  measure  Run the observation stage (sports/basketball/crop/observe.py) on every prepared
           clip with one detector arm (coco, ebard or mixed), the basket time from meta.json
           as the moment, into obs/<arm>/<id>.json with the seconds per stage, and
           obs/<arm>/summary.json.
  detect-eval  Checkpoint 1: the detectors alone on the labelled frames, before any
           tracking. COCO yolov8n (person, sports ball) and E-BARD at each size run on
           every labelled PNG, every box at conf 0.10 kept in detect_eval_raw.json. Scored
           into detect_eval.json: ball recall, top-1 and false balls with and without
           simple gates, hoop recall and the hoop box against the labelled ring and
           backboard, people for the shot kind, and warm timing, split by shot kind,
           near the basket or not, and clip category. --reuse scores the saved boxes
           again without running the detectors.

    python scripts/basketball_framing_bench.py prepare [--job 210] [--only 4272,4255] [--force]
    python scripts/basketball_framing_bench.py frames [--only 4272] [--force]
    python scripts/basketball_framing_bench.py measure --arm coco|ebard|mixed [--only 4272]
    python scripts/basketball_framing_bench.py detect-eval [--ebard-sizes 704,960,1280] [--reuse]

The database is opened read-only and immutable, so the app may stay open while this runs.
"""

import argparse
import json
import os
import re
import sqlite3
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.binaries import ffprobe  # noqa: E402
from core.models import ClipCandidate  # noqa: E402
from core.paths import discard  # noqa: E402
from sports.basketball.crop.basket import ALLOW_OUT  # noqa: E402
from video.cutter import cut_clip  # noqa: E402
from video.encoding import video_encoder_args  # noqa: E402

DATA = ROOT / "data"
OUT = DATA / "framing_bench"
DEFAULT_JOB = 210

END_PAD = 0.4        # core/pipeline.py cuts every clip 0.4 s past its saved end
CONTEXT_LEAD = 8.0   # extra seconds before the start, for lead-in work

# Local clip ids of the owner's basketball job (job 210), picked by reading each
# window's commentary. Most threes have a pass before the shot; the last category
# holds clips whose first seconds are a TV close-up rather than the court.
SELECTION: dict[str, list[int]] = {
    "three": [4272, 4255, 4249, 4247, 4226, 4215, 4225, 4222, 4232, 4199],
    "dunk_layup": [4198, 4221, 4238, 4210, 4257, 4239],
    "block_rebound": [4283, 4288, 4259, 4241, 4291],
    "fast_break": [4275, 4282, 4263, 4208, 4217, 4207],
    "closeup_open": [4203, 4270, 4264, 4204, 4212],
}

# Commentary that says the ball moved between players before the play.
PASS_WORDS = re.compile(
    r"\b(pass\w*|kicks? it|kick(s|ed)? (it )?out|picks? it back out|whips?|swings? it|finds|"
    r"out to|cross-? ?court|lobs?|alley|feeds?|dishes|inbound\w*)\b",
    re.IGNORECASE,
)

SPREAD_WITH_BASKET = 3
SPREAD_WITHOUT_BASKET = 8
AROUND_BASKET = (-3.0, -2.0, -1.2, -0.5, 0.3)

GRID_SIZE = (1280, 720)


# ---- shared helpers ------------------------------------------------------------


def open_db(path: Path) -> sqlite3.Connection:
    # Read-only and immutable: the app may be running and holding the database.
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)


def parse_only(text: str | None) -> set[int] | None:
    if not text:
        return None
    return {int(part) for part in text.replace(" ", "").split(",") if part}


def job_video(con: sqlite3.Connection, job: int) -> str:
    row = con.execute("SELECT video_id FROM jobs WHERE id = ?", (job,)).fetchone()
    if row is None or not row[0]:
        raise SystemExit(f"job {job} has no video in the database")
    return row[0]


def find_source(video_id: str) -> Path:
    """The downloaded source. Listed rather than globbed: names can hold brackets."""
    folder = DATA / "downloads"
    names = os.listdir(folder)
    if f"{video_id}.mp4" in names:
        return folder / f"{video_id}.mp4"
    near = sorted(n for n in names if n.startswith(video_id) and n.lower().endswith(".mp4"))
    if not near:
        raise SystemExit(f"no download for {video_id} in {folder}")
    return folder / near[0]


def probe(path: Path) -> dict:
    """Size, frame rate and the exact frame count (packets counted, not estimated)."""
    r = subprocess.run(
        [ffprobe(), "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=width,height,r_frame_rate,nb_read_packets", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=600,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {r.stderr[-500:]}")
    stream = json.loads(r.stdout)["streams"][0]
    num, den = (float(x) for x in stream["r_frame_rate"].split("/"))
    fps = num / den
    frames = int(stream["nb_read_packets"])
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": round(fps, 5),
        "frames": frames,
        "duration": round(frames / fps, 3),
    }


def load_words(video_id: str) -> list[dict]:
    path = DATA / "transcripts" / f"{video_id}.json"
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    return [w for seg in doc.get("segments", []) for w in seg.get("words", [])]


def commentary(words: list[dict], start: float, end: float) -> str:
    return " ".join(w["word"].strip() for w in words if w["start"] >= start and w["end"] <= end)


def read_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ---- prepare -------------------------------------------------------------------


def cut(source: Path, start: float, end: float, dst: Path, force: bool) -> None:
    """The app's own cut. Written to a side name first, so a stopped run leaves no half file."""
    if not force and dst.exists() and dst.stat().st_size > 0:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.stem + ".part.mp4")
    try:
        cut_clip(source, ClipCandidate(start=start, end=end, score=0), part)
        os.replace(part, dst)
    finally:
        discard(part)


def cmd_prepare(args) -> None:
    only = parse_only(args.only)
    con = open_db(args.db)
    try:
        video_id = job_video(con, args.job)
        source = find_source(video_id)
        rows = {}
        for category, ids in SELECTION.items():
            for cid in ids:
                if only and cid not in only:
                    continue
                row = con.execute(
                    "SELECT id, video_id, start_s, end_s, hook, title, scores FROM clips WHERE id = ?",
                    (cid,),
                ).fetchone()
                if row is None or row[1] != video_id:
                    print(f"  clip {cid}: not a clip of job {args.job}, skipped")
                    continue
                rows[cid] = (category, row)
    finally:
        con.close()

    src = probe(source)
    words = load_words(video_id)
    print(f"Source {source.name}: {src['width']}x{src['height']} at {src['fps']} fps; "
          f"{len(rows)} clips to prepare")

    # Pick the encoder once, before the worker threads would each race to probe it.
    video_encoder_args()

    def one(cid: int) -> tuple[int, dict]:
        category, (_, _, start, end, hook, title, scores_json) = rows[cid]
        scores = json.loads(scores_json or "{}")
        t0 = time.time()
        clip_path = OUT / "clips" / f"{cid}.mp4"
        ctx_path = OUT / "context" / f"{cid}.mp4"
        cut(source, start, end + END_PAD, clip_path, args.force)
        ctx_start = round(max(0.0, start - CONTEXT_LEAD), 2)
        cut(source, ctx_start, end + END_PAD, ctx_path, args.force)
        clip_info = probe(clip_path)
        ctx_info = probe(ctx_path)
        sport_t = scores.get("sport_t")
        said = commentary(words, start, end + END_PAD)
        sport_label = scores.get("sport_label") or ""
        entry = {
            "category": category,
            "label": " | ".join(p for p in (sport_label, title or "", hook or "") if p),
            "title": title or "",
            "hook": hook or "",
            "sport_event": scores.get("sport_event"),
            "sport_label": sport_label or None,
            "source_start": start,
            "source_end": round(end + END_PAD, 2),
            "saved_end": end,
            "sport_t": round(sport_t - start, 3) if sport_t is not None else None,
            "sport_t_source": sport_t,
            **clip_info,
            "path": f"clips/{cid}.mp4",
            "context": {
                "path": f"context/{cid}.mp4",
                "source_start": ctx_start,
                "lead": round(start - ctx_start, 2),
                **ctx_info,
            },
            "pass_mention": bool(PASS_WORDS.search(said)),
            "commentary": said,
        }
        print(f"  {cid} {category}: {clip_info['duration']:.2f} s, {clip_info['frames']} frames, "
              f"sport_t {entry['sport_t']} ({time.time() - t0:.1f} s)")
        return cid, entry

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        done = dict(pool.map(one, list(rows)))

    meta_path = OUT / "meta.json"
    meta = read_json(meta_path)
    clips = meta.get("clips", {})
    clips.update({str(cid): done[cid] for cid in rows})
    order = [str(cid) for ids in SELECTION.values() for cid in ids]
    meta = {
        "job": args.job,
        "video_id": video_id,
        "source": str(source.relative_to(ROOT).as_posix()),
        "source_width": src["width"],
        "source_height": src["height"],
        "source_fps": src["fps"],
        "end_pad": END_PAD,
        "context_lead": CONTEXT_LEAD,
        "made": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "clips": {k: clips[k] for k in sorted(clips, key=lambda k: (order.index(k) if k in order else 999, k))},
    }
    write_json(meta_path, meta)
    print(f"Wrote {meta_path} ({len(meta['clips'])} clips)")


# ---- frames --------------------------------------------------------------------


def frame_targets(entry: dict) -> list[tuple[str, float]]:
    """(purpose, time in the clip) for the 8 labelling frames of one clip."""
    duration = entry["duration"]
    sport_t = entry.get("sport_t")
    spread = SPREAD_WITH_BASKET if sport_t is not None else SPREAD_WITHOUT_BASKET
    picks = [(f"spread_{i + 1}of{spread}", duration * (i + 0.5) / spread) for i in range(spread)]
    if sport_t is not None:
        picks += [(f"sport_t{d:+.1f}", sport_t + d) for d in AROUND_BASKET]
    return picks


def frame_indexes(targets: list[tuple[str, float]], fps: float, n_frames: int) -> list[tuple[str, float, int]]:
    """Nearest frame for each target, clamped into the clip; two targets never share one."""
    used: set[int] = set()
    out = []
    for purpose, t in targets:
        n = min(max(0, round(t * fps)), n_frames - 1)
        step = 1
        while n in used:  # clamped onto a frame already taken: the nearest free one
            for cand in (n + step, n - step):
                if 0 <= cand < n_frames and cand not in used:
                    n = cand
                    break
            step += 1
        used.add(n)
        out.append((purpose, t, n))
    return out


def read_frames(path: Path, wanted: set[int]) -> dict:
    """Decode the clip once, front to back, keeping the frames asked for."""
    from video.capture import video_capture

    got = {}
    last = max(wanted)
    with video_capture(path) as cap:
        for n in range(last + 1):
            if n in wanted:
                ok, frame = cap.read()
                if not ok:
                    break
                got[n] = frame
            elif not cap.grab():
                break
    return got


def grid_image(frame, caption: str):
    """A 1280x720 copy with a light coordinate grid.

    Thin translucent lines every 0.1 of the width and height, labelled 0.1..0.9 along the
    top and left edges, and faint ticks every 0.05 on the edges and along the lines. The
    lines are blended, not painted, so the picture under them stays visible.
    """
    import cv2

    w, h = GRID_SIZE
    img = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
    line_colour = (255, 255, 0)  # cyan in BGR: stands out on wood, crowd and jerseys
    xs = [round(w * k / 20) for k in range(21)]
    ys = [round(h * k / 20) for k in range(21)]

    lines = img.copy()
    for k in range(2, 20, 2):
        cv2.line(lines, (xs[k], 0), (xs[k], h - 1), line_colour, 1)
        cv2.line(lines, (0, ys[k]), (w - 1, ys[k]), line_colour, 1)
    img = cv2.addWeighted(lines, 0.5, img, 0.5, 0)

    ticks = img.copy()
    edge = 9
    for k in range(1, 20, 2):  # the 0.05 positions between the lines
        x, y = xs[k], ys[k]
        cv2.line(ticks, (x, 0), (x, edge), line_colour, 1)
        cv2.line(ticks, (x, h - 1), (x, h - 1 - edge), line_colour, 1)
        cv2.line(ticks, (0, y), (edge, y), line_colour, 1)
        cv2.line(ticks, (w - 1, y), (w - 1 - edge, y), line_colour, 1)
        for j in range(2, 20, 2):  # small crossings on each 0.1 line
            cv2.line(ticks, (xs[j] - 3, y), (xs[j] + 3, y), line_colour, 1)
            cv2.line(ticks, (x, ys[j] - 3), (x, ys[j] + 3), line_colour, 1)
    img = cv2.addWeighted(ticks, 0.45, img, 0.55, 0)

    font, scale = cv2.FONT_HERSHEY_SIMPLEX, 0.42
    labels = []
    for k in range(1, 10):
        text = f"0.{k}"
        labels.append((text, (xs[2 * k] + 3, 13)))   # along the top
        labels.append((text, (3, ys[2 * k] + 14)))   # down the left
    labels.append((caption, (6, h - 8)))
    boxes = img.copy()
    for text, (x, y) in labels:
        (tw, th), base = cv2.getTextSize(text, font, scale, 1)
        cv2.rectangle(boxes, (x - 2, y - th - 3), (x + tw + 2, y + base), (0, 0, 0), -1)
    img = cv2.addWeighted(boxes, 0.55, img, 0.45, 0)
    for text, (x, y) in labels:
        cv2.putText(img, text, (x, y), font, scale, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def cmd_frames(args) -> None:
    import cv2

    only = parse_only(args.only)
    meta = read_json(OUT / "meta.json")
    if not meta.get("clips"):
        raise SystemExit("no meta.json yet: run prepare first")
    plain_dir = OUT / "label" / "plain"
    grid_dir = OUT / "label" / "grid"
    plain_dir.mkdir(parents=True, exist_ok=True)
    grid_dir.mkdir(parents=True, exist_ok=True)

    index_path = OUT / "label" / "frames.json"
    index = read_json(index_path)
    frames = [f for f in index.get("frames", []) if only and f["clip"] not in only]

    for cid_text, entry in meta["clips"].items():
        cid = int(cid_text)
        if only and cid not in only:
            continue
        t0 = time.time()
        picks = frame_indexes(frame_targets(entry), entry["fps"], entry["frames"])
        decoded = read_frames(OUT / entry["path"], {n for _, _, n in picks})
        for purpose, target, n in picks:
            if n not in decoded:
                print(f"  {cid}: frame {n} could not be read, skipped")
                continue
            t = n / entry["fps"]
            ms = round(t * 1000)
            frame_id = f"{cid}_{ms}"
            plain = plain_dir / f"{frame_id}.png"
            grid = grid_dir / f"{frame_id}.jpg"
            if args.force or not plain.exists():
                cv2.imwrite(str(plain), decoded[n])
            if args.force or not grid.exists():
                caption = f"{cid}  t={t:.3f}s  {purpose}"
                cv2.imwrite(str(grid), grid_image(decoded[n], caption), [cv2.IMWRITE_JPEG_QUALITY, 90])
            frames.append({
                "frame_id": frame_id,
                "clip": cid,
                "category": entry["category"],
                "purpose": purpose,
                "target": round(target, 3),
                "n": n,
                "t": round(t, 3),
                "plain": plain.relative_to(OUT).as_posix(),
                "grid": grid.relative_to(OUT).as_posix(),
            })
        print(f"  {cid}: {len(picks)} frames ({time.time() - t0:.1f} s)")

    order = list(meta["clips"])
    frames.sort(key=lambda f: (order.index(str(f["clip"])) if str(f["clip"]) in order else 999, f["t"]))
    write_json(index_path, {"made": time.strftime("%Y-%m-%dT%H:%M:%S"), "frames": frames})
    print(f"Wrote {index_path} ({len(frames)} frames)")


# ---- rims-eval -----------------------------------------------------------------

RIM_IOU = 0.3          # a found rim matches a labelled ring at this IoU, or with the centres a ring width apart
HOLDOUT_EVERY = 3      # every third labelled clip (in id order) is held out of the tuning
BOARD_OK = 0.25        # ring widths: a board edge this close to the labelled one is right


def label_split(labels: list[dict]) -> dict[str, str]:
    """Clip id -> "tune" or "holdout". Every third clip in id order is held out, so the final
    numbers come from clips the finder's thresholds were never tuned on."""
    clips = sorted({x["frame_id"].split("_")[0] for x in labels}, key=int)
    return {c: "holdout" if i % HOLDOUT_EVERY == HOLDOUT_EVERY - 1 else "tune" for i, c in enumerate(clips)}


def ring_distance(box: tuple, ring: dict, size: tuple[int, int]) -> float | None:
    """The centres' distance in ring widths when a found box matches a labelled ring (IoU at
    least RIM_IOU, or the centres within one ring width, in pixels), else None."""
    width, height = size
    x0, y0, x1, y1 = box
    rx0, ry0, rx1, ry1 = ring["x0"], ring["y0"], ring["x1"], ring["y1"]
    inter = max(0.0, min(x1, rx1) - max(x0, rx0)) * max(0.0, min(y1, ry1) - max(y0, ry0))
    union = (x1 - x0) * (y1 - y0) + (rx1 - rx0) * (ry1 - ry0) - inter
    dx = ((x0 + x1) - (rx0 + rx1)) / 2 * width
    dy = ((y0 + y1) - (ry0 + ry1)) / 2 * height
    dist = (dx * dx + dy * dy) ** 0.5 / max(1e-9, (rx1 - rx0) * width)
    if (union > 0 and inter / union >= RIM_IOU) or dist <= 1.0:
        return dist
    return None


def board_offset(ring: dict, outer_x: float) -> float:
    """How far a board's outer edge lies past the ring's outer edge, in ring widths."""
    rw = ring["x1"] - ring["x0"]
    return ((ring["x0"] - outer_x) if ring["side"] == "left" else (outer_x - ring["x1"])) / rw


def board_outer(board: tuple, side: str) -> float:
    return board[0] if side == "left" else board[2]


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(q / 100 * (len(s) - 1))))]


def label_people(labels: list[dict], cache_path: Path) -> dict[str, list[list[float]]]:
    """The person boxes (x, y, w, h fractions) of every labelled frame, from the COCO model the
    app uses, cached: the rim finder turns down ring-coloured shapes on a torso."""
    import cv2

    cache = read_json(cache_path)
    missing = [x["frame_id"] for x in labels if x["frame_id"] not in cache]
    if missing:
        from sports.soccer.ball import _model, detect

        model = _model("yolov8n.pt")
        for fid in missing:
            frame = cv2.imread(str(OUT / "label" / "plain" / f"{fid}.png"))
            _balls, people = detect(model, frame, 1280)
            cache[fid] = [[round(v, 5) for v in p] for p in people]
        write_json(cache_path, cache)
    return cache


def draw_rims(frame, label: dict, rims: list, cands: list[dict], path: Path) -> None:
    """The frame with the labelled rings (green, the board's outer edge as a green line), the
    shapes turned down after the size gate (grey, with the reason's letter and score) and the
    rims found (red, with the board in cyan)."""
    import cv2

    img = frame.copy()
    height, width = img.shape[:2]

    def pt(x: float, y: float) -> tuple[int, int]:
        return round(x * width), round(y * height)

    for c in cands:
        if c["rejected"] in ("", "size", "aspect", "fill"):
            continue
        x0, y0, x1, y1 = c["box"]
        cv2.rectangle(img, pt(x0, y0), pt(x1, y1), (160, 160, 160), 1)
        cv2.putText(img, f"{c['rejected'][0]}{c['score']:.2f}", pt(x0, y1 + 0.02), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (200, 200, 200), 1, cv2.LINE_AA)
    for ring in label["rims"]:
        cv2.rectangle(img, pt(ring["x0"], ring["y0"]), pt(ring["x1"], ring["y1"]), (0, 255, 0), 1)
        bx = ring["backboard_outer_x"]
        cv2.line(img, pt(bx, ring["y0"] - 0.12), pt(bx, ring["y1"]), (0, 255, 0), 1)
    for r in rims:
        cv2.rectangle(img, pt(r.x - r.w / 2, r.y - r.h / 2), pt(r.x + r.w / 2, r.y + r.h / 2), (0, 0, 255), 2)
        cv2.putText(img, f"{r.score:.2f}", pt(r.x - r.w / 2, r.y - r.h / 2 - 0.01), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (0, 0, 255), 1, cv2.LINE_AA)
        if r.backboard:
            x0, y0, x1, y1 = r.backboard
            cv2.rectangle(img, pt(x0, y0), pt(x1, y1), (255, 255, 0), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 88])


def rims_eval(labels: list[dict], which: str = "all", use_people: bool = True, draw: str = "none",
              verbose: bool = False) -> dict[str, dict]:
    """Run the classic rim finder on the labelled frames of one split (tune, holdout or all);
    the counts per split."""
    import cv2

    from sports.basketball.crop import rim_finder
    from sports.basketball.crop.model import Person

    split_of = label_split(labels)
    people = label_people(labels, OUT / "rimdbg" / "people.json") if use_people else {}
    out: dict[str, dict] = {}
    for x in labels:
        fid = x["frame_id"]
        if which != "all" and split_of[fid.split("_")[0]] != which:
            continue
        st = out.setdefault(split_of[fid.split("_")[0]], {
            "frames": 0, "clips": set(), "rings": 0, "found": 0, "false": 0, "false_frames": 0,
            "ms": [], "board_n": 0, "board_found": 0, "board_off": [], "board_side_ok": 0,
            "unit_off": [], "lab_n": 0, "lab_found": 0, "lab_off": [], "lab_ms": [], "misses": [], "falses": []})
        frame = cv2.imread(str(OUT / "label" / "plain" / f"{fid}.png"))
        size = (frame.shape[1], frame.shape[0])
        persons = [Person(*p) for p in people.get(fid, [])]
        t0 = time.perf_counter()
        rims = rim_finder.find_rims(frame, persons)
        st["ms"].append((time.perf_counter() - t0) * 1000)
        st["frames"] += 1
        st["clips"].add(fid.split("_")[0])
        boxes = [(r.x - r.w / 2, r.y - r.h / 2, r.x + r.w / 2, r.y + r.h / 2) for r in rims]
        used: set[int] = set()
        notes = []
        for ring in x["rims"]:
            st["rings"] += 1
            dists = [(d, i) for i, b in enumerate(boxes) if i not in used
                     and (d := ring_distance(b, ring, size)) is not None]
            box = (ring["x0"], ring["y0"], ring["x1"], ring["y1"])
            if not ring.get("cut_by_frame"):
                t1 = time.perf_counter()
                lab_board = rim_finder.find_backboard(frame, box)
                st["lab_ms"].append((time.perf_counter() - t1) * 1000)
                st["lab_n"] += 1
                if lab_board:
                    st["lab_found"] += 1
                    true_off = board_offset(ring, ring["backboard_outer_x"])
                    st["lab_off"].append(board_offset(ring, board_outer(lab_board, ring["side"])) - true_off)
            if not dists:
                st["misses"].append(fid)
                notes.append("MISS")
                continue
            _d, i = min(dists)
            used.add(i)
            st["found"] += 1
            if ring.get("cut_by_frame"):
                continue
            r = rims[i]
            true_off = board_offset(ring, ring["backboard_outer_x"])
            st["board_n"] += 1
            if r.backboard:
                st["board_found"] += 1
                err = board_offset(ring, board_outer(r.backboard, ring["side"])) - true_off
                st["board_off"].append(err)
                st["unit_off"].append(err)
                mid = (r.backboard[0] + r.backboard[2]) / 2
                st["board_side_ok"] += ("left" if mid < r.x else "right") == ring["side"]
                notes.append(f"board {err:+.2f}")
            else:
                outer = r.x - r.w / 2 - ALLOW_OUT * r.w if ring["side"] == "left" else r.x + r.w / 2 + ALLOW_OUT * r.w
                st["unit_off"].append(board_offset(ring, outer) - true_off)
                notes.append("no board")
        n_false = len(rims) - len(used)
        st["false"] += n_false
        st["false_frames"] += n_false > 0
        if n_false:
            st["falses"].append(fid)
            notes.append(f"{n_false} FALSE")
        if verbose:
            print(f"  {fid:12s} {x['shot_kind']:8s} rings {len(x['rims'])} found {len(rims)}  "
                  f"{', '.join(notes)}  {st['ms'][-1]:.1f} ms")
        if draw == "all" or (draw == "fail" and ("MISS" in notes or n_false)):
            draw_rims(frame, x, rims, rim_finder.candidates(frame, persons), OUT / "rimdbg" / f"{fid}.jpg")
    return out


def print_rims_eval(name: str, st: dict) -> None:
    def f(v: float | None, spec: str = ".2f") -> str:
        return "-" if v is None else format(v, spec)

    found, rings, false = st["found"], st["rings"], st["false"]
    prec = found / (found + false) if found + false else None
    recall = found / rings if rings else None
    print(f"\n{name}: {st['frames']} frames from {len(st['clips'])} clips, {rings} labelled rings")
    print(f"  rims found      {found}/{rings}   recall {f(recall, '.3f')}")
    print(f"  false rims      {false} on {st['false_frames']} frames   precision {f(prec, '.3f')}")
    print(f"    missed: {' '.join(st['misses']) or '-'}")
    print(f"    false:  {' '.join(st['falses']) or '-'}")
    for key, label, n_key, found_key in (("board_off", "board of the found rims", "board_n", "board_found"),
                                         ("lab_off", "board of the labelled rings", "lab_n", "lab_found")):
        errs = st[key]
        abs_errs = [abs(e) for e in errs]
        ok = sum(a <= BOARD_OK for a in abs_errs)
        short = sum(e < -BOARD_OK for e in errs)
        print(f"  {label}: found {st[found_key]}/{st[n_key]}; outer-edge error (ring widths) median "
              f"{f(pct(abs_errs, 50))} p90 {f(pct(abs_errs, 90))}; within {BOARD_OK}: {ok}; "
              f"short of the board by more: {short}")
    if st["board_found"]:
        print(f"    side right on {st['board_side_ok']}/{st['board_found']} found boards")
    unit = st["unit_off"]
    print(f"  board edge used (found, else the {ALLOW_OUT} rw allowance), signed error (+ = past the board): "
          f"median {f(pct(unit, 50))} p5 {f(pct(unit, 5))} p95 {f(pct(unit, 95))}; "
          f"short by > {BOARD_OK}: {sum(e < -BOARD_OK for e in unit)}/{len(unit)}")
    ms = st["ms"]
    print(f"  speed: find_rims mean {f(sum(ms) / len(ms), '.1f')} ms, median {f(pct(ms, 50), '.1f')}, "
          f"p95 {f(pct(ms, 95), '.1f')}; find_backboard median {f(pct(st['lab_ms'], 50), '.2f')} ms")


def cmd_rims_eval(args) -> None:
    labels = read_json(OUT / "labels.json")
    if not labels:
        raise SystemExit("no labels.json")
    offs = [board_offset(r, r["backboard_outer_x"]) for x in labels for r in x["rims"]
            if not r.get("cut_by_frame") and r.get("backboard_outer_x") is not None]
    print(f"Labelled boards ({len(offs)}, not cut by the frame): (board outer edge - ring outer edge) / ring width")
    print("  " + "  ".join(f"p{q} {pct(offs, q):.2f}" for q in (5, 25, 50, 75, 95)) + f"  max {max(offs):.2f}")
    results = rims_eval(labels, args.split, use_people=not args.no_people, draw=args.draw,
                        verbose=args.verbose)
    for name in ("tune", "holdout"):
        if name in results:
            print_rims_eval(name, results[name])


# ---- measure -------------------------------------------------------------------

# The detector arms compared (framing.sources): today's detector with the rim finder, the
# basketball-trained one for everything, and that one's ball and hoop with today's people.
ARMS = {
    "coco": {"ball": "coco", "hoop": "classic", "people": "coco"},
    "ebard": {"ball": "ebard", "hoop": "ebard", "people": "ebard"},
    "mixed": {"ball": "ebard", "hoop": "ebard", "people": "coco"},
}


def cmd_measure(args) -> None:
    """Run the observation stage (sports/basketball/crop/observe.py) on every prepared clip
    with one detector arm, and write obs/<arm>/<id>.json (model.to_json, plus the seconds
    each stage took) and obs/<arm>/summary.json."""
    from statistics import median

    from sports.basketball.crop.model import to_json
    from sports.basketball.crop.observe import observe

    only = parse_only(args.only)
    meta = read_json(OUT / "meta.json")
    if not meta.get("clips"):
        raise SystemExit("no meta.json yet: run prepare first")
    sources = dict(ARMS[args.arm])
    if args.coco_imgsz:
        sources["coco_imgsz"] = args.coco_imgsz
    if args.ebard_imgsz:
        sources["ebard_imgsz"] = args.ebard_imgsz
    out_dir = OUT / "obs" / args.arm
    rows = []
    # The score bug is found once, as the app would for one video (the OCR costs 10-13 s a
    # clip), and handed to the other clips; --ocr-each finds it on every clip instead.
    bug_box = None
    for cid, entry in meta["clips"].items():
        if only and int(cid) not in only:
            continue
        timings: dict = {}
        obs = observe(OUT / entry["path"], sources, entry.get("sport_t"), timings=timings,
                      bug_box=None if args.ocr_each else bug_box)
        bug_box = bug_box or obs.bug_box
        samples = obs.samples
        doc = to_json(obs)
        doc.update({"clip": cid, "arm": args.arm, "moment_t": entry.get("sport_t"),
                    "timings": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in timings.items()}})
        write_json(out_dir / f"{cid}.json", doc)
        row = {
            "clip": cid, "frames": obs.signals.frames, "samples": len(samples),
            "dense": sum(s.dense for s in samples),
            "balls": sum(len(s.balls) for s in samples), "people": sum(len(s.people) for s in samples),
            "rims": sum(len(s.rims) for s in samples), "extra": sum(len(s.people_extra) for s in samples),
            "bug": obs.bug_box is not None, "timings": doc["timings"],
        }
        rows.append(row)
        t = row["timings"]
        detect = sum(v for k, v in t.items() if k.startswith("detect_"))
        print(f"  {cid}: {row['frames']} frames, {row['samples']} samples ({row['dense']} dense); "
              f"{row['balls']} balls, {row['people']} people, {row['rims']} rims; "
              f"total {t['total']:.2f} s = a2 {t['a2']:.2f} (detect {detect:.2f}, decode wait "
              f"{t['a2_decode_wait']:.2f}) + bug {t['bug_find']:.2f} + post {t['post']:.2f}; "
              f"a1 {t['a1']:.2f} s beside it ({1000 * t['a1_cpu'] / max(1, row['frames']):.2f} ms cpu a frame)")
    if not rows:
        return
    keys = sorted({k for r in rows for k, v in r["timings"].items() if isinstance(v, (int, float))})
    summary = {
        "arm": args.arm, "sources": rows[0]["timings"].get("sources"), "clips": len(rows),
        "frames": sum(r["frames"] for r in rows), "samples": sum(r["samples"] for r in rows),
        "seconds_total": {k: round(sum(r["timings"].get(k, 0) for r in rows), 3) for k in keys},
        "seconds_median": {k: round(median(r["timings"].get(k, 0) for r in rows), 3) for k in keys},
        "per_sample_ms": {k: round(1000 * sum(r["timings"].get(k, 0) for r in rows) / max(1, sum(r["samples"] for r in rows)), 2)
                          for k in keys if k.startswith("detect_")},
        "clips_detail": rows,
        "made": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    write_json(out_dir / "summary.json", summary)
    print(f"{args.arm}: {summary['clips']} clips, {summary['frames']} frames, {summary['samples']} samples; "
          f"total {summary['seconds_total']['total']:.1f} s (median {summary['seconds_median']['total']:.2f} s a clip)")
    print("  per stage, seconds over all clips: " + ", ".join(f"{k} {v}" for k, v in summary["seconds_total"].items()))
    print("  detection, ms a sample: " + ", ".join(f"{k} {v}" for k, v in summary["per_sample_ms"].items()))


# ---- detect-eval ---------------------------------------------------------------
#
# Checkpoint 1: the detectors alone, scored against the labelled frames, before any tracking.
# Each arm runs once per frame at EVAL_DET_CONF and every box is kept in detect_eval_raw.json,
# so the scoring below can change and be rerun with --reuse.

EVAL_OUT = OUT / "detect_eval.json"
EBARD_WEIGHTS = OUT / "ebard" / "BODD_yolov8n_0001.pt"
COCO_WEIGHTS = "yolov8n.pt"
COCO_PERSON, COCO_BALL = 0, 32
EBARD_BALL, EBARD_HOOP, EBARD_PLAYER, EBARD_REFEREE = 0, 1, 2, 3

EVAL_DET_CONF = 0.10        # every arm runs this low; the thresholds below are applied afterwards
EVAL_BALL_CONFS = (0.10, 0.20, 0.30)
EVAL_FALSE_BANDS = ((0.10, 0.20), (0.20, 0.30), (0.30, 0.50), (0.50, 1.01))
EVAL_HOOP_CONFS = (0.10, 0.25, 0.50)
EVAL_HOOP_CONF = 0.25       # the hoop threshold for the geometry, the false hoops and the stands test
EVAL_PERSON_CONF = 0.35     # people that count for the gates and the shot kind (soccer's PERSON_CONF)
EVAL_HIT_PX = 15            # a ball box hits when its centre is within max(this, half its size) of the label
EVAL_RIM_NEAR = 1.5         # a false ball within this many ring widths of the ring sits "on the rim"
EVAL_WARMUP = 3             # untimed predictions per arm before the timed ones
EVAL_FLOOR_H = (0.08, 0.36)  # floor players, counted the way shots.py counts them: this tall...
EVAL_FLOOR_REL = 0.5        # ...and at least this share of the tallest person
EVAL_STANDS_DROP = 0.25     # the stands start this far above the floor players' median feet (players.py)

# This broadcast's scoreboard, measured on the label frames (the same place in every game).
EVAL_BUG_BOX = (0.235, 0.87, 0.765, 0.985)

# The simple gates are the spec's hard rejects for a ball candidate, judged on one frame (the
# per-shot static-cell test needs a whole shot and belongs to checkpoint 2), plus a looser
# head test and a stands test, measured for the report.
GATE_BALL_REL = 0.12        # expected ball diameter, as a share of the median floor player's height
GATE_SIZE = (0.45, 2.2)     # a diameter within these multiples of the expected one
GATE_SIZE_FREE = (0.004, 0.05)  # frame heights, with fewer than 2 floor players to measure by
GATE_SHAPE = (0.6, 1.67)    # box width over height...
GATE_SHAPE_BLUR = 2.2       # ...or up to this (and its inverse) at GATE_BLUR_CONF: motion blur
GATE_BLUR_CONF = 0.3
GATE_HEAD = 0.12            # on a head: inside the top this share of a person's box
GATE_HEAD_FAN = 0.25        # the looser variant, for seated fans whose box is head and shoulders
GATE_EDGE_Y = 0.8           # in a wide shot nothing lower than this is the ball (the front rows, shoes)
GATE_HOOP_NEAR = 3.0        # the stands test spares a ball within this many hoop-box widths of a hoop

EVAL_GATES = {"s": "size", "a": "aspect", "b": "bug", "h": "head", "H": "head25", "f": "floor",
              "u": "stands", "U": "stands_off_hoop"}
EVAL_GATE_SETS = {"size": "s", "aspect": "a", "bug": "b", "head": "h", "head25": "H", "floor": "f",
                  "stands": "u", "stands_off_hoop": "U", "size+aspect+bug+head": "sabh",
                  "size+aspect+bug+head+floor": "sabhf", "size+aspect+bug": "sab",
                  "size+aspect+bug+stands_off_hoop": "sabU"}


def eval_arms(coco_size: int, ebard_sizes: list[int]) -> list[dict]:
    arms = [{"name": f"coco{coco_size}", "family": "coco", "weights": COCO_WEIGHTS, "imgsz": coco_size,
             "classes": [COCO_PERSON, COCO_BALL]}]
    arms += [{"name": f"ebard{s}", "family": "ebard", "weights": str(EBARD_WEIGHTS), "imgsz": s,
              "classes": None} for s in ebard_sizes]
    return arms


def detect_frame(model, arm: dict, frame) -> dict:
    """One arm on one frame: every box at EVAL_DET_CONF in frame fractions, and the time the
    call took (inference and reading the boxes back; the frame is already decoded)."""
    from core.gpu import torch_device
    from video.tracker import _infer_lock

    h, w = frame.shape[:2]
    t0 = time.perf_counter()
    with _infer_lock:
        results = model.predict(frame, classes=arm["classes"], conf=EVAL_DET_CONF, imgsz=arm["imgsz"],
                                device=torch_device(), verbose=False)
    r = results[0]
    xyxy = r.boxes.xyxy.cpu().tolist()
    confs = r.boxes.conf.cpu().tolist()
    classes = [int(c) for c in r.boxes.cls.cpu().tolist()]
    ms = (time.perf_counter() - t0) * 1000
    out = {"ms": round(ms, 2), "speed": {k: round(v, 2) for k, v in (r.speed or {}).items()},
           "balls": [], "people": [], "hoops": []}
    ball_cls = COCO_BALL if arm["family"] == "coco" else EBARD_BALL
    for (x0, y0, x1, y1), conf, cls in zip(xyxy, confs, classes):
        box = [round(x0 / w, 5), round(y0 / h, 5), round(x1 / w, 5), round(y1 / h, 5), round(conf, 4)]
        if cls == ball_cls:
            out["balls"].append(box)
        elif arm["family"] == "ebard" and cls == EBARD_HOOP:
            out["hoops"].append(box)
        elif (arm["family"] == "coco" and cls == COCO_PERSON) or (
                arm["family"] == "ebard" and cls in (EBARD_PLAYER, EBARD_REFEREE)):
            out["people"].append([*box, cls])
    return out


def run_detectors(arms: list[dict], frame_ids: list[str]) -> dict:
    """Every arm on every labelled frame. Frames are decoded once and handed to each arm in
    turn; each arm is warmed at its own size first, so the times are warm ones."""
    import cv2
    import torch

    from sports.soccer.ball import _model

    models = {arm["name"]: _model(arm["weights"]) for arm in arms}
    plain = OUT / "label" / "plain"
    first = cv2.imread(str(plain / f"{frame_ids[0]}.png"))
    for arm in arms:
        for _ in range(EVAL_WARMUP):
            detect_frame(models[arm["name"]], arm, first)
    found: dict = {}
    size = None
    t0 = time.time()
    for k, fid in enumerate(frame_ids):
        img = cv2.imread(str(plain / f"{fid}.png"))
        if img is None:
            print(f"  {fid}: no image, skipped")
            continue
        size = [img.shape[1], img.shape[0]]
        found[fid] = {arm["name"]: detect_frame(models[arm["name"]], arm, img) for arm in arms}
        if (k + 1) % 64 == 0:
            print(f"  {k + 1}/{len(frame_ids)} frames ({time.time() - t0:.0f} s)")
    device = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
    return {"made": time.strftime("%Y-%m-%dT%H:%M:%S"), "device": device, "size": size,
            "arms": arms, "frames": found}


# -- scoring helpers --


def round3(v: float | None) -> float | None:
    return None if v is None else round(v, 3)


def spread_stats(values: list[float]) -> dict:
    return {"n": len(values), **{f"p{q}": round3(pct(values, q)) for q in (10, 50, 90)},
            "min": round3(min(values)) if values else None, "max": round3(max(values)) if values else None}


def share(k: int, n: int) -> float | None:
    return round(k / n, 3) if n else None


def box_centre(b: list) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def floor_people(people: list) -> list:
    if not people:
        return []
    tallest = max(p[3] - p[1] for p in people)
    return [p for p in people
            if EVAL_FLOOR_H[0] <= p[3] - p[1] <= EVAL_FLOOR_H[1] and p[3] - p[1] >= EVAL_FLOOR_REL * tallest]


def player_height(people: list) -> float | None:
    """The median floor player's height, or None with fewer than 2 of them."""
    floor = floor_people(people)
    return statistics.median(p[3] - p[1] for p in floor) if len(floor) >= 2 else None


def stands_line(people: list) -> float | None:
    feet = [p[3] for p in floor_people(people)]
    return statistics.median(feet) - EVAL_STANDS_DROP if len(feet) >= 3 else None


def on_head(cx: float, cy: float, people: list, part: float) -> bool:
    return any(p[0] <= cx <= p[2] and p[1] <= cy <= p[1] + part * (p[3] - p[1]) for p in people)


def in_bug(cx: float, cy: float) -> bool:
    b = EVAL_BUG_BOX
    return b[0] <= cx <= b[2] and b[1] <= cy <= b[3]


def gate_letters(ball: list, people: list, hoops: list, wide: bool, size: tuple[int, int]) -> str:
    """The reasons one ball box can't be the ball, a letter each (EVAL_GATES), from one frame.
    `people` are the people the size, head and stands tests measure by; `hoops` the hoop boxes
    the stands test spares a ball near."""
    width, height = size
    cx, cy = box_centre(ball)
    wpx, hpx = (ball[2] - ball[0]) * width, (ball[3] - ball[1]) * height
    diameter = max(wpx, hpx) / height
    out = ""
    ph = player_height(people)
    if ph:
        if not GATE_SIZE[0] <= diameter / (GATE_BALL_REL * ph) <= GATE_SIZE[1]:
            out += "s"
    elif not GATE_SIZE_FREE[0] <= diameter <= GATE_SIZE_FREE[1]:
        out += "s"
    lo, hi = (1 / GATE_SHAPE_BLUR, GATE_SHAPE_BLUR) if ball[4] >= GATE_BLUR_CONF else GATE_SHAPE
    if not lo <= wpx / max(hpx, 1e-6) <= hi:
        out += "a"
    if in_bug(cx, cy):
        out += "b"
    if on_head(cx, cy, people, GATE_HEAD):
        out += "h"
    if on_head(cx, cy, people, GATE_HEAD_FAN):
        out += "H"
    if wide and cy > GATE_EDGE_Y:
        out += "f"
    line = stands_line(people)
    if line is not None and cy < line:
        out += "u"
        if not any(abs(cx - (h[0] + h[2]) / 2) <= GATE_HOOP_NEAR * (h[2] - h[0])
                   for h in hoops if h[4] >= EVAL_HOOP_CONF):
            out += "U"
    return out


def ball_hit(ball: list, label: dict, size: tuple[int, int]) -> bool:
    width, height = size
    cx, cy = box_centre(ball)
    half = max((ball[2] - ball[0]) * width, (ball[3] - ball[1]) * height) / 2
    dist = (((cx - label["ball_x"]) * width) ** 2 + ((cy - label["ball_y"]) * height) ** 2) ** 0.5
    return dist <= max(EVAL_HIT_PX, half)


def false_ball_place(ball: list, people: list, rims: list, size: tuple[int, int]) -> str:
    """Where a false ball sits, for the report. Judged with the COCO arm's people for every
    arm, since only COCO reports the spectators."""
    width, height = size
    cx, cy = box_centre(ball)
    if in_bug(cx, cy):
        return "bug"
    for r in rims:
        rx, ry = (r["x0"] + r["x1"]) / 2, (r["y0"] + r["y1"]) / 2
        dist = (((cx - rx) * width) ** 2 + ((cy - ry) * height) ** 2) ** 0.5
        if dist <= EVAL_RIM_NEAR * (r["x1"] - r["x0"]) * width:
            return "rim"
    if on_head(cx, cy, people, GATE_HEAD_FAN):
        return "head"
    if any(p[0] <= cx <= p[2] and p[1] <= cy <= p[3] for p in people):
        return "body"
    line = stands_line(people)
    if line is not None and cy < line:
        return "stands"
    return "other"


def hoop_hits(hoop: list, ring: dict, size: tuple[int, int]) -> bool:
    """rims-eval's match: IoU with the labelled ring at least RIM_IOU, or the centres within
    one ring width."""
    return ring_distance(tuple(hoop[:4]), ring, size) is not None


def shot_kinder():
    """A function giving the kind shots.classify gives one frame with these people, or None
    when the planning core can't be imported. No court colour is measured here, so its
    crowd rule can't fire."""
    try:
        from sports.basketball.crop import shots
        from sports.basketball.crop.model import Person, Sample, Shot
    except Exception as exc:  # the planning core is written beside this bench
        print(f"  shot kinds unavailable: {exc}")
        return None

    def kind(people: list) -> str:
        persons = [Person(x=(p[0] + p[2]) / 2, y=(p[1] + p[3]) / 2, w=p[2] - p[0], h=p[3] - p[1], conf=p[4])
                   for p in people]
        sample = Sample(i=0, frame=0, t=0.0, people=persons)
        return shots.classify(Shot(id=0, f0=0, f1=1, t0=0.0, t1=0.02), [sample], [sample]).kind

    return kind


# -- the per-arm records and their statistics --


def score_records(raw: dict, labels: dict, frame_info: dict, meta: dict) -> dict:
    """Per arm, a record per frame: the label, the split keys and the arm's boxes with their
    hit and gate flags worked out, so every statistic below is a plain count."""
    size = tuple(raw["size"])
    coco_name = next((a["name"] for a in raw["arms"] if a["family"] == "coco"), None)
    out: dict = {a["name"]: [] for a in raw["arms"]}
    for fid, by_arm in raw["frames"].items():
        lab = labels.get(fid)
        if lab is None:
            continue
        info = frame_info.get(fid, {})
        clip = meta.get("clips", {}).get(fid.split("_")[0], {})
        wide = lab["shot_kind"] == "wide"
        coco_people = ([p for p in by_arm[coco_name]["people"] if p[4] >= EVAL_PERSON_CONF]
                       if coco_name else [])
        base = {
            "fid": fid,
            "kind": lab["shot_kind"],
            "near": "near" if str(info.get("purpose", "")).startswith("sport_t") else "spread",
            "category": clip.get("category") or info.get("category") or "?",
            "visible": bool(lab["ball_visible"]),
            # 4 labels have no ball_certain; all say the ball is not visible, which is sure enough
            "certain": bool(lab.get("ball_certain", True)),
            "in_hands": bool(lab.get("ball_in_hands")),
            "rims": lab["rims"],
        }
        for arm in raw["arms"]:
            det = by_arm[arm["name"]]
            people = [p for p in det["people"] if p[4] >= EVAL_PERSON_CONF]
            ph = player_height(people)
            balls = []
            for b in det["balls"]:
                hit = base["visible"] and base["certain"] and ball_hit(b, lab, size)
                balls.append({
                    "conf": b[4], "hit": hit,
                    "gates": gate_letters(b, people, det["hoops"], wide, size),
                    "gates_coco": gate_letters(b, coco_people, det["hoops"], wide, size),
                    "place": None if hit else false_ball_place(b, coco_people, lab["rims"], size),
                    "d_px": round(max((b[2] - b[0]) * size[0], (b[3] - b[1]) * size[1]), 1),
                    "player_h": ph,
                })
            out[arm["name"]].append({**base, "balls": balls, "hoops": det["hoops"], "people": people,
                                     "people_all": det["people"], "ms": det["ms"], "speed": det["speed"]})
    return out


def kept_balls(balls: list, conf: float, drop: str = "", key: str = "gates") -> list:
    return [b for b in balls if b["conf"] >= conf and not (set(drop) & set(b[key]))]


def ball_block(recs: list, drop: str = "", key: str = "gates") -> dict:
    """Recall, top-1 and false balls over a group of frames, once the boxes carrying any of
    the gate letters in `drop` are thrown away. Recall and top-1 count the frames with a
    visible, certain ball; false balls count every frame whose label is certain."""
    eligible = [r for r in recs if r["visible"] and r["certain"]]
    certain = [r for r in recs if r["certain"]]
    out: dict = {"n_ball": len(eligible), "n_certain": len(certain)}
    for c in EVAL_BALL_CONFS:
        found = top = 0
        for r in eligible:
            kept = kept_balls(r["balls"], c, drop, key)
            found += any(b["hit"] for b in kept)
            top += bool(kept) and max(kept, key=lambda b: b["conf"])["hit"]
        out[f"recall@{c:.2f}"] = share(found, len(eligible))
        out[f"top1@{c:.2f}"] = share(top, len(eligible))
        out[f"top1_of_found@{c:.2f}"] = share(top, found)
    bands = {f"{lo:.2f}-{min(hi, 1.0):.2f}": 0 for lo, hi in EVAL_FALSE_BANDS}
    with_false = dict.fromkeys(EVAL_BALL_CONFS, 0)
    over = dict.fromkeys(EVAL_BALL_CONFS, 0)
    for r in certain:
        bad = [b for b in kept_balls(r["balls"], EVAL_DET_CONF, drop, key) if not b["hit"]]
        for lo, hi in EVAL_FALSE_BANDS:
            bands[f"{lo:.2f}-{min(hi, 1.0):.2f}"] += sum(1 for b in bad if lo <= b["conf"] < hi)
        for c in EVAL_BALL_CONFS:
            over[c] += sum(1 for b in bad if b["conf"] >= c)
            with_false[c] += any(b["conf"] >= c for b in bad)
    n = len(certain)
    out["false_per_frame_by_band"] = {band: round(k / n, 3) if n else None for band, k in bands.items()}
    for c in EVAL_BALL_CONFS:
        out[f"false_per_frame@{c:.2f}"] = round(over[c] / n, 3) if n else None
        out[f"frames_with_false@{c:.2f}"] = share(with_false[c], n)
    return out


def gate_block(recs: list, key: str = "gates") -> dict:
    """What each gate costs (true balls thrown away) and buys (false balls removed)."""
    hits = [b for r in recs if r["visible"] and r["certain"] for b in r["balls"] if b["hit"]]
    false = [b for r in recs if r["certain"] for b in r["balls"] if not b["hit"]]
    false30 = [b for b in false if b["conf"] >= 0.30]
    out = {"true_boxes": len(hits), "false_boxes": len(false), "false_boxes@0.30": len(false30)}
    for name, letters in EVAL_GATE_SETS.items():
        rej = set(letters)
        out[name] = {
            "true_lost": sum(1 for b in hits if rej & set(b[key])),
            "false_removed": share(sum(1 for b in false if rej & set(b[key])), len(false)),
            "false_removed@0.30": share(sum(1 for b in false30 if rej & set(b[key])), len(false30)),
            "after": {k: v for k, v in ball_block(recs, letters, key).items()
                      if k.startswith(("recall@", "top1@", "false_per_frame@"))},
        }
    return out


def ball_sizes(recs: list) -> dict:
    """True and false ball diameters, in pixels and in median floor-player heights, for the
    size gate's constants."""
    t_px, t_rel, f_px, f_rel = [], [], [], []
    for r in recs:
        if not r["certain"]:
            continue
        for b in r["balls"]:
            rel = (b["d_px"] / 1080) / b["player_h"] if b["player_h"] else None
            if b["hit"]:
                t_px.append(b["d_px"])
                if rel:
                    t_rel.append(rel)
            elif b["conf"] >= 0.20:
                f_px.append(b["d_px"])
                if rel:
                    f_rel.append(rel)
    return {"true_px": spread_stats(t_px), "true_per_player_h": spread_stats(t_rel),
            "false@0.20_px": spread_stats(f_px), "false@0.20_per_player_h": spread_stats(f_rel)}


def false_places(recs: list) -> dict:
    out: dict = {}
    for c in (0.10, 0.30):
        counts: dict = {}
        for r in recs:
            if not r["certain"]:
                continue
            for b in r["balls"]:
                if not b["hit"] and b["conf"] >= c:
                    counts[b["place"]] = counts.get(b["place"], 0) + 1
        out[f"@{c:.2f}"] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
    return out


def hoop_block(recs: list, size: tuple[int, int]) -> dict:
    """Hoop recall, false hoops, and the hoop box measured against the labelled ring and
    backboard, so the planner can take E-BARD's box as it comes."""
    width, height = size
    rims = [(r, ring) for r in recs for ring in r["rims"]]
    out: dict = {"n_rims": len(rims), "n_frames": len(recs)}
    for c in EVAL_HOOP_CONFS:
        found = sum(1 for r, ring in rims if any(h[4] >= c and hoop_hits(h, ring, size) for h in r["hoops"]))
        out[f"recall@{c:.2f}"] = share(found, len(rims))
        false = [sum(1 for h in r["hoops"] if h[4] >= c and not any(hoop_hits(h, ring, size) for ring in r["rims"]))
                 for r in recs]
        out[f"false_per_frame@{c:.2f}"] = round(sum(false) / len(recs), 3) if recs else None
        out[f"frames_with_false@{c:.2f}"] = share(sum(1 for k in false if k), len(recs))
    conf, w_ratio, h_ratio, dx, top, past_box, past_ring = [], [], [], [], [], [], []
    for r, ring in rims:
        hits = [h for h in r["hoops"] if h[4] >= EVAL_HOOP_CONF and hoop_hits(h, ring, size)]
        if not hits:
            continue
        h = max(hits, key=lambda b: b[4])
        rw = (ring["x1"] - ring["x0"]) * width
        bw = (h[2] - h[0]) * width
        conf.append(h[4])
        w_ratio.append(bw / rw)
        h_ratio.append((h[3] - h[1]) * height / rw)
        dx.append(((h[0] + h[2]) - (ring["x0"] + ring["x1"])) / 2 * width / rw)
        top.append((h[1] - ring["y0"]) * height / rw)
        if ring.get("cut_by_frame") or ring.get("side") not in ("left", "right"):
            continue
        outer = ring["backboard_outer_x"]
        past_box.append(((outer - h[2]) if ring["side"] == "right" else (h[0] - outer)) * width / bw)
        past_ring.append(board_offset(ring, outer))
    out[f"geometry@{EVAL_HOOP_CONF:.2f}"] = {
        "hoop_conf": spread_stats(conf),
        "box_w_over_ring_w": spread_stats(w_ratio),
        "box_h_over_ring_w": spread_stats(h_ratio),
        "box_centre_dx_in_ring_w": spread_stats(dx),
        "box_top_below_ring_top_in_ring_w": spread_stats(top),
        "backboard_outer_past_box_in_box_w": spread_stats(past_box),
        "backboard_outer_past_ring_in_ring_w": spread_stats(past_ring),
    }
    return out


def people_block(recs: list) -> dict:
    """What this arm's people tell shots.classify, frame by frame, and what it reports on
    close-ups and crowd shots."""
    by_kind: dict = {}
    confusion: dict = {}
    right = judged = 0
    for r in recs:
        hs = [p[3] - p[1] for p in r["people"]]
        tallest = max(hs) if hs else 0.0
        g = by_kind.setdefault(r["kind"], {"frames": 0, "with_people": 0, "tall": 0, "tall_at_0.10": 0,
                                           "tallest": [], "floor_n": [], "people_n": []})
        g["frames"] += 1
        g["with_people"] += bool(hs)
        g["tall"] += tallest >= EVAL_FLOOR_H[1]
        g["tall_at_0.10"] += any(p[3] - p[1] >= EVAL_FLOOR_H[1] for p in r["people_all"])
        g["tallest"].append(tallest)
        g["floor_n"].append(len(floor_people(r["people"])))
        g["people_n"].append(len(hs))
        pred = r.get("pred_kind")
        if pred is not None:
            row = confusion.setdefault(r["kind"], {})
            row[pred] = row.get(pred, 0) + 1
            judged += 1
            right += (r["kind"] in ("wide", "medium")) == (pred in ("wide", "medium"))
    kinds = {k: {"frames": g["frames"], "with_people": g["with_people"],
                 "tallest_ge_0.36": g["tall"], "tallest_ge_0.36_at_conf_0.10": g["tall_at_0.10"],
                 "tallest_median": round3(pct(g["tallest"], 50)),
                 "floor_n_median": pct(g["floor_n"], 50), "people_median": pct(g["people_n"], 50)}
             for k, g in by_kind.items()}
    return {"by_kind": kinds, "kind_confusion": confusion, "gameplay_vs_cutaway_right": share(right, judged)}


def timing_block(recs: list) -> dict:
    ms = [r["ms"] for r in recs]
    parts = {k: round3(pct([r["speed"].get(k, 0.0) for r in recs], 50))
             for k in ("preprocess", "inference", "postprocess")}
    return {"frames": len(ms), "median_ms": round3(pct(ms, 50)), "p90_ms": round3(pct(ms, 90)),
            "ultralytics_median_ms": parts}


def evaluate(raw: dict) -> dict:
    labels = {x["frame_id"]: x for x in read_json(OUT / "labels.json")}
    frame_info = {f["frame_id"]: f for f in read_json(OUT / "label" / "frames.json").get("frames", [])}
    meta = read_json(OUT / "meta.json")
    size = tuple(raw["size"])
    records = score_records(raw, labels, frame_info, meta)
    kind = shot_kinder()
    for recs in records.values():
        for r in recs:
            r["pred_kind"] = kind(r["people"]) if kind else None

    def groups(recs: list) -> dict:
        out = {"all": recs}
        for key in ("kind", "near", "category"):
            for value in sorted({r[key] for r in recs}):
                out[f"{key}:{value}"] = [r for r in recs if r[key] == value]
        out["in_hands"] = [r for r in recs if r["in_hands"]]
        out["free"] = [r for r in recs if not r["in_hands"]]
        return out

    arms_out = {}
    for arm in raw["arms"]:
        recs = records[arm["name"]]
        g = groups(recs)
        doc = {
            "weights": Path(arm["weights"]).name,
            "imgsz": arm["imgsz"],
            "timing": timing_block(recs),
            "ball": {name: ball_block(rs) for name, rs in g.items() if rs},
            "gates": {"all": gate_block(recs), "near": gate_block(g["near:near"]) if g.get("near:near") else None},
            "ball_sizes": ball_sizes(recs),
            "false_places": false_places(recs),
            "people": people_block(recs),
        }
        if arm["family"] == "ebard":
            doc["gates_with_coco_people"] = gate_block(recs, key="gates_coco")
            doc["hoop"] = {name: hoop_block(rs, size) for name, rs in g.items()
                           if rs and name not in ("in_hands", "free")}
        arms_out[arm["name"]] = doc

    # The labels' own board reach, for basket.py's allowance: no detector involved.
    reach = [board_offset(ring, ring["backboard_outer_x"]) for lab in labels.values() for ring in lab["rims"]
             if not ring.get("cut_by_frame") and ring.get("side") in ("left", "right")]
    return {
        "made": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "detections_made": raw.get("made"),
        "device": raw.get("device"),
        "frames": len(next(iter(records.values()))) if records else 0,
        "settings": {
            "det_conf": EVAL_DET_CONF, "hoop_conf": EVAL_HOOP_CONF, "person_conf": EVAL_PERSON_CONF,
            "hit_px": EVAL_HIT_PX, "hoop_iou": RIM_IOU, "bug_box": EVAL_BUG_BOX, "gate_letters": EVAL_GATES,
            "note": "people are each arm's own unless the key says coco; no court colour is measured, "
                    "so shots.classify's crowd rule can't fire on one frame here",
        },
        "labels": {"backboard_outer_past_ring_in_ring_w": spread_stats(reach)},
        "arms": arms_out,
    }


def print_eval(doc: dict) -> None:
    arms = doc["arms"]
    names = list(arms)

    def row(title: str, values: list) -> None:
        print(f"  {title:<36}" + "".join(f"{'-' if v is None else v:>18}" for v in values))

    def hoop(n: str, *keys):
        v = arms[n].get("hoop", {}).get("all")
        for k in keys:
            if not isinstance(v, dict):
                return None
            v = v.get(k)
        return v

    print(f"\nDetector evaluation on {doc['frames']} labelled frames ({doc['device']})")
    row("", names)
    for metric, title in (("recall", "ball recall"), ("top1", "top box is the ball"),
                          ("false_per_frame", "false balls a frame")):
        for c in ("0.10", "0.20", "0.30"):
            row(f"{title} @{c}", [arms[n]["ball"]["all"][f"{metric}@{c}"] for n in names])
    for g in ("size+aspect+bug+head", "size+aspect+bug", "head", "stands_off_hoop"):
        gates = [arms[n]["gates"]["all"] for n in names]
        print(f"  gates {g}")
        row("  true boxes lost", [f"{x[g]['true_lost']}/{x['true_boxes']}" for x in gates])
        row("  false removed (all, >=0.30)", [f"{x[g]['false_removed']}, {x[g]['false_removed@0.30']}"
                                              for x in gates])
        for c in ("0.10", "0.20", "0.30"):
            row(f"  recall/top1/false @{c}", [
                f"{x[g]['after'][f'recall@{c}']}/{x[g]['after'][f'top1@{c}']}/{x[g]['after'][f'false_per_frame@{c}']}"
                for x in gates])
    for split in ("near:near", "near:spread", "kind:wide", "in_hands", "free"):
        row(f"ball recall @0.20 {split}", [arms[n]["ball"].get(split, {}).get("recall@0.20") for n in names])
    for c in ("0.25", "0.50"):
        row(f"hoop recall @{c}", [hoop(n, f"recall@{c}") for n in names])
        row(f"false hoops a frame @{c}", [hoop(n, f"false_per_frame@{c}") for n in names])
    geo = f"geometry@{EVAL_HOOP_CONF:.2f}"
    row("hoop box w / ring w (p50)", [hoop(n, geo, "box_w_over_ring_w", "p50") for n in names])
    for q in ("p50", "p90", "max"):
        row(f"board past hoop box, box w ({q})",
            [hoop(n, geo, "backboard_outer_past_box_in_box_w", q) for n in names])
    row("gameplay/cutaway right (1 frame)", [arms[n]["people"]["gameplay_vs_cutaway_right"] for n in names])
    row("close-ups with a person >= 0.36", [
        f"{arms[n]['people']['by_kind'].get('closeup', {}).get('tallest_ge_0.36')}/"
        f"{arms[n]['people']['by_kind'].get('closeup', {}).get('frames')}" for n in names])
    row("ms a frame (median, warm)", [arms[n]["timing"]["median_ms"] for n in names])
    print(f"  labelled board past the ring, ring widths: {doc['labels']['backboard_outer_past_ring_in_ring_w']}")


def cmd_detect_eval(args) -> None:
    out_path = Path(args.out) if args.out else EVAL_OUT
    raw_path = out_path.with_name(out_path.stem + "_raw.json")
    if args.reuse:
        raw = read_json(raw_path)
        if not raw.get("frames"):
            raise SystemExit(f"no detections in {raw_path}: run without --reuse first")
    else:
        labels = read_json(OUT / "labels.json")
        if not labels:
            raise SystemExit("no labels.json")
        if not EBARD_WEIGHTS.exists():
            raise SystemExit(f"no E-BARD weights at {EBARD_WEIGHTS}")
        sizes = [int(s) for s in args.ebard_sizes.split(",") if s.strip()]
        arms = eval_arms(args.coco_size, sizes)
        print(f"Running {', '.join(a['name'] for a in arms)} on {len(labels)} labelled frames")
        raw = run_detectors(arms, [x["frame_id"] for x in labels])
        write_json(raw_path, raw)
        print(f"Wrote {raw_path}")
    doc = evaluate(raw)
    write_json(out_path, doc)
    print_eval(doc)
    print(f"\nWrote {out_path}")


# ---- command line --------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="cut the bench clips and write meta.json")
    p.add_argument("--job", type=int, default=DEFAULT_JOB, help="the basketball job to draw clips from")
    p.add_argument("--db", type=Path, default=DATA / "state.db")
    p.add_argument("--only", help="comma-separated clip ids to (re)do; the rest of meta.json is kept")
    p.add_argument("--workers", type=int, default=2, help="cuts running at once")
    p.add_argument("--force", action="store_true", help="cut again even when the file exists")
    p.set_defaults(func=cmd_prepare)

    f = sub.add_parser("frames", help="extract the labelling frames of the prepared clips")
    f.add_argument("--only", help="comma-separated clip ids to (re)do")
    f.add_argument("--force", action="store_true", help="write the images again even when they exist")
    f.set_defaults(func=cmd_frames)

    r = sub.add_parser("rims-eval", help="the classic rim and backboard finder against the labelled frames")
    r.add_argument("--split", choices=("all", "tune", "holdout"), default="all",
                   help="which clips to report: every third clip is held out of the tuning")
    r.add_argument("--no-people", action="store_true", help="skip the person boxes (no torso reject)")
    r.add_argument("--draw", choices=("none", "fail", "all"), default="none",
                   help="write the frames with the rings and rims drawn to rimdbg/")
    r.add_argument("--verbose", action="store_true", help="a line per frame")
    r.set_defaults(func=cmd_rims_eval)

    m = sub.add_parser("measure", help="the observation stage on every clip with one detector arm")
    m.add_argument("--arm", choices=sorted(ARMS), default="coco",
                   help="coco (+ rim finder), ebard, or mixed (E-BARD ball and hoop, COCO people)")
    m.add_argument("--only", help="comma-separated clip ids")
    m.add_argument("--coco-imgsz", type=int, help="COCO's input size (default 1280)")
    m.add_argument("--ebard-imgsz", type=int, help="E-BARD's input size (default 960)")
    m.add_argument("--ocr-each", action="store_true", help="find the score bug on every clip, not once")
    m.set_defaults(func=cmd_measure)

    d = sub.add_parser("detect-eval", help="the detectors alone against the labelled frames (checkpoint 1)")
    d.add_argument("--coco-size", type=int, default=1280, help="COCO yolov8n's input size")
    d.add_argument("--ebard-sizes", default="704,960,1280", help="comma-separated E-BARD input sizes")
    d.add_argument("--reuse", action="store_true", help="score the saved boxes again, without the detectors")
    d.add_argument("--out", help=f"the result file (default {EVAL_OUT.relative_to(ROOT).as_posix()}); "
                                 "the boxes go beside it as <name>_raw.json")
    d.set_defaults(func=cmd_detect_eval)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
