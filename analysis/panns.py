"""PANNs MobileNetV1: an AudioSet sound tagger, for game sounds.

The network is Kong et al.'s MobileNetV1 from "PANNs: Large-Scale Pretrained
Audio Neural Networks for Audio Pattern Recognition" (2020), adapted from
qiuqiangkong/audioset_tagging_cnn, pytorch/models.py:

    Copyright (c) 2018-2020 Qiuqiang Kong. MIT License: permission is hereby
    granted, free of charge, to any person obtaining a copy of this software
    and associated documentation files (the "Software"), to deal in the
    Software without restriction, including without limitation the rights to
    use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions: The above
    copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED
    "AS IS", WITHOUT WARRANTY OF ANY KIND.

Not in third_party/: that folder holds verbatim copies, and upstream builds
its front end on torchlibrosa and librosa, which this app doesn't ship. The
layers and their names are upstream's, so the published checkpoint loads
as-is; what changed is the front end (torch.stft, and the mel filterbank
the checkpoint carries), and training-only parts (SpecAugment, mixup,
dropout) are gone. The weights (Zenodo record 3987831, CC BY 4.0) are
fetched by scripts/fetch_panns.py.
"""

import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
WEIGHTS = "panns_mobilenetv1.pth"
LABELS = _ROOT / "config" / "audioset_labels.txt"

# What the network was trained on; none of these are free parameters.
SAMPLE_RATE = 32000
N_FFT = 1024
HOP = 320
MEL_BINS = 64
CLASSES = 527

_model = None


def weights_path() -> Path:
    """Where the checkpoint lives, frozen build or checkout."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / WEIGHTS  # type: ignore[attr-defined]
    return _ROOT / "models" / WEIGHTS


def available() -> bool:
    """True when game sounds can be listened for. Without the weights the
    gaming profile goes on without this signal."""
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return weights_path().exists()


def labels() -> list[str]:
    """The 527 AudioSet class names, in the network's output order."""
    return LABELS.read_text(encoding="utf-8").splitlines()


def _network():
    import torch
    from torch import nn

    def conv_bn(inp, oup, stride):
        return nn.Sequential(nn.Conv2d(inp, oup, 3, 1, 1, bias=False), nn.AvgPool2d(stride),
                             nn.BatchNorm2d(oup), nn.ReLU(inplace=True))

    def conv_dw(inp, oup, stride):
        return nn.Sequential(nn.Conv2d(inp, inp, 3, 1, 1, groups=inp, bias=False), nn.AvgPool2d(stride),
                             nn.BatchNorm2d(inp), nn.ReLU(inplace=True),
                             nn.Conv2d(inp, oup, 1, 1, 0, bias=False), nn.BatchNorm2d(oup),
                             nn.ReLU(inplace=True))

    class MobileNetV1(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer("window", torch.hann_window(N_FFT, periodic=True))
            self.register_buffer("melW", torch.zeros(N_FFT // 2 + 1, MEL_BINS))
            self.bn0 = nn.BatchNorm2d(MEL_BINS)
            self.features = nn.Sequential(
                conv_bn(1, 32, 2), conv_dw(32, 64, 1), conv_dw(64, 128, 2), conv_dw(128, 128, 1),
                conv_dw(128, 256, 2), conv_dw(256, 256, 1), conv_dw(256, 512, 2),
                conv_dw(512, 512, 1), conv_dw(512, 512, 1), conv_dw(512, 512, 1),
                conv_dw(512, 512, 1), conv_dw(512, 512, 1), conv_dw(512, 1024, 2),
                conv_dw(1024, 1024, 1))
            self.fc1 = nn.Linear(1024, 1024, bias=True)
            self.fc_audioset = nn.Linear(1024, CLASSES, bias=True)

        def forward(self, wave):
            """wave: (batch, samples) at 32 kHz -> (batch, 527) probabilities."""
            spec = torch.stft(wave, N_FFT, HOP, N_FFT, self.window, center=True,
                              pad_mode="reflect", return_complex=True)
            power = spec.real ** 2 + spec.imag ** 2                  # (batch, freq, time)
            mel = torch.matmul(power.transpose(1, 2), self.melW)    # (batch, time, mel)
            x = (10.0 * torch.log10(torch.clamp(mel, min=1e-10))).unsqueeze(1)
            x = self.bn0(x.transpose(1, 3)).transpose(1, 3)
            x = self.features(x)
            x = torch.mean(x, dim=3)
            x = torch.max(x, dim=2)[0] + torch.mean(x, dim=2)
            x = torch.relu(self.fc1(x))
            return torch.sigmoid(self.fc_audioset(x))

    return MobileNetV1()


def load():
    """The network with its weights, on the GPU when the build can use one.
    Cached: loading costs a second, a video only needs it once."""
    global _model
    if _model is not None:
        return _model
    import torch

    # NOT torch.cuda.is_available(): see core/gpu.py.
    from core.gpu import torch_device

    device = torch_device()
    state = torch.load(weights_path(), map_location="cpu", weights_only=True)
    state = state.get("model", state)
    model = _network()
    # The checkpoint's STFT is a convolution with the window baked in
    # (torchlibrosa); torch.stft computes the same thing, so only its mel
    # filterbank is kept.
    state = {k: v for k, v in state.items() if not k.startswith("spectrogram_extractor.")}
    state["melW"] = state.pop("logmel_extractor.melW")
    state["window"] = model.window
    model.load_state_dict(state)
    model.eval().to(device)
    _model = model
    print(f"      Game sounds (PANNs) loaded on {device}")
    return _model


def tag(windows: np.ndarray, batch: int = 32) -> np.ndarray:
    """(n, samples) float32 audio at 32 kHz -> (n, 527) probabilities."""
    import torch

    model = load()
    device = next(model.parameters()).device
    out = []
    with torch.inference_mode():
        for i in range(0, len(windows), batch):
            x = torch.from_numpy(np.ascontiguousarray(windows[i:i + batch], dtype=np.float32)).to(device)
            out.append(model(x).float().cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, CLASSES), dtype=np.float32)
