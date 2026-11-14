"""The captions file name inside FFmpeg's `subtitles=` filter.

Every renderer hands the filter a bare file name (with cwd set to the
captions' folder, to dodge Windows drive-letter escaping). The name was put
into the filtergraph as it was, so a clip whose file name carried a comma,
a quote, a bracket or a semicolon broke the graph: a comma reads as the next
filter, and the burn failed with a parse error. Seen on a translated burn,
whose output is named after the clip's title.

FFmpeg needs two levels of escaping (doc: "Notes on filtergraph escaping"):
the option value (\\ ' :) and then the filtergraph (\\ ' [ ] , ;).
"""

import subprocess
from pathlib import Path

import pytest

from core.binaries import ffmpeg
from video.filters import subtitles_filter

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 240

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, BackColour, Bold, Italic, Alignment, MarginL, MarginR, MarginV
Style: Default,Arial,20,&H00FFFFFF,&H00000000,0,0,2,10,10,10

[Events]
Format: Layer, Start, End, Style, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,ola
"""


def test_escapes_both_filter_levels():
    assert subtitles_filter("Corte, do 'Paulinho' [GTA]; parte 1: final.ass") == \
        r"subtitles=Corte\, do \\\'Paulinho\\\' \[GTA\]\; parte 1\\: final.ass"


def test_plain_name_unchanged():
    assert subtitles_filter("clip_12.ass") == "subtitles=clip_12.ass"


def test_ffmpeg_burns_a_title_with_commas_quotes_and_brackets(tmp_path):
    binary = ffmpeg()
    if not Path(binary).is_file():
        pytest.skip("FFmpeg is not installed here")
    name = "Corte, do 'Paulinho' [GTA]; parte 1.ass"
    (tmp_path / name).write_text(ASS, encoding="utf-8")
    result = subprocess.run(
        [binary, "-v", "error", "-f", "lavfi", "-i", "color=black:s=320x240:d=1",
         "-vf", subtitles_filter(name), "-f", "null", "-"],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr[-500:]
