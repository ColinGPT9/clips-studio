import numpy as np

from analysis import hype


def test_organic_retention_spike_becomes_clip_scale_hot_zone():
    duration = 432.0
    rows = []
    for i in range(1, 101):
        ratio = i / 100.0
        watch = 1.0 - (0.005 * i)
        if i == 92:
            watch += 0.20
        rows.append([ratio, watch])

    curve = hype._retention_rows_to_curve(rows, duration)

    assert curve is not None
    assert len(curve) == 433
    assert 0.0 <= float(curve.min()) <= float(curve.max()) <= 1.0

    peak_second = round(0.92 * duration)
    assert float(curve[peak_second]) > 0.95

    # The point-like Analytics peak is widened only enough to survive Clips
    # Kitty's clip-window mean. It remains a local hot zone, not a video-wide
    # lift.
    local_mean = float(curve[peak_second - 12 : peak_second + 13].mean())
    assert local_mean > 0.85
    assert float(curve[peak_second - 30 : peak_second - 20].mean()) < local_mean


def test_clip_scale_interest_preserves_peak_timestamp_and_value():
    point_curve = np.zeros(101, dtype=np.float32)
    point_curve[50] = 1.0

    curve = hype._clip_scale_interest(point_curve)

    assert int(curve.argmax()) == 50
    assert float(curve[50]) == 1.0
    assert float(curve[38]) >= 0.925 - 1e-6
    assert float(curve[62]) >= 0.925 - 1e-6
    assert float(curve[37]) == 0.0
    assert float(curve[63]) == 0.0


def test_youtube_prefers_authenticated_organic_curve(monkeypatch):
    organic = np.linspace(0.0, 1.0, 61, dtype=np.float32)

    monkeypatch.setattr(
        hype,
        "_youtube_organic_retention",
        lambda video_id, duration: organic,
    )

    def public_heatmap_must_not_run(url, duration):
        raise AssertionError("public heatmap should not run when organic data exists")

    monkeypatch.setattr(hype, "_youtube_heatmap", public_heatmap_must_not_run)

    result = hype.audience_curve(
        "https://www.youtube.com/watch?v=owned",
        "owned",
        60.0,
    )

    assert result is organic


def test_youtube_falls_back_to_existing_public_heatmap(monkeypatch):
    public = np.linspace(1.0, 0.0, 61, dtype=np.float32)

    monkeypatch.setattr(
        hype,
        "_youtube_organic_retention",
        lambda video_id, duration: None,
    )
    monkeypatch.setattr(
        hype,
        "_youtube_heatmap",
        lambda url, duration: public,
    )

    result = hype.audience_curve(
        "https://www.youtube.com/watch?v=other",
        "other",
        60.0,
    )

    assert result is public


def test_missing_oauth_token_is_normal_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPS_STUDIO_DATA_DIR", str(tmp_path))

    assert hype._youtube_organic_retention("video", 60.0) is None
