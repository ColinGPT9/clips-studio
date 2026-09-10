# Known issues

Everything here has actually been observed, most of it while testing the alpha
on a clean machine. Nothing on this list is speculative, and nothing that has
been fixed is still listed as broken.

Hit something that is not here? The **💬 button on the Dashboard** files a
report without needing a GitHub account, or open an
[issue](https://github.com/ColinGPT9/clips-studio/issues).

---

## YouTube locks uploads to private until your API project is audited

If you publish from the editor and the video arrives on your channel as
**private** when you asked for public, this is why — and it is not something
Clips Kitty can fix.

YouTube restricts uploads made through the API by any Google Cloud project that
has not passed its free compliance audit. The lock is **permanent**: you cannot
change the video to public in YouTube Studio afterwards, and there is no appeal.
The only remedy is uploading the video again from an audited project.

Clips Kitty reads the privacy back after every upload and tells you when this
has happened, rather than reporting success for a video nobody can watch.

**What to do:** submit the
[YouTube API Services audit form](https://support.google.com/youtube/contact/yt_api_form)
before you rely on public publishing. Uploading as **unlisted** or **private** is
not affected, so it is a usable workflow in the meantime.

## Your YouTube sign-in expires weekly if the consent screen is on "Testing"

Google expires the sign-in for an OAuth app in *Testing* after 7 days, so
Clips Kitty asks you to reconnect every week.

**Fix:** in the Google Cloud Console, open the OAuth consent screen and press
**Publish app**. "In production" does not mean verified and costs nothing — you
will simply see a "Google hasn't verified this app" warning when connecting, and
**Advanced → Go to (unsafe)** gets past it. It is your own app warning you about
yourself.

---

## Only three AI models have actually been tested

Clips Kitty can run any model Ollama serves, and the Models page lists several.
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

**Top-down games get nothing at all, not just bad framing.** MOBAs, strategy
and tower-defence games — Dota 2, League, and anything viewed from above — have
no person-shaped subject on screen. Part of a clip's score measures whether a
human is visible and being emphasised, and with nothing detected that part is
zero rather than merely low. Scored gameplay therefore lands under the
threshold and the run finishes normally. **The app now tells you this where
the clips would have been**, with the numbers behind it — how many moments were
considered, the best score any of them reached, and how many had nobody on
screen — so a run that produces nothing explains itself rather than looking
broken. Lowering
**Minimum score** in Settings will start producing clips, but they are chosen
without the visual half of the signal, so expect them to be arbitrary.

**What works well:** IRL, just chatting, podcasts, vlogs and interviews. That
is what it is tuned for and tested on.

## A download can break when a site changes, until the next release

Downloading is handled by yt-dlp, which is bundled inside the app. Twitch, Kick
and YouTube change things regularly; yt-dlp fixes them within days, but the
copy inside Clips Kitty is fixed at build time.

So there is a window — from a site changing to the next Clips Kitty release —
where downloads from that one site fail even though the fix already exists.
Other sites keep working, which is the tell: **if Twitch works and Kick does
not, it is this, not your setup.**

Being worked on in
[#39](https://github.com/ColinGPT9/clips-studio/issues/39). If you hit it,
report it with the site and the error — it helps establish how often this
actually bites.

## Podcast mode frames one person per shot, on purpose

Podcast mode does not put two people on screen together, and does not
split-screen. That is a decision, not a limitation it fell into.

Earlier versions tried framing two people at once. Averaging two positions
pulled every shot back toward the centre and made both faces small — the
"everyone tiny" look — and the same person appearing in two camera angles got
counted as two speakers, producing a split screen of somebody with himself.

A podcast is already edited so the camera cuts to whoever is talking. So the
app follows that edit: detect the camera cuts, and within each shot punch in on
the person who matters. If a clip frames the *wrong* person, that is a real bug
worth reporting. Framing one person is intended.

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

Seen twice now, both times in a memory-capped virtual machine rather than on
bare metal: at 8 GB the job analysed a video and produced zero clips, and the
first contributor to get a full run through a container had to raise the limit
to **12 GB**. At 12 GB, roughly an hour of source video is the practical
ceiling. If you are running in Docker, Windows Sandbox, or a VM, that limit is
the setting to check first, because the failure looks like a crash rather than
a memory limit.

Still not seen on a normal 16 GB desktop. If you hit it on a machine that
should be capable, please report it with your specifications.

## Everything is slower without a graphics card

It works on CPU. Transcription, tracking and scoring all run, just far slower —
a video that takes minutes with an NVIDIA card can take hours without one. The
app detects this and picks a smaller AI model to compensate.
