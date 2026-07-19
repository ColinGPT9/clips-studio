"""Turn one finished clip into subtitle tracks for several languages.

Input is what the clip already has — its caption lines and, optionally, the
video file to copy alongside them. Output is new files only:

    my-clip.mp4          (copied when asked)
    my-clip.es.srt       my-clip.es.vtt
    my-clip.fr.srt       my-clip.fr.vtt

That naming is what YouTube expects for per-language caption uploads, so a
creator picks the files up and the platform shows each viewer their own
language. Nothing about the clip itself changes.
"""

import shutil
from pathlib import Path

from multilingual.languages import english_name, is_supported
from multilingual.subtitles import write_srt, write_vtt
from multilingual.translate import translate_lines


def publish(
    lines: list[dict],
    languages: list[str],
    out_dir: Path,
    stem: str,
    llm,
    terms: list[str] | None = None,
    clip_path: Path | None = None,
    source_language: str = "en",
    on_progress=None,
) -> list[str]:
    """Write subtitle files for each language. Returns the paths written.

    A language that fails is skipped with a message — the others still get
    written, so one bad translation never costs the whole batch."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    if clip_path is not None and clip_path.exists():
        dest = out_dir / f"{stem}{clip_path.suffix}"
        if dest.resolve() != clip_path.resolve():
            shutil.copy2(clip_path, dest)
        written.append(str(dest))

    # The original language ships as a track too — a viewer whose player
    # is set to it should get captions, not nothing.
    if source_language and is_supported(source_language):
        written.append(str(write_srt(lines, out_dir / f"{stem}.{source_language}.srt")))
        written.append(str(write_vtt(lines, out_dir / f"{stem}.{source_language}.vtt")))

    total = len([c for c in languages if c != source_language])
    done = 0
    for code in languages:
        if code == source_language or not is_supported(code):
            continue
        try:
            if on_progress:
                on_progress(f"Translating to {english_name(code)}", done, total)
            translated = translate_lines(lines, code, llm, terms=terms)
            written.append(str(write_srt(translated, out_dir / f"{stem}.{code}.srt")))
            written.append(str(write_vtt(translated, out_dir / f"{stem}.{code}.vtt")))
            print(f"      Subtitles written: {english_name(code)}")
        except Exception as e:  # one language failing must not stop the rest
            print(f"      ({english_name(code)} failed: {e})")
        done += 1
    if on_progress:
        on_progress("Done", total, total)
    return written
