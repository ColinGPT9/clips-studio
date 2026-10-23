"""Clip selection 2 (scoring.selection: 2): where a clip starts.

_fit_to_segments starts a clip on a fresh thought by moving its start INWARD
past every segment that follows one without a full stop. Whisper splits on
pauses, not grammar, so a line before a long pause often has no full stop,
and the move then skips the very line the clip was picked for: a payoff line,
or the line its hook quotes. With selection 2 the move never passes the
segment the pick starts in, nor the one its hook is quoted from, and a signal
window (a reaction the audio or picture found) gets a few seconds of complete
sentences in front of it. The end rules are untouched.

Those lines can begin mid-sentence themselves: "he came back holding a frying
pan." right after "So I asked my brother" and "to grab the lantern and". With
selection 2 a start on a line that runs on from the one before (no sentence
end, a pause of at most 2 s) goes back to the line its sentence starts on,
when that is at most 8 s earlier and the clip stays within max_duration;
otherwise the start stays where it was. The sentence ends are the ones the
end rules read, Chinese full-width punctuation included, and marks they don't
read: the danda (Hindi, Bengali), the Arabic full stop and question mark
(Urdu, Arabic), and the full-width and half-width full stops (Chinese,
Japanese). In a transcript with no sentence end at all no start moves.

Synthetic transcripts only. CI-light: analysis.highlights and core.models.
"""

import hashlib
import json
import random

import pytest

from analysis import highlights
from analysis.highlights import _fit_to_segments
from core.models import ClipCandidate, Segment

MIN, MAX = 10.0, 60.0


def span(c):
    return (c.start, c.end)


def payoff_after_a_pause():
    """An unpunctuated line, a 5 s pause, then the payoff line the pick starts on."""
    return [
        Segment(70.0, 75.0, "so we pulled over by the old bridge"),
        Segment(80.0, 84.0, "And that is when the tyre just exploded."),
        Segment(84.5, 90.0, "We had no spare and no signal out there."),
        Segment(90.5, 96.0, "So we sat on the roof and laughed about it."),
        Segment(96.5, 103.0, "Honestly it was the best night of the whole trip."),
        Segment(103.5, 110.0, "Then a farmer came by with a tractor."),
    ]


def payoff_pick():
    return ClipCandidate(start=80.0, end=100.0, score=72, hook="that is when the tyre just exploded")


def hook_before_the_pick():
    """A pick near the end of the transcript, so it grows backwards; the line
    its hook quotes comes before the line it starts on."""
    return [
        Segment(0.0, 4.0, "okay so this was the second day"),
        Segment(4.5, 9.0, "we were driving all night"),
        Segment(9.5, 14.0, "and I said turn the radio up right now."),
        Segment(14.5, 19.0, "Then the song came on."),
        Segment(19.5, 26.0, "Everyone started singing."),
    ]


def hook_pick(hook="turn the radio up right now"):
    return ClipCandidate(start=14.5, end=24.0, score=70, hook=hook)


def reaction_with_a_setup(setup=("Watch this.", "Three, two, one.")):
    """A reaction the audio found at 300 s, set up by two short sentences
    just before it."""
    return [
        Segment(280.0, 285.0, "So that was the warm up."),
        Segment(286.0, 290.0, "Alright here is the real test."),
        Segment(294.5, 297.0, setup[0]),
        Segment(297.5, 299.5, setup[1]),
        Segment(300.5, 304.0, "HAHAHA no way!"),
        Segment(305.0, 312.0, "It actually worked, I cannot believe it."),
        Segment(313.0, 320.0, "Okay let me try it again."),
        Segment(321.0, 327.0, "This time it is going to be even louder."),
        Segment(328.0, 335.0, "Right, three more of these and we are done."),
    ]


def reaction_window():
    return ClipCandidate(start=300.0, end=310.0, score=70, hook="no way", source="signal")


# Selection 1's results, recorded from the unchanged code (5c3fb52) before
# selection 2 existed, faults included: what scoring.selection: 1 gives.
V1_T12 = (84.5, 103.0)
V1_HOOK = (14.5, 26.0)
V1_T13 = (300.5, 327.0)
V1_T13_SHORT_CAP = (300.5, 327.0)
V1_CORPUS_SHA256 = "b2eb30921d8dff0cd677a075b1a32f52a72e26884751f69a7eebe4aaea3770dd"


# ---- T12: the payoff line after a pause -------------------------------------------


def test_t12_the_payoff_line_the_pick_starts_on_stays_in_the_clip():
    rules = highlights.start_rules(2, payoff_pick())
    got = _fit_to_segments(payoff_pick(), payoff_after_a_pause(), MIN, MAX, **rules)
    assert span(got) == (80.0, 103.0)
    # Without the hook too: the line the pick starts on is enough.
    got = _fit_to_segments(payoff_pick(), payoff_after_a_pause(), MIN, MAX, keep_own_start=True)
    assert span(got) == (80.0, 103.0)


def test_t12_selection_1_is_unchanged_the_payoff_line_is_cut():
    """Selection 1's behaviour, recorded: the line before the pause has no full stop,
    so the start moves past the payoff line to the next fresh sentence."""
    assert span(_fit_to_segments(payoff_pick(), payoff_after_a_pause(), MIN, MAX)) == V1_T12
    assert V1_T12[0] > 84.0


def test_t12_through_the_chunk_pass():
    """find_highlights fits the model's picks with selection 2's start rules
    when told to (analysis/fusion.py tells it for a standard run)."""

    class Picks:
        def generate(self, prompt, *, json_mode=False):
            return json.dumps({"clips": [{"start": 80.0, "end": 100.0, "score": 72, "engagement": 70,
                                          "trending": False, "hook": "that is when the tyre just exploded",
                                          "reason": "a payoff"}]})

        name = "fake"

    segs = payoff_after_a_pause()
    v2, _ = highlights.find_highlights(segs, Picks(), min_duration=MIN, max_duration=MAX, selection=2)
    v1, _ = highlights.find_highlights(segs, Picks(), min_duration=MIN, max_duration=MAX)
    assert [span(c) for c in v2] == [(80.0, 103.0)]
    assert [span(c) for c in v1] == [V1_T12]


# ---- the hook's own line ---------------------------------------------------------------


def test_the_line_the_hook_quotes_is_never_dropped_from_the_start():
    """The hook's line comes in mid-sentence, but its sentence starts 9.5 s
    before it, further back than a start may reach: the clip starts on the
    hook's line."""
    rules = highlights.start_rules(2, hook_pick())
    got = _fit_to_segments(hook_pick(), hook_before_the_pick(), MIN, MAX, **rules)
    assert span(got) == (9.5, 26.0)


def test_a_hook_that_is_not_quoted_protects_nothing_more():
    """A paraphrase, or the placeholder a window gets when the model says
    nothing, matches no line: only the pick's own line is kept."""
    for hook in ("the moment the music started", "High-energy moment", ""):
        c = hook_pick(hook)
        got = _fit_to_segments(c, hook_before_the_pick(), MIN, MAX, **highlights.start_rules(2, c))
        assert span(got) == (14.5, 26.0), hook


def test_the_hook_rule_selection_1_is_unchanged():
    assert span(_fit_to_segments(hook_pick(), hook_before_the_pick(), MIN, MAX)) == V1_HOOK


def test_a_chinese_hook_is_found_character_by_character():
    """Written without spaces, a whole Chinese clause is one "word" to \\w+;
    the hook is matched a character at a time instead. As in the test above,
    the hook's line comes in mid-sentence, but its sentence starts 9.5 s before
    it, out of reach: the clip starts on the hook's line."""
    segs = [
        Segment(0.0, 4.0, "我们那天开了一整夜"),
        Segment(4.5, 9.0, "大家都很累"),
        Segment(9.5, 14.0, "然后我说把音乐开大一点。"),
        Segment(14.5, 19.0, "接着那首歌就响了。"),
        Segment(19.5, 26.0, "大家都跟着唱起来。"),
    ]
    c = ClipCandidate(start=14.5, end=24.0, score=70, hook="把音乐开大一点")
    assert span(_fit_to_segments(c, segs, MIN, MAX, **highlights.start_rules(2, c))) == (9.5, 26.0)


# ---- a start that comes in mid-sentence -----------------------------------------------


def runs_on_into_the_pick(pause=0.2, sentence_from=101.5):
    """A finished sentence, then one over three lines: the first two have no
    full stop, and the third, the line the pick starts on, comes `pause`
    seconds after them. The sentence starts at `sentence_from`, 2.7 s before
    the pick's line by default."""
    p = 104.0 + pause
    return [
        Segment(sentence_from - 5.5, sentence_from - 0.5, "We finally got the tent up before the rain."),
        Segment(sentence_from, 103.0, "So I asked my brother"),
        Segment(103.0, 104.0, "to grab the lantern and"),
        Segment(p, p + 3.5, "he came back holding a frying pan."),
        Segment(p + 4.0, p + 9.0, "We laughed so hard the neighbours complained."),
        Segment(p + 9.5, p + 15.0, "Nobody got any sleep that night."),
        Segment(p + 15.5, p + 21.0, "In the morning the pan was still by the fire."),
        Segment(p + 21.5, p + 27.0, "My brother still says it was a joke."),
    ]


def pan_pick(pause=0.2):
    return ClipCandidate(start=104.0 + pause, end=119.0 + pause, score=74,
                         hook="he came back holding a frying pan")


def hook_line_runs_on():
    """Near the end of a transcript, so the pick grows backwards: the line its
    hook quotes comes before the line it starts on, and comes in mid-sentence,
    0.2 s after the line without a full stop that starts the sentence."""
    return [
        Segment(0.0, 5.0, "We had been on the road since dawn."),
        Segment(5.5, 8.0, "Somewhere past midnight my sister"),
        Segment(8.2, 11.5, "leaned over and said turn the radio up right now"),
        Segment(11.7, 14.0, "because her song was on."),
        Segment(14.5, 19.0, "Everyone started singing."),
        Segment(19.5, 26.5, "Even the dog joined in."),
    ]


def hook_line_pick(hook="turn the radio up right now"):
    return ClipCandidate(start=14.5, end=24.0, score=71, hook=hook)


def chinese_runs_on(before_the_pick="去拿那盏灯"):
    """The tent story in Chinese, written without spaces and with full-width
    punctuation. The pick's line comes 0.2 s after `before_the_pick`; by
    default that line and the one before it have no sentence end, and the
    sentence starts 3.7 s before the pick's line."""
    return [
        Segment(40.0, 44.0, "我们终于把帐篷搭好了。"),
        Segment(44.5, 46.5, "然后我让我弟弟"),
        Segment(46.5, 48.0, before_the_pick),
        Segment(48.2, 51.5, "结果他拿回来一个平底锅。"),
        Segment(52.0, 57.0, "我们笑得停不下来。"),
        Segment(57.5, 63.0, "那天晚上谁都没睡着。"),
        Segment(63.5, 69.0, "第二天早上锅还在火堆旁边。"),
        Segment(69.5, 75.0, "我弟弟到现在还说那是个玩笑。"),
    ]


def chinese_pick():
    return ClipCandidate(start=48.2, end=63.2, score=74, hook="他拿回来一个平底锅")


def told(lines):
    """Made-up lines from 0 s, each 2.85 s long, 0.3 s apart: the third starts at 6.3 s."""
    return [Segment(round(i * 3.15, 2), round(i * 3.15 + 2.85, 2), text) for i, text in enumerate(lines)]


def on_line(segs, k):
    """A pick that starts on segs[k], 15 s long, with no hook."""
    return ClipCandidate(start=segs[k].start, end=segs[k].start + 15.0, score=70)


def hindi(over_two_lines=False):
    """A trip told in Hindi, each line a sentence that ends in a danda (।).
    `over_two_lines`: the second line has none, and the third finishes its
    sentence."""
    lines = [
        "हम सुबह जल्दी निकल गए।",  # we set off early in the morning.
        "रास्ते में बहुत बारिश हो रही थी।",  # it was raining hard on the way.
        "फिर गाड़ी का टायर फट गया।",  # then the car's tyre burst.
        "हमारे पास दूसरा टायर नहीं था।",  # we had no spare tyre.
        "तो हम गाड़ी की छत पर बैठ गए।",  # so we sat on the roof of the car.
        "और खूब हँसे।",  # and laughed and laughed.
        "फिर एक किसान ट्रैक्टर लेकर आया।",  # then a farmer came by with a tractor.
        "उसने हमें गाँव तक पहुँचाया।",  # he took us to the village.
    ]
    if over_two_lines:
        lines[1:3] = [
            "रास्ते में मेरे भाई ने बताया कि",  # on the way my brother told us that
            "गाड़ी का टायर फट गया है।",  # the car's tyre had burst.
        ]
    return told(lines)


def urdu():
    """The trip told in Urdu: sentences end in the Arabic full stop (۔), and
    the second line in the Arabic question mark (؟)."""
    return told([
        "ہم صبح سویرے نکل پڑے۔",  # we set out early in the morning.
        "کیا آپ یقین کریں گے؟",  # would you believe it?
        "راستے میں گاڑی کا ٹائر پھٹ گیا۔",  # on the way the car's tyre burst.
        "ہمارے پاس دوسرا ٹائر نہیں تھا۔",  # we had no spare tyre.
        "ہم گاڑی کی چھت پر بیٹھ کر ہنستے رہے۔",  # we sat on the roof and kept laughing.
        "پھر ایک کسان ٹریکٹر لے کر آیا۔",  # then a farmer came by with a tractor.
        "اس نے ہمیں گاؤں تک پہنچایا۔",  # he took us to the village.
        "وہ دن ہم کبھی نہیں بھولیں گے۔",  # we will never forget that day.
    ])


def arabic():
    """The tent story told in Arabic, where a sentence ends in a full stop or
    the Arabic question mark (؟). The third line has no sentence end: its
    sentence runs on into the fourth."""
    return told([
        "خرجنا للتخييم في نهاية الأسبوع.",  # we went camping at the weekend.
        "هل تعرفون ماذا حدث بعد ذلك؟",  # do you know what happened next?
        "طلبت من أخي أن يحضر المصباح",  # I asked my brother to bring the lamp
        "فعاد ومعه مقلاة.",  # and he came back with a frying pan.
        "ضحكنا كثيرا.",  # we laughed a lot.
        "لم ينم أحد تلك الليلة.",  # nobody slept that night.
        "وفي الصباح كانت المقلاة بجانب النار.",  # in the morning the pan was by the fire.
        "وما زال أخي يقول إنها كانت مزحة.",  # my brother still says it was a joke.
    ])


# Speech recognition that left the punctuation out, and Thai, which is written
# without sentence ends (a space between sentences).
UNPUNCTUATED = [
    "so we got to the campsite really late",
    "and my brother was meant to grab the lantern",
    "and he comes back holding a frying pan",
    "we could not stop laughing",
    "the neighbours started shouting at us",
    "nobody slept at all that night",
    "in the morning the pan was still by the fire",
    "and he still says it was a joke",
]
THAI = [
    "เรามาถึงที่ตั้งแคมป์ดึกมาก",  # we got to the campsite very late
    "พี่ชายผมควรจะไปเอาตะเกียงมา",  # my brother was meant to fetch the lantern
    "แต่เขากลับถือกระทะมาแทน",  # but he came back holding a frying pan instead
    "พวกเราหัวเราะกันไม่หยุด",  # we could not stop laughing
    "เพื่อนบ้านตะโกนใส่พวกเรา",  # the neighbours shouted at us
    "คืนนั้นไม่มีใครได้นอนเลย",  # nobody slept that night
    "ตอนเช้ากระทะยังวางอยู่ข้างกองไฟ",  # in the morning the pan was still by the fire
    "พี่ชายผมยังบอกว่ามันเป็นแค่เรื่องตลก",  # my brother still says it was only a joke
]


def fit_2(c, segs, max_duration=MAX, **change):
    """_fit_to_segments with selection 2's start rules for `c`; `change` overrides some."""
    return span(_fit_to_segments(c, segs, MIN, max_duration, **{**highlights.start_rules(2, c), **change}))


def mid_sentence_cases():
    """The transcripts below, by name: (candidate, segments, max_duration)."""
    return {
        "a breath": (pan_pick(), runs_on_into_the_pick(), MAX),
        "a 1.5 s pause": (pan_pick(1.5), runs_on_into_the_pick(1.5), MAX),
        "a 4 s pause": (pan_pick(4.0), runs_on_into_the_pick(4.0), MAX),
        "sentence 7.2 s back": (pan_pick(), runs_on_into_the_pick(sentence_from=97.0), MAX),
        "sentence 8.7 s back": (pan_pick(), runs_on_into_the_pick(sentence_from=95.5), MAX),
        "max_duration 23": (pan_pick(), runs_on_into_the_pick(), 23.0),
        "the hook's line": (hook_line_pick(), hook_line_runs_on(), MAX),
        "the hook's line, max_duration 20": (hook_line_pick(), hook_line_runs_on(), 20.0),
        "Chinese, no sentence end": (chinese_pick(), chinese_runs_on(), MAX),
        "Chinese, a question mark": (chinese_pick(), chinese_runs_on("你猜怎么着？"), MAX),
        "Chinese, a closing quote": (chinese_pick(), chinese_runs_on("他说：「我去拿灯。」"), MAX),
        "a 2 s pause": (pan_pick(2.0), runs_on_into_the_pick(2.0), MAX),
        "sentence 8 s back": (pan_pick(), runs_on_into_the_pick(sentence_from=96.2), MAX),
        "Hindi, after a danda": (on_line(hindi(), 2), hindi(), MAX),
        "Hindi, a sentence over two lines": (on_line(hindi(True), 2), hindi(True), MAX),
        "Urdu, after a full stop": (on_line(urdu(), 1), urdu(), MAX),
        "Urdu, after a question mark": (on_line(urdu(), 2), urdu(), MAX),
        "Arabic, after a question mark": (on_line(arabic(), 2), arabic(), MAX),
        "Arabic, a sentence over two lines": (on_line(arabic(), 3), arabic(), MAX),
        "no punctuation": (on_line(told(UNPUNCTUATED), 2), told(UNPUNCTUATED), MAX),
        "Thai": (on_line(told(THAI), 2), told(THAI), MAX),
    }


# Selection 1's results, recorded from the unchanged code (5c3fb52) before
# selection 2 existed, faults included: what scoring.selection: 1 gives.
V1_MID_SENTENCE = {
    "a breath": (108.2, 125.2),
    "a 1.5 s pause": (109.5, 126.5),
    "a 4 s pause": (112.0, 129.0),
    "sentence 7.2 s back": (108.2, 125.2),
    "sentence 8.7 s back": (108.2, 125.2),
    "max_duration 23": (108.2, 125.2),
    "the hook's line": (14.5, 26.5),
    "the hook's line, max_duration 20": (14.5, 26.5),
    "Chinese, no sentence end": (52.0, 69.0),
    "Chinese, a question mark": (48.2, 69.0),
    "Chinese, a closing quote": (48.2, 69.0),
    "a 2 s pause": (110.0, 127.0),
    "sentence 8 s back": (108.2, 125.2),
    "Hindi, after a danda": (6.3, 24.9),
    "Hindi, a sentence over two lines": (6.3, 24.9),
    "Urdu, after a full stop": (3.15, 21.75),
    "Urdu, after a question mark": (6.3, 24.9),
    "Arabic, after a question mark": (12.6, 24.9),
    "Arabic, a sentence over two lines": (12.6, 24.9),
    "no punctuation": (6.3, 24.9),
    "Thai": (6.3, 24.9),
}


def test_a_pick_that_comes_in_mid_sentence_starts_where_the_sentence_starts():
    """The pick's line comes a breath, or a 1.5 s pause, after two lines without
    a full stop: the clip starts on the first of them, where the sentence
    starts. Up to 8 s back: 7.2 s is in reach."""
    assert fit_2(pan_pick(), runs_on_into_the_pick()) == (101.5, 125.2)
    assert fit_2(pan_pick(1.5), runs_on_into_the_pick(1.5)) == (101.5, 126.5)
    assert fit_2(pan_pick(), runs_on_into_the_pick(sentence_from=97.0)) == (97.0, 125.2)
    # Without this rule the start stays on the pick's own line, mid-sentence.
    assert fit_2(pan_pick(), runs_on_into_the_pick(), sentence_reach=0.0) == (104.2, 125.2)


def test_a_pick_that_comes_in_mid_sentence_through_the_chunk_pass():
    class OnePick:
        name = "fake"

        def generate(self, prompt, *, json_mode=False):
            c = pan_pick()
            return json.dumps({"clips": [{"start": c.start, "end": c.end, "score": c.score, "engagement": 70,
                                          "trending": False, "hook": c.hook, "reason": "a payoff"}]})

    segs = runs_on_into_the_pick()
    v2, _ = highlights.find_highlights(segs, OnePick(), min_duration=MIN, max_duration=MAX, selection=2)
    v1, _ = highlights.find_highlights(segs, OnePick(), min_duration=MIN, max_duration=MAX)
    assert [span(c) for c in v2] == [(101.5, 125.2)]
    assert [span(c) for c in v1] == [V1_MID_SENTENCE["a breath"]]


def test_after_a_long_pause_the_pick_keeps_its_own_line():
    """Four seconds of silence after the line without a full stop: the speaker
    stopped there, and going back over it would put that silence in the clip's
    first seconds. The start stays on the pick's line, though the sentence's
    first line is only 6.5 s back."""
    assert fit_2(pan_pick(4.0), runs_on_into_the_pick(4.0)) == (108.0, 129.0)


def test_a_sentence_that_starts_too_far_back_leaves_the_start_where_it_was():
    """The sentence starts 8.7 s before the pick's line, further back than the
    8 s a start may reach: the start stays on the pick's line, as it was
    without this rule, and never moves part of the way (still mid-sentence)."""
    segs = runs_on_into_the_pick(sentence_from=95.5)
    assert fit_2(pan_pick(), segs) == (104.2, 125.2)
    assert fit_2(pan_pick(), segs, sentence_reach=0.0) == (104.2, 125.2)
    assert fit_2(pan_pick(), segs, sentence_reach=10.0) == (95.5, 125.2)  # in reach, the sentence's start


def test_the_sentence_start_must_fit_within_max_duration():
    """From the sentence's start the clip runs 23.7 s: with max_duration 23
    the start stays on the pick's line (the end is never given up for it, and
    the start never moves part of the way); with 24 there is room, and with
    exactly the 23.7 s it needs too (the limit is inclusive)."""
    assert fit_2(pan_pick(), runs_on_into_the_pick(), max_duration=23.0) == (104.2, 125.2)
    assert fit_2(pan_pick(), runs_on_into_the_pick(), max_duration=24.0) == (101.5, 125.2)
    assert fit_2(pan_pick(), runs_on_into_the_pick(), max_duration=125.2 - 101.5) == (101.5, 125.2)


def test_a_pause_of_exactly_sentence_pause_seconds_still_runs_on():
    """The pause limit is inclusive: the pick's line comes exactly
    SENTENCE_PAUSE_SECONDS (2 s) after the line without a full stop, and the
    start still goes back to where the sentence starts."""
    segs = runs_on_into_the_pick(2.0)
    assert segs[3].start - segs[2].end == highlights.SENTENCE_PAUSE_SECONDS  # 106.0 - 104.0, exactly
    assert fit_2(pan_pick(2.0), segs) == (101.5, 127.0)


def test_a_sentence_exactly_sentence_reach_seconds_back_is_in_reach():
    """The reach is inclusive: the sentence starts exactly
    SENTENCE_REACH_SECONDS (8 s) before the pick's line, and the start goes
    back to it."""
    segs = runs_on_into_the_pick(sentence_from=96.2)
    assert segs[3].start - segs[1].start == highlights.SENTENCE_REACH_SECONDS  # 104.2 - 96.2, exactly
    assert fit_2(pan_pick(), segs) == (96.2, 125.2)


def test_the_hook_line_is_kept_and_its_sentence_comes_with_it():
    """The line the hook quotes comes before the pick's line and comes in
    mid-sentence: the start goes back to where that sentence starts, 2.7 s
    earlier, never later than the hook's line. Without a quote only the
    pick's own line is kept, and it starts a sentence of its own."""
    assert fit_2(hook_line_pick(), hook_line_runs_on()) == (5.5, 26.5)
    assert fit_2(hook_line_pick(), hook_line_runs_on(), sentence_reach=0.0) == (8.2, 26.5)
    paraphrase = hook_line_pick("the moment she asked for the music")
    assert fit_2(paraphrase, hook_line_runs_on()) == (14.5, 26.5)


def test_the_hook_line_stays_first_when_its_sentence_does_not_fit():
    """From where the hook line's sentence starts the clip would run 21 s, over
    a max_duration of 20: the start stays on the hook's line, mid-sentence but
    never past it. (A sentence that starts too far back: the hook test above,
    test_the_line_the_hook_quotes_is_never_dropped_from_the_start.)"""
    assert fit_2(hook_line_pick(), hook_line_runs_on(), max_duration=20.0) == (8.2, 26.5)


def test_chinese_full_width_punctuation_marks_where_a_sentence_starts():
    """The sentence ends the end rules read, Chinese full-width ones included
    (tests/test_chinese_sentence_boundaries.py): after two lines with none,
    the start goes back to the line after the "。", 3.7 s earlier; after a
    "？", or a "。" inside a closing 」, the pick's line starts its own
    sentence and stays the start."""
    assert fit_2(chinese_pick(), chinese_runs_on()) == (44.5, 69.0)
    assert fit_2(chinese_pick(), chinese_runs_on("你猜怎么着？")) == (48.2, 69.0)
    assert fit_2(chinese_pick(), chinese_runs_on("他说：「我去拿灯。」")) == (48.2, 69.0)


def test_hindi_sentences_end_in_a_danda():
    """Hindi ends a sentence with a danda (।). Read as no sentence end, it made
    every line run on from the one before, and a pick on a line that starts
    its own sentence went back 6.3 s to the first line. That line stays the
    start now, and a pick that does come in mid-sentence goes back to the line
    after the danda, no further."""
    assert fit_2(on_line(hindi(), 2), hindi()) == (6.3, 24.9)
    assert fit_2(on_line(hindi(True), 2), hindi(True)) == (3.15, 24.9)


def test_urdu_sentences_end_in_the_arabic_full_stop_or_question_mark():
    """A pick on the line after an Arabic full stop (۔), or after an Arabic
    question mark (؟), starts its own sentence and stays the start (read as
    no sentence end, both went back to the first line)."""
    assert fit_2(on_line(urdu(), 1), urdu()) == (3.15, 21.75)
    assert fit_2(on_line(urdu(), 2), urdu()) == (6.3, 24.9)


def test_an_arabic_question_mark_ends_a_sentence():
    """A pick on the line after the question starts its own sentence and stays
    the start; a pick on the next line, which finishes that sentence, goes
    back to it, and not on into the question (read as no sentence end, both
    went back to the question's line)."""
    assert fit_2(on_line(arabic(), 2), arabic()) == (6.3, 24.9)
    assert fit_2(on_line(arabic(), 3), arabic()) == (6.3, 24.9)


def test_a_transcript_with_no_sentence_end_moves_no_start():
    """Speech recognition that left the punctuation out, and Thai, written
    without sentence ends: every line runs on from the one before it, so where
    a sentence starts can't be told, and no start moves. (On short pauses
    alone the rule took this pick 6.3 s back, to the first line.)"""
    for lines in (UNPUNCTUATED, THAI):
        segs = told(lines)
        assert fit_2(on_line(segs, 2), segs) == (6.3, 24.9)
        assert fit_2(on_line(segs, 2), segs, sentence_reach=0.0) == (6.3, 24.9)
        assert not any(highlights._ends_sentence_for_start(s) for s in segs)


@pytest.mark.parametrize("mark", [
    "\N{DEVANAGARI DANDA}", "\N{DEVANAGARI DOUBLE DANDA}", "\N{ARABIC FULL STOP}", "\N{ARABIC QUESTION MARK}",
    "\N{FULLWIDTH FULL STOP}", "\N{HALFWIDTH IDEOGRAPHIC FULL STOP}",
], ids=["danda", "double danda", "Arabic full stop", "Arabic question mark", "full-width full stop",
        "half-width ideographic full stop"])
def test_the_start_rule_reads_these_sentence_ends_and_the_end_rules_do_not(mark):
    """Sentence ends of languages in multilingual/languages.py that the end
    rules don't read: the start rule reads them, also inside a closing quote
    or bracket; the end rules, and so selection 1, still don't."""
    for text in ("x" + mark, "x" + mark + "”", " x" + mark + "» ", "(x" + mark + ")"):
        assert highlights._ends_sentence_for_start(Segment(0.0, 1.0, text)), text
        assert not highlights._ends_sentence(Segment(0.0, 1.0, text)), text
    assert not highlights._ends_sentence_for_start(Segment(0.0, 1.0, "x" + mark + " and"))


def test_mid_sentence_selection_1_is_unchanged():
    """Selection 1's behaviour on these transcripts, recorded: the start moves in past
    the pick's line, and past the hook's, to the next fresh sentence."""
    for name, (c, segs, cap) in mid_sentence_cases().items():
        rules = highlights.start_rules(1, c, lead_in=highlights.LEAD_IN_SECONDS)
        assert span(_fit_to_segments(c, segs, MIN, cap, **rules)) == V1_MID_SENTENCE[name], name
    assert V1_MID_SENTENCE["a breath"][0] > 104.2 and V1_MID_SENTENCE["the hook's line"][0] > 8.2
    assert V1_MID_SENTENCE["Chinese, no sentence end"][0] > 48.2


# ---- T13: a reaction's setup (a policy, not a measured fix) -----------------------------


def test_t13_a_reaction_peak_gets_its_setup_as_complete_sentences():
    """POLICY TEST. Selection 2 gives a signal window up to LEAD_IN_SECONDS
    (6 s) of complete sentences before its start, so a reaction comes with what
    it reacts to. The length and the complete-sentences rule are a choice
    made for selection 2, not a measured fix; this test pins the choice."""
    rules = highlights.start_rules(2, reaction_window(), lead_in=highlights.LEAD_IN_SECONDS)
    got = _fit_to_segments(reaction_window(), reaction_with_a_setup(), MIN, MAX, target_duration=25.0, **rules)
    assert span(got) == (294.5, 327.0)  # "Watch this. Three, two, one." then the laugh


def test_t13_the_setup_stays_within_max_duration():
    """POLICY TEST (see above): the lead-in never takes a clip past max_duration;
    it keeps what fits, whole sentences only."""
    rules = highlights.start_rules(2, reaction_window(), lead_in=highlights.LEAD_IN_SECONDS)
    got = _fit_to_segments(reaction_window(), reaction_with_a_setup(), MIN, 30.0, target_duration=25.0,
                           **rules)
    assert span(got) == (297.5, 327.0)
    assert got.end - got.start <= 30.0


def test_t13_the_setup_never_starts_mid_sentence():
    """POLICY TEST (see above): no sentence starts within 6 s before the window
    (the line before "and watch this" has no full stop), so nothing is added."""
    segs = reaction_with_a_setup(setup=("and watch this", "Three, two, one."))
    segs[1] = Segment(286.0, 290.0, "alright here is the real test")
    rules = highlights.start_rules(2, reaction_window(), lead_in=highlights.LEAD_IN_SECONDS)
    got = _fit_to_segments(reaction_window(), segs, MIN, MAX, target_duration=25.0, **rules)
    assert span(got) == (300.5, 327.0)


def test_t13_selection_1_is_unchanged():
    got = _fit_to_segments(reaction_window(), reaction_with_a_setup(), MIN, MAX, target_duration=25.0)
    assert span(got) == V1_T13
    got = _fit_to_segments(reaction_window(), reaction_with_a_setup(), MIN, 30.0, target_duration=25.0)
    assert span(got) == V1_T13_SHORT_CAP


# ---- selection 1 is as it was, on many transcripts ----------------------------------------

LINES = [
    "That was close.", "and then we", "Really?", "no way", "We did it!", "so anyway",
    "He said \"stop.\"", "um", "Okay.", "完成了。", "还有后续", "and the tyre just exploded",
    "turn the radio up right now.", "I cannot believe it",
]


def corpus(seed=2026, cases=400):
    """Synthetic transcripts and candidates of every shape: pauses, long and
    tiny segments, punctuated and not, near the start and the end. Only
    Random.random() is used, which Python keeps the same across versions."""
    r = random.Random(seed)
    out = []
    for _ in range(cases):
        segs = []
        t = round(r.random() * 50, 2)
        for _ in range(1 + int(r.random() * 24)):
            t = round(t + r.random() * 6, 2)  # a pause, often short
            length = round(0.3 + r.random() * (90 if r.random() < 0.05 else 9), 2)
            segs.append(Segment(start=t, end=round(t + length, 2), text=LINES[int(r.random() * len(LINES))]))
            t = round(t + length, 2)
        a = round(segs[0].start - 5 + r.random() * (segs[-1].end - segs[0].start + 10), 2)
        b = round(a + 1 + r.random() * 70, 2)
        lo, hi = [(10.0, 60.0), (5.0, 30.0), (61.0, 180.0), (15.0, 35.0)][int(r.random() * 4)]
        target = [None, 25.0, 30.0][int(r.random() * 3)]
        hook = LINES[int(r.random() * len(LINES))] if r.random() < 0.5 else ""
        out.append((a, b, segs, lo, hi, target, hook))
    return out


def fit_all(rules_for):
    got = []
    for a, b, segs, lo, hi, target, hook in corpus():
        c = ClipCandidate(start=a, end=b, score=50, hook=hook)
        c = _fit_to_segments(c, segs, lo, hi, target, **rules_for(c))
        got.append((c.start, c.end))
    return got


def digest(spans):
    return hashlib.sha256(json.dumps(spans).encode("utf-8")).hexdigest()


def test_selection_1_fits_400_transcripts_exactly_as_before():
    assert digest(fit_all(lambda c: {})) == V1_CORPUS_SHA256
    assert digest(fit_all(lambda c: highlights.start_rules(1, c, lead_in=6.0))) == V1_CORPUS_SHA256


def test_selection_2_only_ever_starts_earlier_and_keeps_the_end_rules():
    """On the same 400: selection 2 never starts a clip later than selection 1
    did, never ends it elsewhere unless selection 1 padded a too-short clip by
    the clock, and never takes it past max_duration when selection 1 kept it
    within."""
    before = fit_all(lambda c: {})
    after = fit_all(lambda c: highlights.start_rules(2, c, lead_in=6.0))
    cases = corpus()
    assert before != after  # it does change something on these
    for (s1, e1), (s2, e2), (_a, _b, _segs, lo, hi, _t, _h) in zip(before, after, cases):
        assert s2 <= s1
        if round(e1 - s1, 2) > lo:
            assert e2 == e1
        if round(e1 - s1, 2) <= hi:
            assert round(e2 - s2, 2) <= hi


def starts_a_sentence(segs, j):
    """The first line, or one after a sentence end (as the start rule reads
    them) or after a pause longer than SENTENCE_PAUSE_SECONDS."""
    return (j == 0 or highlights._ends_sentence_for_start(segs[j - 1])
            or segs[j].start - segs[j - 1].end > highlights.SENTENCE_PAUSE_SECONDS)


def test_a_start_only_ever_goes_back_to_where_its_sentence_starts():
    """On the same 400, selection 2 with and without the sentence rule: a start
    never comes later and the end never moves; a start that moves goes back at
    most SENTENCE_REACH_SECONDS, within max_duration, to the nearest line
    before it that starts a sentence, from a line that didn't, and never in a
    transcript with no sentence end."""
    without = fit_all(lambda c: {**highlights.start_rules(2, c), "sentence_reach": 0.0})
    with_it = fit_all(lambda c: highlights.start_rules(2, c))
    moved = 0
    for (s0, e0), (s1, e1), (_a, _b, segs, shortest, longest, _t, _h) in zip(without, with_it, corpus()):
        assert s1 <= s0
        if not shortest < round(e0 - s0, 2) <= longest:
            continue  # the clock, not the lines, set this clip's length
        assert e1 == e0 and round(e1 - s1, 2) <= longest
        if s1 < s0:
            moved += 1
            assert any(highlights._ends_sentence_for_start(s) for s in segs)
            starts = [s.start for s in segs]
            new, old = starts.index(s1), starts.index(s0)
            assert s0 - s1 <= highlights.SENTENCE_REACH_SECONDS
            assert starts_a_sentence(segs, new)
            assert not any(starts_a_sentence(segs, j) for j in range(new + 1, old + 1))
    assert moved >= 10  # 12 of the 400 when this was written
