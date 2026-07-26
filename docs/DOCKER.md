# Docker (for contributors)

```
docker compose up --build
```

The engine comes up on <http://localhost:8765> with Ollama beside it. Run
the tests without installing anything:

```
docker compose run --rm engine pytest
docker compose run --rm engine ruff check .
```

Pull a model the first time:

```
docker compose exec ollama ollama pull gemma3:4b
```

---

## What this is for

Working on the Python engine without installing Python, FFmpeg, PyTorch,
OpenCV and the rest on your own machine — and letting someone on Linux or
macOS contribute to a project whose app only ships for Windows.

**It is not how anyone installs Clips Studio.** Creators use the Windows
installer. Nothing here replaces that.

## What it deliberately cannot do

**The desktop app is not in the container.** Electron needs a display and a
real windowing system; containerising that costs more than installing Node.
Run the UI on your host against the containerised engine:

```
docker compose up -d          # engine on :8765
cd ui && BACKEND_EXTERNAL=1 npm run dev
```

`BACKEND_EXTERNAL=1` stops Electron spawning its own backend, so it talks to
the container instead.

**The image is CPU-only PyTorch.** That is on purpose: the CUDA wheels are
around 4 GB, and a container is for editing code and running tests. Whisper
and YOLO both run, just slowly. Do GPU work on the host, where the app
actually ships.

If you do want GPU inside Docker: install the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
uncomment the `deploy` block under `ollama` in `docker-compose.yml`, and
build the engine against the CUDA wheels instead of the CPU index. On Windows
this needs WSL2 and Docker Desktop with GPU support enabled — several moving
parts, each of which can fail quietly.

**Clip quality still cannot be judged here.** Same limit as CI: no GPU, no
real footage. See `tests/README.md`.

## How it is wired

| Piece | Why |
|---|---|
| Source bind-mounted to `/app` | Edits are live; no rebuild to run a test |
| `clips-data` volume at `/data` | Downloads and clips survive a rebuild, and never bloat the image |
| `ollama-models` volume | Models are gigabytes; losing them on every rebuild would be miserable |
| `CLIPS_STUDIO_OLLAMA_HOST` | Inside compose, Ollama is a service name, not localhost |
| Ports bound to `127.0.0.1` | The API has no authentication and can read and write video files. It must not be reachable from the network. |

The engine binds `0.0.0.0` **inside** the container, because there
"localhost" means the container itself and nothing on the host could reach
it. The published port is still host-only, and `main.py serve` warns when
asked to bind anything other than `127.0.0.1`.

## First build is slow

Ten minutes or so, mostly PyTorch and OpenCV. Afterwards Docker caches the
dependency layer, and only a change to `requirements.txt` re-triggers it.

`.dockerignore` keeps `data/` out of the build context — without it Docker
would upload every downloaded video and rendered clip before running a single
step.
