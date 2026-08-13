# Microsoft Store listing copy

Draft for review, not final. Paste into Partner Center's **Store listings**
page. Character limits are Microsoft's.

Two rules this copy follows deliberately:

- **No comparative claims.** Not "an OpusClip alternative", not "better than".
  Store policy 11.2 covers third-party names, and a listing that leans on
  someone else's product reads as derivative even where it is permitted. The
  positioning is what Clips Studio *is* — local, open source, yours — which is
  the genuine difference anyway.
- **No unmeasured performance numbers.** Nothing about speed appears here that
  is not in the README's tested-hardware table.

---

## Product name

```
Clips Studio
```

## Short description (1,000 max)

```
Turn long videos into vertical clips with AI that runs on your own PC. Paste a YouTube, Twitch or Kick link, or open a video file, and Clips Studio finds the moments worth clipping, follows whoever is speaking, adds subtitles and renders them ready to post. Your footage never leaves your computer. No subscription, no upload, no clip limit. Free and open source.
```

## Description (10,000 max)

```
Clips Studio finds the best moments in a long video and cuts them into vertical clips for Shorts, TikTok and Reels. Give it a YouTube, Twitch or Kick link, or a video file from your own disk, and it does the rest.

Everything runs on your computer.

That is the part that makes it different. Most AI clipping tools upload your video to a server, charge a monthly fee, and cap how many clips you get. Clips Studio does the transcription, the scoring, the speaker tracking, the subtitles and the rendering locally, on your own hardware. Your footage is never uploaded. There is no subscription, no clip limit, and no account to create.

WHAT IT DOES

• Finds the moments — transcribes the whole video, then scores every candidate on what was said, how the audience reacted, and what is happening on screen
• Keeps the speaker in frame — tracks who is actually talking, by lip movement rather than by who is biggest in the shot, so a two-person conversation does not jump to the wrong face
• Writes the titles — a local language model drafts a title and description for each clip
• Burns in subtitles — word-level timing, styled, in the language of the clip
• Speaks 19 languages — translate, subtitle and dub clips into any of them
• Learns a creator — recurring jokes, catchphrases and running bits feed into how their moments are scored
• Longer edits too — assemble a long-form cut, not only shorts
• Edit before you publish — adjust the crop, the captions, the music and the branding

YOUR CHOICE OF AI MODEL

The language model runs through Ollama, on your machine, and you pick it. A small model runs on a laptop with no graphics card; a larger one gives better scoring if you have the VRAM for it. The app recommends one based on your hardware and downloads it for you on first run.

FREE AND OPEN SOURCE

The whole thing is on GitHub under the AGPL-3.0 licence. You can read exactly what it does, including every line that touches the network. Bug reports, translations and pull requests are welcome — 18 of the 19 interface translations have never been checked by a native speaker, and that is an open invitation.

WHAT YOU NEED

• Windows 10 or 11, 64-bit
• 16 GB of RAM. This is a requirement, not a suggestion: with less, a video is analysed and then rendering fails.
• About 20 GB of disk, plus room for your videos
• A graphics card is not required, but an NVIDIA one makes it substantially faster

ALPHA SOFTWARE

Clips Studio is version 0.1.2 and it is early. It works, and it is rough in places. The known issues are listed openly in the repository, and there is a feedback button in the app that files a report for you without needing a GitHub account.

It was built for IRL, just-chatting and talking-head content, which is what it has been tested on. Gaming footage with a busy background is the weakest case today.
```

## App features (200 chars each, 20 max)

```
Runs entirely on your own PC — your video is never uploaded
Works from YouTube, Twitch and Kick links, or your own video files
Finds clip-worthy moments using speech, audience reaction and what is on screen
Tracks the active speaker by lip movement, so the right face stays in frame
Burns in word-timed subtitles
Translates, subtitles and dubs into 19 languages
Choose your own local AI model to match your hardware
Learns a creator's recurring jokes and catchphrases
Assembles long-form edits as well as shorts
No subscription, no account, no clip limit
Free and open source under AGPL-3.0
```

## Search terms (7 max, 40 chars each, 21 unique words total)

Policy 10.1.3: at most seven, relevant, no pricing words, no other products'
names. These are phrases someone would actually type, not a keyword dump.

```
ai video clipper
local ai video editing
twitch clip maker
youtube shorts maker
vertical video editor
open source video ai
auto subtitle generator
```

## Copyright

```
Copyright (c) 2026 ColinGPT9. AGPL-3.0.
```

---

## Screenshots

**Required: 1. Recommended: 4 or more. Maximum: 10.** PNG, 1366x768 or larger.

Capture on a clean install with a real video, not placeholder data — a listing
with obviously fake content reads as fake.

| # | Shows | Why it earns the slot |
|---|---|---|
| 1 | The queue mid-job, progress bar and ETA visible | The first screenshot is the one everybody sees. It should show the app *working*. |
| 2 | The clips grid with real thumbnails and scores | Proves it produced something, and that the scoring is visible rather than magic |
| 3 | The editor with a clip open, subtitles and crop visible | Answers "can I change what it decided?" |
| 4 | The model picker with the hardware recommendation | This is the local-AI story, which is the whole positioning |
| 5 | Settings on the language selector | Shows the 19 languages are real |
| 6 | A long-form assembly, if it looks good | Optional, and only if it does |

Avoid: empty states, error messages, anything with a real person's face you do
not have permission to use, and anything showing a channel name you have not
cleared.

## Store logos

- **1:1 box art — required.** 300x300 minimum. `site/assets/mascot.png` is
  1024x1024 and works directly.
- **2:3 poster art — recommended.** 720x1080. Needs making; the mascot centred
  on the app's dark background (`#0A1628`) is enough.

## Trailer

Optional, and issue #35 already tracks making a demo video. If one exists by
submission time, add it — a fifteen-second clip of a link going in and a
finished vertical clip coming out is worth more than any of the screenshots.
