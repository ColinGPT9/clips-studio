"""A GPU that CUDA can see is not the same as a GPU this build can run on.

`torch.cuda.is_available()` answers "is there a driver and a device". It says
nothing about whether the wheel was compiled for that device, and the gap
between those two questions is issue #83: an RTX 5070 Ti (Blackwell, sm_120)
against a CUDA 12.6 build topping out at sm_90 passed every check, accepted
`.to("cuda")`, and then killed the job at the first inference with "no kernel
image is available for execution on the device".

The cases below are the ones that actually matter, and two of them are traps:

  * an RTX 4090 is sm_89 while the newest Ada entry a wheel ships is sm_86, so
    testing for an exact match would silently demote every 4090 to CPU
  * sm_120 must parse as major 12, not major 1 or 120, or Blackwell compares
    against the wrong architectures entirely

There is no wheel that covers every card, so this is permanent: CUDA 13 buys
Blackwell and gives up Maxwell, Pascal and Volta. Whatever ships, somebody is
outside it, and they must land on the CPU with an explanation instead of a
crash.
"""

import pytest

from core.gpu import NO_GPU, _parse_arch, _runs_on, cuda_usable

# Real architecture lists, measured from the wheels themselves rather than
# written from memory.
CU126 = ["sm_50", "sm_60", "sm_61", "sm_70", "sm_75", "sm_80", "sm_86", "sm_90"]
CU130 = ["sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"]


def _usable(archs: list[str], device: tuple[int, int]) -> bool:
    parsed = [a for a in (_parse_arch(s) for s in archs) if a]
    return any(_runs_on(a, device) for a in parsed)


@pytest.mark.parametrize(
    "archs, device, expected, why",
    [
        (CU126, (12, 0), False, "the bug: RTX 50-series on the CUDA 12.6 build"),
        (CU130, (12, 0), True, "the fix: the same card on CUDA 13"),
        (CU126, (8, 6), True, "RTX 3060, the machine this was developed on"),
        (CU130, (8, 6), True, "and it must keep working after the upgrade"),
        (CU126, (8, 9), True, "RTX 4090: sm_89 runs on sm_86 kernels"),
        (CU130, (7, 5), True, "GTX 1660: Turing survives CUDA 13"),
        (CU126, (6, 1), True, "GTX 1080 works today"),
        (CU130, (6, 1), False, "and is the accepted cost of CUDA 13"),
        (CU130, (5, 0), False, "Maxwell, dropped by CUDA 13"),
    ],
)
def test_capability_rule(archs, device, expected, why):
    assert _usable(archs, device) is expected, why


def test_sm_120_parses_as_major_12():
    """The digits are not fixed-width: sm_86 is 8.6 but sm_120 is 12.0.

    Read the wrong way, Blackwell either becomes major 1 or major 120 and gets
    compared against a set of architectures it has nothing to do with.
    """
    assert _parse_arch("sm_120") == (12, 0)
    assert _parse_arch("sm_100") == (10, 0)
    assert _parse_arch("sm_86") == (8, 6)
    assert _parse_arch("sm_90") == (9, 0)


def test_ptx_entries_are_ignored():
    """`compute_*` entries are PTX, not compiled code. Treating them as
    architectures would claim support this build cannot guarantee."""
    assert _parse_arch("compute_90") is None
    assert _parse_arch("") is None
    assert _parse_arch("sm_") is None


def test_forward_compatible_only_within_a_major_version():
    """sm_86 code runs on sm_89; nothing below sm_120 runs on sm_120."""
    assert _runs_on((8, 6), (8, 9)) is True
    assert _runs_on((8, 9), (8, 6)) is False, "a newer minor cannot run on an older device"
    assert _runs_on((9, 0), (12, 0)) is False, "majors are not forward compatible"


class _FakeCuda:
    def __init__(self, available, archs=(), capability=(12, 0)):
        self._available, self._archs, self._cap = available, list(archs), capability

    def is_available(self):
        return self._available

    def get_arch_list(self):
        return self._archs

    def get_device_capability(self, _i=0):
        return self._cap

    def get_device_name(self, _i=0):
        return "NVIDIA GeForce RTX 5070 Ti"


def _patch(monkeypatch, fake):
    import torch

    monkeypatch.setattr(torch, "cuda", fake)


def test_reports_no_gpu_distinctly(monkeypatch):
    """"No GPU" and "GPU we cannot use" both mean CPU but share no remedy, so
    preflight has to tell them apart without matching on prose."""
    _patch(monkeypatch, _FakeCuda(available=False))
    usable, reason = cuda_usable()
    assert usable is False
    assert reason == NO_GPU


def test_names_both_architectures_when_the_card_is_too_new(monkeypatch):
    """The message has to carry the card's capability and the build's ceiling.

    Diagnosing #83 needed exactly those two numbers side by side; a bare
    "GPU unsupported" would have sent the next report round the same loop.
    """
    _patch(monkeypatch, _FakeCuda(available=True, archs=CU126, capability=(12, 0)))
    usable, reason = cuda_usable()
    assert usable is False
    assert "sm_120" in reason
    assert "sm_90" in reason


def test_cpu_only_build_is_named_as_such(monkeypatch):
    """An empty arch list is the CPU-only wheel, which on Windows is what a
    plain `pip install torch` gives. The GPU is real; the build is the problem,
    and saying "no GPU detected" would send someone to check their hardware."""
    _patch(monkeypatch, _FakeCuda(available=True, archs=[], capability=(8, 6)))
    usable, reason = cuda_usable()
    assert usable is False
    assert "CPU-only" in reason


def test_never_raises(monkeypatch):
    """A broken GPU probe is a reason to use the CPU, never a reason to take
    the caller down — this runs inside preflight and the tracker."""

    class _Exploding:
        def is_available(self):
            raise RuntimeError("driver on fire")

    _patch(monkeypatch, _Exploding())
    usable, reason = cuda_usable()
    assert usable is False
    assert "could not check" in reason


def test_real_machine_agrees_with_torch():
    """Whatever this machine has, the verdict must match what torch reports.

    Guards the upgrade itself: after moving to cu130 a working GPU must still
    come back usable, so a silent demotion to CPU cannot pass as success.
    """
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("no CUDA GPU on this machine")

    usable, reason = cuda_usable()
    device = torch.cuda.get_device_capability(0)
    expected = _usable(torch.cuda.get_arch_list(), device)
    assert usable is expected, (
        f"sm_{device[0]}{device[1]} against {torch.cuda.get_arch_list()}: {reason}"
    )
