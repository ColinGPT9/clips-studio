from analysis.fusion import _fuse, _speech_ratio
from core.models import ClipCandidate, Segment

# A silent workout clip: great visuals/reaction, no talking
silent = ClipCandidate(start=0, end=20, score=30)  # LLM text score low
silent.subscores = {"text": 30, "visual": 82, "reaction": 75, "audio": 55, "engagement": 30}

# A talking-head clip: strong text, mild visuals
talky = ClipCandidate(start=0, end=20, score=85)
talky.subscores = {"text": 85, "visual": 40, "reaction": 50, "audio": 45, "engagement": 80}

weights = {"text": 0.30, "visual": 0.20, "reaction": 0.20, "audio": 0.20, "engagement": 0.10}

# Silent clip: speech_ratio ~0 -> weights shift to visual/audio/reaction
sil_old = round(100 * _fuse(silent, weights, 0.75, speech_ratio=1.0))  # if treated as talky (old behavior)
sil_new = round(100 * _fuse(silent, weights, 0.75, speech_ratio=0.0))  # adaptive (silent)
print(f"silent workout clip: old(text-weighted)={sil_old}  new(adaptive)={sil_new}")
assert sil_new > sil_old, "adaptive must boost silent-but-visual clips"
assert sil_new >= 65, f"great workout moment should now pass 65, got {sil_new}"

# Talky clip: speech_ratio ~1 -> unchanged, text still matters
talk_new = round(100 * _fuse(talky, weights, 0.5, speech_ratio=1.0))
print(f"talking-head clip: adaptive={talk_new} (text still weighted)")
assert talk_new >= 65

# Speech ratio computation
segs = [Segment(0.0, 20.0, "one two three four five six seven eight")]  # 8 words / 20s = 0.4 wps
c = ClipCandidate(start=0, end=20, score=50)
sr = _speech_ratio(c, segs)
print(f"speech_ratio for 0.4 words/sec: {sr:.2f} (0=silent, 1=steady talk)")
assert 0 < sr < 0.3, "sparse speech should be low ratio"
print("\nadaptive weighting: OK -- workout clips can now reach the bar")
