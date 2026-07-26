# Docker (for contributors)

```
docker compose up --build
```

Three services, and you need nothing installed but Docker:

| | |
|---|---|
| **engine** | <http://localhost:8765> — the Python pipeline and its API |
| **ui** | <http://localhost:5173> — the interface, in your browser |
| **ollama** | the model the scoring runs against |

Run the checks without installing Python:

```
docker compose run --rm engine pytest
docker compose run --rm engine ruff check .
docker compose run --rm ui npm run typecheck
```

Pull a model the first time:

```
docker compose exec ollama ollama pull gemma3:4b
```

Generate a test video to actually run something against — see
[`../tests/assets/`](../tests/assets/):

```
docker compose run --rm engine python tests/assets/make_sample_video.py
```

---

## What this is for

Working on Clips Studio without installing Python, FFmpeg, PyTorch, OpenCV,
Node and the rest on your own machine — and letting someone on Linux or
macOS contribute to a project whose app only ships for Windows.

**It is not how anyone installs Clips Studio.** Creators use the Windows
installer. Nothing here replaces that.

## The one piece that is not in the container: Electron

The `ui` service runs Vite, so the **interface** is containerised and you can
work on it in a browser. What is not containerised is the Electron shell
around it, which needs a display and a real windowing system — forwarding
that through X or VNC costs more than it gives.

In a browser everything that talks to the engine works normally, because that
is all HTTP to `:8765`. What is missing is the handful of things that live in
Electron's preload — native file pickers, the donate window, opening external
links. Rather than crashing on those, the renderer installs a stand-in
(`ui/src/renderer/src/lib/browserShim.ts`) that logs what it cannot do. Open
the console and you will see exactly which call it was.

Without Docker, the same thing is:

```
cd ui && npm run dev:web
```

That covers most interface work. When you need the real thing — anything
touching IPC, the preload, the updater, window behaviour, or the packaged
app — run Electron on your host against the containerised engine:

```
docker compose up -d engine ollama      # engine on :8765
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

**Clip quality still cannot be judged here.** Same limit as CI: no GPU, and
the generated test video has no faces and no speech, so it exercises the
plumbing and nothing about the judgment. See
[`../tests/assets/README.md`](../tests/assets/README.md).

## How it is wired

| Piece | Why |
|---|---|
| Source bind-mounted to `/app` | Edits are live; no rebuild to run a test |
| `ui-node-modules` volume | The container keeps its own. esbuild and rollup ship platform-specific binaries, and a host's Windows `node_modules` mounted over the top fails as "cannot find module" for a package that is plainly installed. |
| `CHOKIDAR_USEPOLLING` on `ui` | inotify does not cross a bind mount from a Windows or macOS host, so hot reload needs polling |
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
