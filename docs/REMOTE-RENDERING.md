# Remote rendering

**Let another computer of yours render the clips, so the one you're using
stays free while a long stream or a big batch is processed.**

It is for people with more than one PC: a gaming PC and a laptop, or an
always-on box in the corner. It is off by default, and nothing about it shows
anywhere in the app until you switch it on under **Settings → Advanced
settings**.

Only the rendering moves. Your main PC still downloads the video, transcribes
it, finds the moments with the AI, keeps the library and publishes. The render
PC gets one clip's stretch of video at a time, renders it with its own
graphics card, and sends the finished clip back. Clips rendered on the other PC
are identical to ones rendered here: it runs the same rendering code with the
same settings (measured: pixel-identical frames, face tracking and captions
included).

It works for every kind of clip: standard, Gaming / Reaction, Vertical Live
(the whole 9:16 frame kept, no face tracking) and longform.

## Setting it up

You need Clips Kitty on both computers, the **same version**, and both on the
same home network, or both on the same [Tailscale](https://tailscale.com)
tailnet (see below for PCs in different places).

**On the main PC** (the one you use):

1. Settings → **Advanced settings** → turn on **Remote rendering**.
2. Press **Add a render PC**. It shows this PC's address, a pairing code
   (`ABCD-EFGH`, good once, for ten minutes) and its certificate.
3. Windows asks whether Clips Kitty may accept connections on your network.
   Allow it on **private** networks.

**On the render PC:**

1. Settings → **Advanced settings** → turn on **Remote rendering**.
2. Under **Use this PC as a render worker**, enter the main PC's address and the
   code, and press **Pair**. It shows the certificate it connected to; it should
   match the one on the main PC.
3. Leave Clips Kitty open. While it runs, it renders whatever the main PC sends.

For an always-on box without anyone at it, the render worker also runs on its
own, with no window:

```
api.exe render-worker --pair 192.168.1.20:8766 ABCD-EFGH    # once
api.exe render-worker                                       # from then on
```

(`python main.py render-worker ...` from a source checkout.)

## Choosing where clips render

Back on the main PC, **Render clips on**:

- **This computer**: as always. The default.
- **Automatic**: a render PC when one is on and can take the clip; this
  computer when none is. A clip a render PC fails (after two retries), or that
  no render PC picks up within a minute, renders here.
- **Only on <a render PC>**: always there. If it's off, the clips wait for it,
  and the progress bar offers **Render here instead**. Nothing falls back
  without you choosing it.
- A clip with music you added in the editor renders on this computer
  whichever you choose: the music is a file here, and a render PC can't
  fetch it.

The progress bar says where each clip is: "Rendering clip 3/10 · on Gaming PC ·
uploading 62%".

What goes remote: new videos, **Make clips again**, watched channels and
longform. Re-rendering one clip from the editor and dubbing stay on this
computer, because they are one clip you're waiting to look at.

## The render PCs list

Each render PC shows whether it's online, idle or rendering (and what), its
graphics card and memory, the video encoder it can actually use (tested, not
guessed from the card's name: a card whose driver is too old for the bundled
FFmpeg shows CPU encoding), whether it can do face tracking, and its version.

- **Stop accepting jobs** (on either PC): finishes the clip it's on and takes no
  more. For switching a gaming PC back to gaming, or shutting it down.
- **Clips at a time** (on the render PC): how many it renders at once. 1 is the
  default and safe on any graphics card.
- **Remove**: unpairs it. Its credential stops working at once, and any clip it
  held goes back in the queue.

## If something goes wrong

- **A render PC switches off mid-clip**: after 45 seconds without hearing from
  it, its clips go back in the queue, and go to another render PC or (in
  Automatic) render here. A clip is never made twice: every clip has one job
  number, and only the first finished copy counts.
- **The network drops mid-transfer**: transfers carry on from where they
  stopped, in both directions.
- **A clip arrives damaged**: every file is checked (size, checksum, and that it
  plays for the right length: its window, or what its edit keeps of it) before
  it's accepted; a damaged one is rendered again.
- **Cancel**: cancelling a video stops its clips on the render PC too, at its next
  check-in (within ten seconds), including any FFmpeg it started.
- **"Worker update required"**: the two PCs run different versions. Install the
  same version on both.
- **Can't reach the main PC**: check both are on the same network (or tailnet),
  that Windows allowed Clips Kitty on private networks on the main PC, and the
  port (8766 by default).

## PCs in different places

Remote rendering never needs your router opened. For two PCs on different
networks, put both on the same [Tailscale](https://tailscale.com) tailnet (free
for personal use): each gets a private address that works from anywhere,
encrypted, visible only to your own devices. Use the main PC's tailnet address
(the one starting 100.) when pairing. Clips Kitty has no server of its own in
between, and there is no cloud rendering.

## Security

- The render PC connects **out** to the main PC. The main PC listens for render
  PCs on its own port (8766), separate from the app's own local API, which
  never leaves the PC.
- It's HTTPS with a certificate the main PC makes for itself. The render PC
  remembers that certificate when it pairs and talks to nothing else.
- Pairing codes work once, for ten minutes, and five wrong tries void them.
  Every render PC gets its own credential; the main PC stores only a hash of it.
- A render job is a description of the clip to make, never a command. The
  render PC only ever runs Clips Kitty's own rendering on it.
- What a render PC receives: the clip's stretch of video, the transcript lines
  inside it (for captions), the render settings (caption style, layout,
  filters, end card), and a watermark image if you use one. Never your AI keys,
  publishing accounts, YouTube sign-in or library.

## For contributors

`remote_render/` holds all of it; its `__init__.py` maps the modules. The
render step (`core.pipeline._render_files`) is unchanged and shared: the worker
calls it in a child process with the job's settings and the clip's times
shifted into the piece. With remote rendering off, `dispatch.renderer_for`
returns None and the pipeline's local loop runs as before. Tests:
`tests/test_remote_render.py`.
