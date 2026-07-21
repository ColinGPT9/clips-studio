"""Podcast clips — a SEPARATE, opt-in render path for multi-cam footage.

A podcast is nothing like a just-chatting stream. It has hard CUTS between
camera angles and several people on screen. Subject tracking is actively
wrong here: it chases whoever is loudest (a laugh beats a quiet sentence),
and it tries to follow a person straight across a cut where they jump to a
different position — which is most of the jitter. So this path tracks NO
one. It shows the podcast editor's own framed shot, letterboxed onto a
blurred vertical backdrop: steady, cut-safe, everyone in view.

Opt-in only (the Podcast toggle). When it's off, this module is never
imported and the stream tracking path is byte-for-byte unchanged. Nothing
here imports the tracker, so it cannot affect how normal clips track.

Gentle panning to the active speaker is deliberately NOT done: knowing who
is talking (vs laughing) needs audio-visual speaker ID, which is exactly
what mis-fired and panned to the louder laugher. Stable letterbox never
picks the wrong person. If panning is added later it lives HERE, still
isolated from the stream path.
"""

from pathlib import Path


def render_clip(
    intermediate: Path,
    output_path: Path,
    ass_path: Path | None,
    vf_extra: str = "",
    normalize: bool = True,
) -> None:
    """Render one podcast clip: the whole 16:9 shot fit into 1080 wide,
    centred on a heavily blurred backdrop, captions burned on top. No crop
    to a subject, no tracking.

    Reuses the existing letterbox renderer with region=None (the full frame)
    — the same output the manual 'Letterbox' layout produces, chosen here
    automatically for the whole video instead of per clip."""
    from video.cropper import render_vertical

    render_vertical(
        intermediate,
        {"mode": "fit_blur", "region": None},  # region None = show the ENTIRE frame
        output_path,
        ass_path=ass_path,
        vf_extra=vf_extra,
        normalize=normalize,
    )
