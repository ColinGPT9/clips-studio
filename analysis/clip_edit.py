"""Conversational clip editing.

Turns a plain-language request ("the crop is too far left", "make it 5
seconds longer", "the caption says 'gost', it should be 'ghost'") into the
app's structured edit controls, using the same local LLM as the rest of the
pipeline. Never raises into the request path — on any LLM trouble it returns
a friendly no-op reply.
"""

import json
import re
from pathlib import Path

from llm.base import LLMBackend

PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "edit_clip.txt"

_CROP_MODES = {"track", "center", "bias_left", "bias_right"}
_POSITIONS = {"bottom", "middle", "top"}

MIN_DURATION = 5.0
MAX_DURATION = 180.0  # matches the 60s+ monetization mode's upper bound


def interpret_edit(
    message: str,
    *,
    clip_state: dict,
    caption_lines: list[dict],
    source_duration: float,
    llm: LLMBackend,
) -> dict:
    """Returns:
      {
        "reply": str,               # what to show the user in chat
        "needs_render": bool,       # whether a re-render job should be queued
        "start": float | None,      # new clip start in the source (if changed)
        "end": float | None,        # new clip end (if changed)
        "render_opts": dict,        # crop / captions / caption_style / caption_lines
      }
    """
    captions_text = "\n".join(
        f'{i}: [{l["start"]:.1f}-{l["end"]:.1f}] {l["text"]}' for i, l in enumerate(caption_lines)
    ) or "(no captions)"

    prompt = (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{state}", json.dumps(clip_state, indent=2))
        .replace("{captions}", captions_text)
        .replace("{source_duration}", f"{source_duration:.1f}")
        .replace("{message}", message.strip())
    )

    try:
        raw = llm.generate(prompt, json_mode=True)
        data = _parse(raw)
    except Exception:
        data = None

    if data is None:
        return {
            "reply": "Sorry, I couldn't work that out. Try things like "
            '"make it 5 seconds longer", "center the crop", "yellow captions", '
            'or "caption 3 should say ghost, not gost".',
            "needs_render": False,
            "start": None,
            "end": None,
            "render_opts": {},
        }

    return _apply(data, clip_state, caption_lines, source_duration)


def _apply(
    data: dict, clip_state: dict, caption_lines: list[dict], source_duration: float
) -> dict:
    render_opts: dict = {}
    start = float(clip_state.get("start", 0.0))
    end = float(clip_state.get("end", 0.0))
    new_start: float | None = None
    new_end: float | None = None

    # ---- timestamps -----------------------------------------------------
    if _is_number(data.get("start")) or _is_number(data.get("end")):
        s = float(data["start"]) if _is_number(data.get("start")) else start
        e = float(data["end"]) if _is_number(data.get("end")) else end
        s, e = _clamp_window(s, e, source_duration)
        if abs(s - start) > 0.05:
            new_start = s
        if abs(e - end) > 0.05:
            new_end = e

    # ---- crop -----------------------------------------------------------
    if isinstance(data.get("crop"), str) and data["crop"] in _CROP_MODES:
        render_opts["crop"] = data["crop"]

    # ---- color filter ----------------------------------------------------
    filt = data.get("filter")
    if isinstance(filt, str):
        from video.filters import is_valid

        if is_valid(filt):
            render_opts["filter"] = filt

    # ---- manual picture adjustments ---------------------------------------
    adjust = data.get("adjust")
    if isinstance(adjust, dict):
        from video.filters import ADJUST_RANGES

        clean_adjust = {}
        for key, (lo, hi, _default) in ADJUST_RANGES.items():
            if _is_number(adjust.get(key)):
                clean_adjust[key] = max(lo, min(hi, float(adjust[key])))
        if clean_adjust:
            render_opts["adjust"] = clean_adjust

    # ---- captions on/off ------------------------------------------------
    if isinstance(data.get("captions"), bool):
        render_opts["captions"] = data["captions"]

    # ---- caption style ---------------------------------------------------
    if isinstance(data.get("caption_style"), dict):
        clean = _clean_caption_style(data["caption_style"])
        if clean:
            render_opts["caption_style"] = clean

    # ---- caption text corrections ----------------------------------------
    fixes = data.get("caption_fixes")
    if isinstance(fixes, list) and fixes and caption_lines:
        fixed = [dict(l) for l in caption_lines]
        applied = False
        for fix in fixes:
            if not isinstance(fix, dict):
                continue
            idx = fix.get("line")
            text = fix.get("text")
            if _is_number(idx) and isinstance(text, str) and 0 <= int(idx) < len(fixed):
                fixed[int(idx)]["text"] = text.strip()
                applied = True
        if applied:
            # Timestamp edits would invalidate line indexes/times — caption
            # text fixes only apply when the window is unchanged.
            if new_start is None and new_end is None:
                render_opts["caption_lines"] = fixed

    needs_render = bool(
        data.get("needs_render", True)
        and (new_start is not None or new_end is not None or render_opts)
    )

    reply = str(data.get("reply") or "").strip()
    if not reply:
        reply = "Done — re-rendering the clip now." if needs_render else "I didn't change anything."

    return {
        "reply": reply,
        "needs_render": needs_render,
        "start": new_start,
        "end": new_end,
        "render_opts": render_opts,
    }


def _clean_caption_style(style: dict) -> dict:
    out: dict = {}
    if _is_number(style.get("font_size")):
        out["font_size"] = int(max(40, min(140, float(style["font_size"]))))
    color = style.get("color")
    if isinstance(color, str) and re.fullmatch(r"#?[0-9a-fA-F]{6}", color.strip()):
        out["color"] = "#" + color.strip().lstrip("#").upper()
    if isinstance(style.get("position"), str) and style["position"] in _POSITIONS:
        out["position"] = style["position"]
    font = style.get("font")
    if isinstance(font, str):
        from video.captions import FONTS

        match = next((f for f in FONTS if f.lower() == font.strip().lower()), None)
        if match:
            out["font"] = match
    if _is_number(style.get("words_per_caption")):
        out["words_per_caption"] = int(max(1, min(6, float(style["words_per_caption"]))))
    if isinstance(style.get("uppercase"), bool):
        out["uppercase"] = style["uppercase"]
    return out


def _clamp_window(start: float, end: float, source_duration: float) -> tuple[float, float]:
    start = max(0.0, start)
    if source_duration > 0:
        end = min(source_duration, end)
        start = min(start, max(0.0, source_duration - MIN_DURATION))
    if end - start < MIN_DURATION:
        end = start + MIN_DURATION
        if source_duration > 0:
            end = min(end, source_duration)
    if end - start > MAX_DURATION:
        end = start + MAX_DURATION
    return round(start, 2), round(end, 2)


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _parse(raw: str) -> dict | None:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        data = json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
