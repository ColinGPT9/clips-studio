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
from multilingual.metadata import translate_metadata, write_post_file
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
    burn: bool = False,
    dub: bool = False,          # also speak the translation over the clip
    voices_dir: Path | None = None,
    voice_choice: dict | None = None,  # {language: voice id}
    post: dict | None = None,   # {"title", "description", "hashtags"} to translate
    want_subtitles: bool = False,  # .srt/.vtt files beside the video
    want_post: bool = False,       # .txt with the post text
    clip_row=None,
    config: dict | None = None,
    data_dir: Path | None = None,
) -> list[str]:
    """Write subtitle files for each language. Returns the paths written.

    With burn=True each language also gets its own video with the captions
    painted in, for platforms that don't read subtitle files (TikTok,
    Reels, Shorts).

    A language that fails is skipped with a message — the others still get
    written, so one bad translation never costs the whole batch."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    # One caption-free re-render serves every language's burn, and is also
    # the video the dub is laid over.
    base = None
    caption_style = None
    if (burn or dub) and clip_row is not None and config is not None and data_dir is not None:
        import json as _json

        from multilingual import burn as burner

        opts = _json.loads(clip_row["render_opts"]) if clip_row["render_opts"] else {}
        caption_style = opts.get("caption_style")
        try:
            print("      Rendering a caption-free base for burned languages…")
            base = burner.clean_base(clip_row, config, data_dir, out_dir / ".ml_work")
        except Exception as e:
            print(f"      (could not build the caption-free base: {e})")
        if base is None and clip_path is not None and clip_path.exists():
            # No source on disk: burn onto the clip as it is. It may already
            # carry its original captions, so say so rather than surprise them.
            print("      (source video is gone — burning over the existing clip)")
            base = clip_path

    if clip_path is not None and clip_path.exists():
        dest = out_dir / f"{stem}{clip_path.suffix}"
        if dest.resolve() != clip_path.resolve():
            shutil.copy2(clip_path, dest)
        written.append(str(dest))

    # The original language ships as a track too — a viewer whose player
    # is set to it should get captions, not nothing.
    if source_language and is_supported(source_language):
        if want_subtitles:
            written.append(str(write_srt(lines, out_dir / f"{stem}.{source_language}.srt")))
            written.append(str(write_vtt(lines, out_dir / f"{stem}.{source_language}.vtt")))
        if want_post and post is not None:
            written.append(str(write_post_file(post, out_dir / f"{stem}.{source_language}.txt")))

    total = len([c for c in languages if c != source_language])
    done = 0
    for code in languages:
        if code == source_language or not is_supported(code):
            continue
        try:
            if on_progress:
                on_progress(f"Translating to {english_name(code)}", done, total)
            translated = translate_lines(lines, code, llm, terms=terms)
            if want_subtitles:
                written.append(str(write_srt(translated, out_dir / f"{stem}.{code}.srt")))
                written.append(str(write_vtt(translated, out_dir / f"{stem}.{code}.vtt")))
                print(f"      Subtitles written: {english_name(code)}")
            if want_post and post is not None:
                meta = translate_metadata(
                    post.get("title", ""), post.get("description", ""),
                    post.get("hashtags", []), code, llm, terms=terms,
                )
                written.append(str(write_post_file(meta, out_dir / f"{stem}.{code}.txt")))
            burned = None
            if burn and base is not None:
                from multilingual import burn as burner

                burned = burner.burn(
                    base, translated, code, out_dir / f"{stem}.{code}.mp4",
                    caption_style, config or {},
                )
                if burned is not None:
                    written.append(str(burned))
                    print(f"      Video written: {english_name(code)}")
            if dub and base is not None and voices_dir is not None:
                from multilingual import dub as dubber

                # Dub over the burned version when there is one, so the
                # viewer gets translated captions AND translated speech.
                source = burned or base
                spoken = dubber.dub(
                    translated, code, source,
                    out_dir / f"{stem}.{code}.dubbed.mp4",
                    voices_dir, out_dir / ".ml_work",
                    voice_id=(voice_choice or {}).get(code),
                )
                if spoken is not None:
                    written.append(str(spoken))
                    print(f"      Dubbed audio written: {english_name(code)}")
                elif not dubber.supported(code):
                    print(f"      ({english_name(code)} has no voice available — subtitles only)")
        except Exception as e:  # one language failing must not stop the rest
            print(f"      ({english_name(code)} failed: {e})")
        done += 1
    if on_progress:
        on_progress("Done", total, total)

    # The caption-free base was scratch, not an output.
    work = out_dir / ".ml_work"
    if base is not None and work in base.parents:
        shutil.rmtree(work, ignore_errors=True)
    return written
