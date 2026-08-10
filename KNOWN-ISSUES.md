# Known issues

Everything here has actually been observed, most of it while testing the alpha
on a clean machine. Nothing on this list is speculative, and nothing that has
been fixed is still listed as broken.

Hit something that is not here? The **💬 button on the Dashboard** files a
report without needing a GitHub account, or open an
[issue](https://github.com/ColinGPT9/clips-studio/issues).

---

## Only three AI models have actually been tested

Clips Studio can run any model Ollama serves, and the Models page lists several.
**Only these three have been run against real streams:**

- `gemma:7b`
- `gemma3:4b`
- `gemma3:12b`

Everything else — Gemma 4, Qwen3, Mistral Nemo, Phi-4, Llama — is listed
because it is a sensible size for the hardware and is free to use commercially,
**not** because clip quality has been measured with it. They should work; the
app talks to all of them the same way. Nobody has checked whether they pick
better or worse moments.

If you try one, saying how it went is genuinely useful — that is a gap that
only gets closed by people running different models on different content.

## Windows warns that the app is unsigned

On first run you get **"Windows protected your PC"**. Click *More info → Run
anyway*.

The installer is not code-signed, because a certificate costs money this
project does not have yet. The warning is about the **absence of a signature**,
not about anything Windows found in the file. Signing is on the roadmap.

## Gaming, split-screen and reaction videos are not supported

The app will still run on them and produce clips, but the framing will be poor.

Two things were not good enough in testing. The framing did not reliably find
the part of the screen where the action was, and subject tracking mistook
characters *inside the game* for the streamer — to a person detector, a person
on screen is a person on screen. The result was clips centred on the wrong
human, so it is held back rather than shipped half-working.

Reaction videos sit out for a related reason: clips are chosen from what is
**said**, and the app cannot see the video you are reacting to.

**What works well:** IRL, just chatting, podcasts, vlogs and interviews. That
is what it is tuned for and tested on.

## The app looks frozen while it is scoring

After transcription there is a long stretch — often the longest part of the
whole job — where nothing appears to happen and the log says nothing. It is
working; the scoring stage does not report progress yet.

**How to tell:** open Task Manager. If `ollama.exe` is using CPU, it is
scoring. Being unable to distinguish this from a crash is a real problem and it
is being fixed.

## Updating shows no progress after the first megabyte

The update downloads a small installer, shows 1 MB / 1 MB and 100%, and then
appears to stop. It has not stopped — it is fetching the ~6 GB payload behind
that, and the tool it uses does not report progress for that part.

Leave it running. A **Restart & install** button appears when it finishes.

## A failed update disappears silently

If an update fails, the banner vanishes rather than saying so, and no log is
kept. If an update seems to go nowhere, download the installer from the
[releases page](https://github.com/ColinGPT9/clips-studio/releases) instead —
installing over the top works fine and keeps your settings and models.

## Low-memory machines may fail to render

Seen on a machine with **8 GB of RAM and no graphics card**: the whole job runs,
picks its clips, and then every render fails with an out-of-memory error from
the encoder.

**16 GB is the recommended minimum**, and a graphics card matters more than
anything else here — with one, the AI model sits in video memory instead of
competing with the encoder for system memory.

If you hit it on a machine that should be capable, please report it with your
specifications; it has not yet been seen on real hardware.

## Everything is slower without a graphics card

It works on CPU. Transcription, tracking and scoring all run, just far slower —
a video that takes minutes with an NVIDIA card can take hours without one. The
app detects this and picks a smaller AI model to compensate.
