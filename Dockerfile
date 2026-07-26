# Contributor environment for the Clips Studio engine.
#
# This is NOT how anyone installs Clips Studio — that is the Windows
# installer. This image exists so someone can work on the Python engine, run
# the tests and hit the API without installing Python, FFmpeg, PyTorch and
# the rest on their own machine, and so a contributor on Linux or macOS can
# work on a project whose app only ships for Windows.
#
# The desktop app is not in here. Electron needs a display and a real
# windowing system; running it in a container would be more trouble than
# installing Node. Run the UI on your host against this engine — see
# docs/DOCKER.md.

FROM python:3.11-slim

# ffmpeg      — every decode, encode and probe in the pipeline
# libgl1 etc. — OpenCV imports fail without them on slim images, with an
#               error that names libGL rather than cv2 and wastes an hour
# git         — yt-dlp and some tooling shell out to it
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU PyTorch, installed FIRST so the requirements below resolve against it
# rather than pulling ~4 GB of CUDA wheels. A container is for editing code
# and running tests; if you want GPU work, do it on the host where the app
# actually ships. docs/DOCKER.md explains the GPU variant.
COPY requirements.txt requirements-build.txt ./
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        torch torchvision \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir ruff pytest

# Source is bind-mounted by compose so edits are live; this copy is what
# makes the image useful on its own.
COPY . .

# Data lives on a volume, not in the image — a container should never be
# where someone's downloads and clips quietly accumulate.
ENV CLIPS_STUDIO_DATA_DIR=/data
VOLUME ["/data"]

EXPOSE 8765

# Bind to 0.0.0.0 rather than 127.0.0.1: inside a container, localhost is
# the container, and nothing on the host could reach it.
CMD ["python", "main.py", "serve", "--port", "8765", "--host", "0.0.0.0"]
