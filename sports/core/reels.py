"""Story reels from a match's clips, as Spiideo makes them: the match recap,
and a reel per team and per player, in match order.

Built from the clips already rendered (9:16, or 16:9 with Longform), so a
reel costs a join, not a render: the clips are joined with the concat
demuxer and no re-encode (they come out of the same renderer in the same
format), each one's end card trimmed off and one added at the end of the
reel. What each reel holds:
- the recap: every named moment, the goals and cards and saves, in order;
- a team's reel: the moments the scoreboard or the match events give to it;
- a player's reel: the moments the match events name them in, and for a
  name typed in Teams or players, the moments whose commentary says it.
A reel needs two moments or more: one is the clip itself.
"""

import json
import math
import re
import subprocess
import unicodedata
import zlib
from fractions import Fraction
from pathlib import Path
from typing import NamedTuple

KINDS = ("recap", "teams", "players")
MIN_PARTS = 2


class Reel(NamedTuple):
    kind: str       # recap, team or player
    subject: str    # the team or the player; "" for the recap
    title: str
    parts: list     # the clips, in match order


def _scores(clip) -> dict:
    return getattr(clip.candidate, "subscores", None) or {}


def _named(clip) -> bool:
    s = _scores(clip)
    return bool(s.get("sport_event")) and s.get("sport_event") != "big_moment" and not s.get("sport_replay")


def _at(clip) -> float:
    return _scores(clip).get("sport_t", clip.candidate.start)


def _fold(text: str) -> str:
    """Lower case with the accents off: "Joël" typed is "Joel" heard."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)).casefold()


def names(typed: str) -> list[str]:
    """The names in Teams or players: split at commas, "and", "&", "/" or
    "vs" ("Player A, Player B & Player C")."""
    parts = re.split(r"[,;/&]|\s(?:and|vs?\.?)\s", typed or "", flags=re.IGNORECASE)
    out: list[str] = []
    for part in parts:
        name = " ".join(part.split())
        if len(name) >= 2 and _fold(name) not in {_fold(n) for n in out}:
            out.append(name)
    return out


def says(text: str, name: str) -> bool:
    """Whether the commentary says the name: all of it, or its last word
    when that's long enough to be the surname commentators use. Whole words
    only, accents and case aside."""
    words = _fold(name).split()
    if not words:
        return False
    heard = _fold(text)
    options = {" ".join(words)} | ({words[-1]} if len(words) > 1 and len(words[-1]) >= 4 else set())
    return any(re.search(rf"(?<!\w){re.escape(o)}(?!\w)", heard) for o in options)


def _a_team(name: str, codes: set[str]) -> bool:
    """Whether a typed name is a team the score box read: its code ("HOM"),
    or a name starting with it ("Home United")."""
    letters = re.sub(r"[^a-z]", "", _fold(name))
    return _fold(name) in codes or (len(letters) >= 3 and letters[:3] in codes)


def plan(clips: list, asked, score: str = "", typed: str = "", said=None) -> list[Reel]:
    """Each reel asked for that has enough moments. `clips` are the run's
    rendered clips (RenderedClip); `typed` is Teams or players, and
    `said(clip)` the commentary in a clip's window, when there is a
    transcript."""
    asked = set(asked or [])
    moments = sorted((c for c in clips if _named(c)), key=_at)
    out: list[Reel] = []
    if "recap" in asked and len(moments) >= MIN_PARTS:
        out.append(Reel("recap", "", f"{score}: match recap" if score else "Match recap", moments))
    if "teams" in asked:
        teams: dict[str, list] = {}
        for c in moments:
            if _scores(c).get("sport_team"):
                teams.setdefault(_scores(c)["sport_team"], []).append(c)
        for team, parts in teams.items():
            if len(parts) >= MIN_PARTS:
                out.append(Reel("team", team, f"{team}: the match", parts))
    if "players" in asked:
        players: dict[str, list] = {}
        for c in moments:
            if _scores(c).get("sport_player"):
                players.setdefault(_scores(c)["sport_player"], []).append(c)
        # A name typed in Teams or players: the moments whose commentary says
        # it. A team the score box read is left to its team reel.
        codes = {_fold(_scores(c)["sport_team"]) for c in moments if _scores(c).get("sport_team")}
        for name in names(typed) if said is not None else []:
            if _a_team(name, codes):
                continue
            heard = [c for c in moments if says(said(c), name)]
            if heard:
                key = next((k for k in players if _fold(k) == _fold(name)), name)
                have = players.setdefault(key, [])
                have.extend(c for c in heard if c not in have)
                have.sort(key=_at)
        most = max((len(p) for p in players.values()), default=0)
        for player, parts in players.items():
            if len(parts) >= MIN_PARTS:
                # The most moments, and more than anyone else: the stand-in
                # for a player of the match, and said to be only that.
                top = len(parts) == most and most >= 3 and sum(len(p) == most for p in players.values()) == 1
                out.append(Reel("player", player, f"{player}: {'the most moments' if top else 'the match'}", parts))
    return out


def file_name(reel: Reel) -> str:
    """One file per reel, whatever its length, so a re-run writes the same
    one: reel_recap.mp4, reel_team_hom_1a2b.mp4. The hash keeps two names
    that read alike apart; the short name keeps the path short."""
    if not reel.subject:
        return f"reel_{reel.kind}.mp4"
    slug = re.sub(r"[^a-z0-9]+", "-", _fold(reel.subject)).strip("-")[:16].strip("-")
    tag = format(zlib.crc32(reel.subject.encode("utf-8")) & 0xFFFF, "04x")
    return f"reel_{reel.kind}_{slug}_{tag}.mp4" if slug else f"reel_{reel.kind}_{tag}.mp4"


def chapters(parts: list, durations: list[float]) -> str:
    """"0:00 Goal 18' HOM" per moment, for the reel's description."""
    lines, at = [], 0.0
    for clip, dur in zip(parts, durations):
        s = _scores(clip)
        when = f" {s['sport_minute']}'" if s.get("sport_minute") is not None else ""
        who = s.get("sport_team") or s.get("sport_player") or ""
        lines.append(f"{int(at) // 60}:{int(at) % 60:02d} {s.get('sport_label', 'Moment')}{when}{(' ' + who) if who else ''}")
        at += dur
    return "\n".join(lines)


def duration(path) -> float:
    from core.binaries import ffprobe

    out = subprocess.run([ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def lengths(paths: list, trims: list[float] | None = None) -> list[float]:
    """Each clip's length in a reel: its own, less the `trims[i]` seconds
    (its end card, when it has one) left off."""
    return [_cut(p, trim)[0] for p, trim in zip(paths, trims or [0.0] * len(paths))]


# How far from `trim` seconds before the video's end the card's keyframe may
# be. A card is 2.9 s rounded to its frames and can lose its last one to its
# sound (86 frames at 29.97 fps, 173 at 59.94): a frame or two, never 0.1 s.
CARD_SLACK = Fraction(1, 10)


def _cut(path, trim: float) -> tuple[float, str | None]:
    """(the length kept, the listing's outpoint or None) for a clip with its
    last `trim` seconds, its end card, left off.

    On a whole frame: the cut is the time the clip's last frame ends,
    exactly (_clip_end), and the next clip starts there. The concat demuxer
    ends a file at the first packet, of any stream, whose decoding time
    reaches the outpoint, so every frame of the clip is in and none of the
    card. (When the card follows the clip at once, its first sound packet,
    the encoder's 1024 samples of silence just before it, still goes in, as
    it always did.)

    The cut used to be the file's length less `trim`, to the millisecond.
    That is a frame or two off: a clip's sound can run past its last frame,
    and the card is 2.9 s only to its nearest frame. So a reel could show a
    frame of the card at a seam, or hold a frame too long, or overlap the
    next clip's first frame by a fraction of a millisecond.

    When the card can't be found (a clip whose card was skipped) or the file
    can't be read, the cut is made as before.
    """
    whole = duration(path)
    if not trim:
        return whole, None
    end = _clip_end(path, trim)
    if end is None:
        keep = max(0.5, whole - trim)
        return keep, f"{keep:.3f}"
    # Down to the microsecond the listing is read in, so the cut is never
    # past the end of the clip's last frame.
    us = math.floor(end * 1_000_000)
    return us / 1_000_000, f"{us // 1_000_000}.{us % 1_000_000:06d}"


def _clip_end(path, trim: float) -> Fraction | None:
    """When the clip's last frame ends in `path`, before its end card,
    exactly. None when the card can't be found or the packets can't be read.

    The card was joined on without re-encoding, so it starts on a keyframe:
    the latest one within CARD_SLACK of `trim` seconds before the video
    ends. The clip's last frame is the one shown before it, and it ends one
    frame step later or where the card starts, whichever is sooner. (A clip
    whose sound runs past its last frame has its card joined on after the
    sound, so a little after its last frame ends.)"""
    from core.binaries import ffprobe

    try:
        r = subprocess.run([ffprobe(), "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=time_base:packet=pts,duration,flags",
                            "-of", "json", str(path)], capture_output=True, text=True, timeout=120)
        data = json.loads(r.stdout)
        base = Fraction(data["streams"][0]["time_base"])
        packets = [(int(p["pts"]), int(p.get("duration") or 0), "K" in str(p.get("flags", "")))
                   for p in data.get("packets", []) if isinstance(p.get("pts"), int)]
    except (OSError, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError, subprocess.SubprocessError):
        return None
    keys = [pts * base for pts, _d, key in packets if key]
    if not keys:
        return None
    should = max(pts + dur for pts, dur, _k in packets) * base - Fraction(trim)
    # Not the file's last keyframe: a clip has keyframes of its own (at its
    # cuts), and a card could have one more than its first. Nor the nearest:
    # a card a frame short puts `should` a frame before it, where the clip's
    # own last frame can be a keyframe (a cut on it, or a periodic one). The
    # latest near it is the card's first: the card is libx264's, which makes
    # no keyframe within 25 frames of the one before, and the shipped cards
    # have only the one.
    near = [key for key in keys if abs(key - should) <= CARD_SLACK]
    if not near:
        return None
    card = max(near)
    shown = sorted(pts * base for pts, _d, _k in packets if pts * base < card)
    if not shown:
        return None                     # nothing of the clip before it
    step = shown[-1] - shown[-2] if len(shown) > 1 else 0
    return min(card, shown[-1] + step) if step > 0 else card


def join(paths: list, out: Path, trims: list[float] | None = None) -> list[float]:
    """The clips joined into `out` without re-encoding, each with its last
    `trims[i]` seconds (its end card) left off, cut on a whole frame
    (_cut). Returns each part's length as it went in."""
    from core.paths import discard
    from video.encoding import concat

    out.parent.mkdir(parents=True, exist_ok=True)
    listing = out.with_suffix(".txt")
    trims = trims or [0.0] * len(paths)
    rows = []
    kept = []
    for p, trim in zip(paths, trims):
        keep, outpoint = _cut(p, trim)
        kept.append(keep)
        rows.append(f"file '{Path(p).resolve().as_posix()}'")
        if outpoint is not None:
            rows.append(f"outpoint {outpoint}")
    listing.write_text("\n".join(rows) + "\n", encoding="utf-8")
    try:
        # The end card's join, so the reel's video starts at 0.
        r = concat(listing, out)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(r.stderr.strip()[-300:] or "the join failed")
    finally:
        discard(listing)
    return kept
