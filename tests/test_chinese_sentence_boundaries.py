"""Chinese transcript punctuation must participate in clip boundary repair.

These are hand-written transcript fixtures, not ASR or model-quality claims.
They exercise the same deterministic boundary logic as English clips.
"""

import pytest

from analysis.highlights import _ends_sentence, _fit_to_segments
from core.models import ClipCandidate, Segment


@pytest.mark.parametrize("text", [
    "完成了。", "真的吗？", "成功了！", "还在思考……",
    "他说：‘完成了。’", "他说：“完成了。”", "他说：「完成了。」",
    "他说：『完成了。』", "（完成了。）", "【完成了。】",
    "《完成了。》", "〈完成了。〉", "（他说：「完成了。」）",
    "完成了。  ",
    "Done.", "Really?", "Yes!", "Thinking…", 'He said "stop."',
    "(Done.)", "[Done.]", "«Done.»", "Done.’",
])
def test_recognizes_sentence_ends_and_closing_punctuation(text):
    assert _ends_sentence(Segment(0, 1, text))


@pytest.mark.parametrize("text", [
    "", "   ", "还有后续", "继续，", "说明：", "还有；", "甲、",
    "continued,", "explanation:", "continued;", "a value of 3.14",
    "他说：「还没说完」", "（没有句末标点）", "。后面还没结束",
])
def test_does_not_treat_nonterminal_punctuation_as_a_sentence_end(text):
    assert not _ends_sentence(Segment(0, 1, text))


def test_chinese_clip_reaches_the_completion_of_its_sentence():
    segments = [
        Segment(0, 20, "今天介绍的方法只有在"),
        Segment(20, 30, "素材已经审核时才可以使用。"),
        Segment(30, 40, "下一个话题完全不同。"),
    ]
    clip = _fit_to_segments(ClipCandidate(0, 20, 80), segments, 10, 60)

    assert (clip.start, clip.end) == (0, 30)


def test_complete_chinese_sentence_does_not_absorb_an_unrelated_english_one():
    segments = [
        Segment(0, 20, "这个观点已经讲完了。"),
        Segment(20, 30, "This is an unrelated English topic."),
    ]
    clip = _fit_to_segments(ClipCandidate(0, 20, 80), segments, 10, 60)

    assert (clip.start, clip.end) == (0, 20)


def test_chinese_clip_falls_back_to_a_complete_sentence_within_the_cap():
    segments = [
        Segment(0, 10, "第一句完整。"),
        Segment(10, 20, "第二句完整。"),
        Segment(20, 30, "但第三句话还没有"),
        Segment(30, 45, "讲完。"),
    ]
    clip = _fit_to_segments(ClipCandidate(0, 30, 80), segments, 10, 30)

    assert (clip.start, clip.end) == (0, 20)


def test_chinese_clip_skips_a_previous_sentence_fragment_at_the_start():
    segments = [
        Segment(0, 5, "前一条消息还没有"),
        Segment(5, 10, "结束。"),
        Segment(10, 20, "新的独立话题。"),
        Segment(20, 30, "本条观点完成。"),
    ]
    clip = _fit_to_segments(ClipCandidate(5, 30, 80), segments, 10, 60)

    assert (clip.start, clip.end) == (10, 30)
