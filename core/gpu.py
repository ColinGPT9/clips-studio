"""Whether CUDA can actually run here, as opposed to merely being present.

`torch.cuda.is_available()` is the obvious check and it is not enough. It
answers "is there a driver and a device", not "was this build compiled for that
device". A GPU outside the shipped build's architecture list passes it, accepts
`.to("cuda")` without complaint — moving weights launches no kernel — and then
dies at the first inference with:

    CUDA error: no kernel image is available for execution on the device

That was issue #83: an RTX 5070 Ti (Blackwell, sm_120) against a CUDA 12.6
build whose newest architecture is sm_90.

This is a check rather than a one-off version bump because no build covers
every card, so the same failure arrives from the other end too: CUDA 13 gains
Blackwell and drops Maxwell, Pascal and Volta. Whatever we ship, somebody's GPU
is outside it, and the honest answer is to run on CPU and say so rather than to
crash halfway through their video.
"""


# The one reason callers need to tell apart from the rest: having no GPU and
# having one this build cannot use both mean "running on CPU", but the first is
# a hardware fact and the second is our problem to fix in a release. Compared
# by identity rather than by matching on prose, which would rot.
NO_GPU = "no CUDA GPU detected"

_DEVICE: str | None = None


def gpu_too_old(reason: str) -> bool:
    """Whether `reason` describes a card BELOW the build's oldest architecture.

    The two unusable cases need opposite advice. A card newer than the build is
    ours to fix in a release; a card older than it never will be, because the
    CUDA that gains new architectures is the same one that drops old ones.
    Telling a GTX 1060 owner to wait for a newer build is telling them to wait
    for the thing that would remove their card entirely.
    """
    return "older than" in reason


def _parse_arch(arch: str) -> tuple[int, int] | None:
    """`"sm_86"` -> `(8, 6)`. The last digit is the minor version and
    everything before it is the major, which is the only reading that gets
    both `sm_86` (8.6) and `sm_120` (12.0) right."""
    if not arch.startswith("sm_"):
        return None  # ignore "compute_*" PTX entries; see cuda_usable()
    digits = arch[3:]
    if not digits.isdigit() or len(digits) < 2:
        return None
    return int(digits[:-1]), int(digits[-1])


def _runs_on(arch: tuple[int, int], device: tuple[int, int]) -> bool:
    """Whether code compiled for `arch` runs on a device of capability
    `device`.

    Same major version, at or below the device's minor. CUDA is forward
    compatible within a major version but not across one: an RTX 4090 (sm_89)
    runs happily on sm_86 kernels, while an sm_120 card runs on nothing below
    sm_120. Testing for an exact match instead would quietly demote every 4090
    to CPU.
    """
    return arch[0] == device[0] and arch[1] <= device[1]


def cuda_usable() -> tuple[bool, str]:
    """`(usable, reason)` — whether CUDA work will actually run.

    The reason is always worth showing: "no CUDA GPU detected" and "your GPU is
    newer than this build" both mean "running on CPU" but have completely
    different fixes, and conflating them is how someone concludes their new
    graphics card is broken.
    """
    try:
        import torch
    except Exception as e:
        return False, f"PyTorch did not load ({type(e).__name__})"

    try:
        if not torch.cuda.is_available():
            return False, NO_GPU

        name = torch.cuda.get_device_name(0)
        device = torch.cuda.get_device_capability(0)
        archs = [a for a in (_parse_arch(a) for a in torch.cuda.get_arch_list()) if a]

        # No architectures at all means a CPU-only build, which on Windows is
        # what a plain `pip install torch` gives you. Worth naming precisely,
        # because the GPU is real and the driver is fine.
        if not archs:
            return False, f"{name} found, but this is a CPU-only PyTorch build"

        if any(_runs_on(a, device) for a in archs):
            return True, name

        # WHICH SIDE the card falls on decides what the user should do, so the
        # message has to say. Reporting only the ceiling reads as nonsense for
        # an old card — a GTX 1060 owner is told "sm_61, build goes up to
        # sm_120", concludes 61 < 120 so it should work, and reports a bug.
        # The number that excludes them is the FLOOR.
        here = f"sm_{device[0]}{device[1]}"
        if device < min(archs):
            oldest = min(archs)
            return False, (
                f"{name} is compute capability {here}, which is older than the "
                f"oldest this PyTorch build supports (sm_{oldest[0]}{oldest[1]})"
            )
        newest = max(archs)
        return False, (
            f"{name} is compute capability {here}, and this "
            f"PyTorch build only goes up to sm_{newest[0]}{newest[1]}"
        )
    except Exception as e:
        # Never let a diagnostic take the caller down: an unreadable GPU is a
        # reason to use the CPU, not a reason to fail.
        return False, f"could not check the GPU ({type(e).__name__})"


def torch_device() -> str:
    """`"cuda"` or `"cpu"` — the device every model and inference call must use.

    Exists because "don't move the model to CUDA" turned out not to be enough.
    Ultralytics rebuilds its predictor on each call with
    `select_device(self.args.device)`, and that default "auto-selects the first
    available GPU" — so a model deliberately left on the CPU was silently put
    back on a GPU this build has no kernels for, and died at the first frame.
    A GTX 1060 owner hit exactly that, at the reactions stage, after preflight
    had already told him he was running on CPU.

    Passing this explicitly at every call site removes the dependency on a
    third-party default that can change under us, which is the real lesson.

    Cached: cuda_usable() imports torch and queries the driver, and this is
    called per frame. The answer cannot change inside one process.
    """
    global _DEVICE
    if _DEVICE is None:
        usable, _ = cuda_usable()
        _DEVICE = "cuda" if usable else "cpu"
    return _DEVICE
