"""Unit tests for hardware-accelerated video encoding and VideoToolbox on macOS."""

import unittest
from unittest.mock import patch

import pytest

from video.encoding import (
    _CANDIDATES,
    CPU_ARGS,
    _probe,
    _select,
)


class TestEncodingCandidates(unittest.TestCase):
    def test_candidates_include_videotoolbox(self):
        """VideoToolbox candidate must be present in _CANDIDATES with h264_videotoolbox."""
        self.assertIn("videotoolbox", _CANDIDATES)
        self.assertEqual(
            _CANDIDATES["videotoolbox"],
            ["-c:v", "h264_videotoolbox", "-b:v", "8M", "-maxrate", "12M"],
        )

    def test_select_cpu_mode(self):
        """Explicit cpu mode returns CPU_ARGS without running probes."""
        mode, args = _select("cpu")
        self.assertEqual(mode, "cpu")
        self.assertEqual(args, CPU_ARGS)

    @patch("video.encoding._probe")
    def test_select_forced_videotoolbox_success(self, mock_probe):
        """Forced videotoolbox returns videotoolbox args when probe succeeds."""
        mock_probe.return_value = True
        mode, args = _select("videotoolbox")
        self.assertEqual(mode, "videotoolbox")
        self.assertEqual(args, _CANDIDATES["videotoolbox"])
        mock_probe.assert_called_once_with(_CANDIDATES["videotoolbox"])

    @patch("video.encoding._probe")
    def test_select_forced_videotoolbox_failure_fallback(self, mock_probe):
        """Forced videotoolbox falls back to cpu when probe fails."""
        mock_probe.return_value = False
        mode, args = _select("videotoolbox")
        self.assertEqual(mode, "cpu")
        self.assertEqual(args, CPU_ARGS)

    @patch("video.encoding._probe")
    def test_select_auto_videotoolbox_on_mac(self, mock_probe):
        """Auto mode picks videotoolbox when nvenc, amf, and qsv fail but videotoolbox succeeds."""
        def probe_side_effect(args):
            return args == _CANDIDATES["videotoolbox"]

        mock_probe.side_effect = probe_side_effect
        mode, args = _select("auto")
        self.assertEqual(mode, "videotoolbox")
        self.assertEqual(args, _CANDIDATES["videotoolbox"])

    @patch("video.encoding._probe")
    def test_select_auto_fallback_to_cpu(self, mock_probe):
        """Auto mode falls back to CPU when all hardware candidates fail probe."""
        mock_probe.return_value = False
        mode, args = _select("auto")
        self.assertEqual(mode, "cpu")
        self.assertEqual(args, CPU_ARGS)


def test_real_videotoolbox_probe_on_macos():
    """If running on macOS with ffmpeg supporting VideoToolbox, verify live probe."""
    import platform

    if platform.system() != "Darwin":
        pytest.skip("VideoToolbox is only available on macOS")

    # Run the real _probe with videotoolbox args
    success = _probe(_CANDIDATES["videotoolbox"])
    assert success is True, "Live _probe with h264_videotoolbox should succeed on macOS"
