# Awesome Clips Kitty

Apps, pipelines, plugins, models, workflows, integrations and tools for [Clips Kitty](https://github.com/ColinGPT9/clips-studio), the free app that turns long videos into short clips on your own PC.

This is a curated directory, not a list of everything: a maintainer checks each entry against the [inclusion criteria](CONTRIBUTING.md#what-gets-in) before it is listed in its section. Entries added by their authors and not checked yet are shown separately, under "Not yet checked". The same data, in [`registry/`](registry/), is what the Clips Kitty Marketplace shows, so adding a project here also makes it discoverable inside the app.

**How a project relates to Clips Kitty**

- **Built for Clips Kitty**: runs inside Clips Kitty. These are the pipelines and plugins you install from the Marketplace.
- **Built with Clips Kitty**: a separate app or tool that uses Clips Kitty's local API or SDK.
- **Related**: relevant to the ecosystem, not connected to Clips Kitty yet. Some are candidates for an adapter that would let them run through Clips Kitty.

**Labels**

- **✓ Official**: made and maintained by the Clips Kitty project.
- **✓ Compatible**: this version passed Clips Kitty's automated checks (its manifest is valid, it installs, its requirements are met, and it runs on a sample video and gives an answer Clips Kitty accepts). A technical label, not a security review.
- **★ Featured**: picked by hand by a maintainer.
- Everything else is **Community**: made by someone outside the project, and nobody at Clips Kitty has read its code.

The numbers measure different things and are never added together: Clips Kitty installs, stars on GitHub, and a model's Hugging Face downloads. Every project keeps its own licence, shown like `MIT`, with a licence note where its models or parts have other terms. A ⚠ marks what to know before using a project: it sends your videos, audio or transcripts to an online service by default, downloads from sites whose terms may not allow it, has usage tracking switched on, or is archived or has had no commits for over a year.

The first entries come from a check of 308 open-source projects on 2026-10-07: each one's licence file, latest commit and documentation were read, and nothing was installed or run. The projects that didn't make it, and why, are in the [research notes](../docs/platform/research-notes/ecosystem-projects.md).

<!-- generated: everything from here to the end marker comes from registry/ -->

## Contents

- [Apps](#apps)
  - [Companion apps](#companion-apps)
  - [Open-source video clipping](#open-source-video-clipping)
  - [Open-source video AI](#open-source-video-ai)
  - [Gaming](#gaming)
  - [Automation](#automation)
- [Pipelines](#pipelines)
  - [General](#general)
- [Models](#models)
  - [Speech to text](#speech-to-text)
  - [Sound and audio events](#sound-and-audio-events)
  - [Detection and tracking](#detection-and-tracking)
  - [Moment and highlight detection](#moment-and-highlight-detection)
  - [Reading text on screen](#reading-text-on-screen)
  - [Describing images and video](#describing-images-and-video)
  - [Speakers and voice activity](#speakers-and-voice-activity)
  - [Language models](#language-models)
- [Workflows](#workflows)
  - [ComfyUI](#comfyui)
- [Integrations](#integrations)
  - [Streaming](#streaming)
  - [AI assistants](#ai-assistants)
  - [Publishing](#publishing)
  - [Chat and community](#chat-and-community)
  - [Games](#games)
- [Tools](#tools)
  - [For developers](#for-developers)
  - [Finding moments](#finding-moments)
  - [Transcription](#transcription)
  - [Captions and subtitles](#captions-and-subtitles)
  - [Translation and dubbing](#translation-and-dubbing)
  - [Audio and video processing](#audio-and-video-processing)
  - [Scene detection](#scene-detection)
  - [Detection and tracking](#detection-and-tracking)
  - [Automatic editing](#automatic-editing)
  - [Game replays and events](#game-replays-and-events)
  - [Sports analysis](#sports-analysis)
  - [Stream recording and downloads](#stream-recording-and-downloads)
  - [Running AI locally](#running-ai-locally)
- [Wanted](#wanted)

## Apps

### Companion apps

_Apps that go well with Clips Kitty, such as editors and recorders._

- [Fireshare](https://github.com/fireshare-app/fireshare) - Self-hosted web app for sharing game clips and other videos through unique links. It scans a video folder, such as your clip exports, and can send Discord or webhook notices for new videos. `GPL-3.0` · runs locally
- [Kdenlive](https://github.com/KDE/kdenlive) - KDE's free timeline video editor for finishing clips. Its project files and command-line rendering let other tools hand over a ready-made timeline. `GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL` · runs locally  
  Licence note: The KDE-Accepted-GPL option allows later GPL versions approved by KDE e.V.
- [LosslessCut](https://github.com/mifi/lossless-cut) - Desktop app for fast lossless trimming and cutting of video and audio without re-encoding. It can import segment lists such as CSV, so a list of clip timings can be cut in one go. `GPL-2.0-only` · runs locally
- [OBS Studio](https://github.com/obsproject/obs-studio) - Desktop software for recording and live streaming, with a replay buffer that saves the last moments on a hotkey. Its recordings are the usual starting point for clips. `GPL-2.0-or-later` · runs locally
- [OpenCut](https://github.com/OpenCut-app/OpenCut) - Free, open video editor in the spirit of CapCut for trimming and assembling clips. This repository holds a ground-up rewrite that is still in progress. `MIT` · runs locally
- [OpenShot Video Editor](https://github.com/OpenShot/openshot-qt) - Beginner-friendly video editor for finishing clips, with titles, keyframe animation, unlimited tracks and EDL or XML timeline import. `GPL-3.0-or-later` · runs locally
- [Shotcut](https://github.com/mltframework/shotcut) - Free cross-platform timeline video editor for finishing clips. It opens MLT project files, so a list of moments can be handed over as a ready-made timeline. `GPL-3.0-or-later` · runs locally
- [Subtitle Edit](https://github.com/SubtitleEdit/subtitleedit) - Desktop subtitle editor for fixing caption timing and text against the video and waveform, converting between hundreds of formats, and running speech-to-text, OCR and translation. `MIT` · local or cloud  
  ⚠ Its optional yt-dlp add-on downloads videos from online sites; their terms may not allow it for videos you don't own.

### Open-source video clipping

_Open-source apps that turn long videos into short clips. Not connected to Clips Kitty yet; the ones with a licence and design that allow it are candidates for adapters._

- [AutoClip](https://github.com/artbyjazi/autoclip) - Local-first app with a web UI and CLI that turns a long video into ranked vertical, square or wide clips with burned-in captions and speaker-tracked reframing, using faster-whisper, Ollama and MediaPipe. `MIT` · local or cloud  
  Licence note: Optional speaker diarization uses gated pyannote models: sign in to Hugging Face and accept their terms first.  
  ⚠ Downloads YouTube videos with yt-dlp and suggests browser cookies to get past bot checks; YouTube's terms may not allow it for videos you don't own.
- [AutoClip](https://github.com/zhouxiaoka/autoclip) - Desktop and web app that turns podcasts, interviews and courses into shorts with captions, covers and post text. It can transcribe with local Whisper or SenseVoice and pick moments with Ollama or LM Studio. `MIT` · local or cloud  
  ⚠ Sends usage analytics (PostHog) and crash reports (Sentry) by default; turn off in Settings. Downloads videos from YouTube and other sites, which their terms may not allow.
- [ClipMint](https://github.com/kleZ799/clipmint) - Desktop app that transcribes podcasts, vlogs and stream VODs locally, ranks moments with a cloud LLM and renders vertical clips with captions and a webcam-over-gameplay layout. `MIT` · local or cloud  
  ⚠ Sends transcripts to Gemini, Groq or OpenAI to rank moments. Downloads YouTube videos, optionally with your browser cookies; YouTube's terms may not allow it.
- [FunClip](https://github.com/modelscope/FunClip) - Local app that transcribes video with FunASR speech models and lets you cut clips by picking transcript text or a speaker, with SRT subtitles for each clip. Well suited to Chinese-language videos. `MIT` · local or cloud  
  Licence note: The MIT licence covers the code only. Speech models download separately under their own licences: the README says the default Paraformer and CAM++ models list Apache-2.0, while SenseVoice uses the FunASR Model License, which requires attribution.  
  ⚠ Optional LLM clipping sends transcripts to cloud APIs. Its keyless g4f option routes text through third-party providers, some via browser automation; check their terms.
- [Hi-Lite](https://github.com/seanng-1/hi-lite) - Desktop app that transcribes gameplay, podcast or sports commentary locally, cuts one clip per spoken segment and lets you or a local LLM pick the best. It stitches the picks into a highlight reel. `GPL-3.0-or-later` · local or cloud
- [HotClip](https://github.com/xixihhhh/hotclip) - Desktop app that finds highlights in long videos and livestream VODs from the transcript, loudness, shots, faces and chat, then exports captioned vertical clips. Transcribes locally and can use Ollama. `AGPL-3.0-only` · local or cloud  
  ⚠ Sends transcripts to Atlas Cloud by default unless set to Ollama. Downloads Bilibili and YouTube videos, which their terms may not allow. Its anti-fingerprint feature dodges repost checks.
- [Open Clipper](https://github.com/GrepCut/OpenClipper) - Windows desktop app that transcribes long videos locally, reframes them to vertical with GPU face and subject tracking, styles captions and batch-exports. Clip picking is done by an outside AI agent over MCP. `MIT` · local or cloud  
  Licence note: The bundled InsightFace SCRFD face weights are for non-commercial research only; commercial distribution needs other weights. The YOLOX-S and MoveNet weights are Apache-2.0.
- [OpenClip](https://github.com/linzzzzzz/openclip) - Python tool with a web UI and CLI that transcribes long videos, asks an LLM to rank the most engaging moments and cuts clips with subtitles, titles and covers. `MIT` · local or cloud  
  Licence note: Optional speaker identification uses the gated pyannote/speaker-diarization-community-1 model; sign in to Hugging Face and accept its terms first. Whisper and Paraformer weights come under their own licences.  
  ⚠ Sends transcripts to Alibaba's Qwen API by default to pick moments. Downloads from YouTube and Bilibili, whose terms may not allow it for videos you don't own.
- [OpenShorts](https://github.com/mutonby/openshorts) - Self-hosted web app that turns long videos into vertical shorts with Whisper transcription, LLM moment picking, face-tracked reframing and subtitles. It can use a local LLM through Ollama. `MIT` · local or cloud  
  Licence note: Everything in the cloud/ folder is under the source-available OpenShorts Commercial License. Its Remotion render service is free only for individuals, non-profits and companies with up to 3 employees.  
  ⚠ Sends transcripts to Google Gemini by default to pick moments, and video frames to pick layouts. Downloads videos from YouTube; its terms may not allow it for videos you don't own.
- [podcli](https://github.com/nmbrthirteen/podcli) - Podcast clipper that transcribes episodes locally, suggests clips, crops to the active speaker and burns in captions. Use it from a CLI, a web studio or an AI agent over MCP. `AGPL-3.0-only` · local or cloud  
  Licence note: Open core: podcli Pro adds a cloud multicam editor, and a commercial licence is offered. Speaker diarization needs an HF_TOKEN because the pyannote models are gated; accept their terms on Hugging Face first.  
  ⚠ Clip scoring sends transcripts to Anthropic or OpenAI through the Claude Code or Codex CLI if installed. It can pull episodes from URLs with yt-dlp, which sites' terms may not allow.
- [RipeClips](https://github.com/joonfjp/ripeclips) - Mac app that transcribes with Parakeet, picks highlights through its own self-hosted service backed by any OpenAI-compatible LLM, then trims, reframes to vertical and burns in captions. `MIT` · local or cloud  
  Licence note: Parakeet speech model files download at runtime under NVIDIA's model licence, not MIT.
- [Shortcast](https://github.com/mutonby/shortcast) - Mac app for Apple Silicon that transcribes a long video on device, has a local LLM pick moments and write TikTok, Instagram and YouTube post text, and reframes clips to vertical with face tracking. `Apache-2.0` · local or cloud  
  Licence note: The default Gemma 4 model downloads at runtime under Google's Gemma Terms of Use, not Apache-2.0.
- [Stream Clipper Factory](https://github.com/Cbhhhh211/Stream-Clipper-Factory) - Local web app that finds highlights in livestream recordings from chat density, chat emotion and how closely speech matches chat. It has a clip review flow and can learn a ranker from your ratings. `MIT` · local or cloud  
  ⚠ Downloads videos from Bilibili, YouTube and Douyin and records Bilibili live rooms; their terms may not allow it for videos you don't own.
- [SupoClip](https://github.com/FujiwaraChoki/supoclip) - Self-hosted web app that picks clip-worthy segments with an LLM, crops them to vertical around faces and burns word-synced subtitles. It also has a clip editor, a REST API and an MCP server. `AGPL-3.0` · local or cloud  
  Licence note: The README and LICENSE say AGPL-3.0 without "or later"; only the mcp/ folder declares AGPL-3.0-or-later.  
  ⚠ Uses the AssemblyAI cloud service for transcription by default. Downloads YouTube videos through Apify or yt-dlp; YouTube's terms may not allow it for videos you don't own.
- [VideoHighlighter](https://github.com/Aseiel/VideoHighlighter) - Local desktop app that scores moments in long footage from scenes, motion, audio, objects, actions and the transcript, explains each pick in a report and exports highlight reels and clips. `AGPL-3.0-or-later` · runs locally  
  ⚠ Can download videos from YouTube and other sites; their terms may not allow it for videos you don't own.

### Open-source video AI

_Open-source apps for captions, dubbing, reframing and other video AI work._

- [AutoSubs](https://github.com/tmoroney/auto-subs) - On-device subtitle generator that transcribes with Whisper, Parakeet and other local models, labels speakers and sends styled subtitles to DaVinci Resolve, Premiere Pro or After Effects, or exports SRT. `MIT` · runs locally  
  Licence note: Models download separately under their own licences; the optional MMS word-alignment weights are CC BY-NC 4.0 (non-commercial).
- [Buzz](https://github.com/chidiwilliams/buzz) - Offline desktop app that transcribes and translates audio and video with Whisper models and exports TXT, SRT or VTT subtitles. It also has live transcription, speaker labels and a CLI. `MIT` · local or cloud  
  ⚠ Can download YouTube links to transcribe them; YouTube's terms may not allow it for videos you don't own.
- [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) - Makes a short video from a topic or keywords: writes the script, gathers stock or AI footage, adds voiceover, subtitles and music, and exports 9:16, 16:9 or 1:1. `MIT` · local or cloud  
  Licence note: The README says the bundled background music comes from YouTube videos and asks you to delete it if there are copyright issues.  
  ⚠ Sends your topic and script to a cloud LLM (Moonshot by default) and to Microsoft Edge TTS, and fetches stock footage from Pexels.
- [NarratoAI](https://github.com/linyqh/NarratoAI) - Writes a commentary script for a film or short drama, cuts the matching scenes, and adds AI voiceover and subtitles, with export to Jianying drafts. `MIT` · local or cloud  
  ⚠ Sends video frames and subtitles to a cloud LLM (SiliconFlow by default, or another OpenAI-compatible service) to write the narration.
- [OpenCreator](https://github.com/krillinai/OpenCreator) - Creator workspace, formerly KrillinAI, that transcribes, translates and dubs videos and renders bilingual subtitles in landscape or portrait, plus AI writing, image and video tools. `Apache-2.0` · local or cloud  
  ⚠ Desktop app sends usage stats by default (off in Settings); sends prompts and subtitles to your AI service; downloads videos from YouTube and other sites, whose terms may not allow it.
- [pyVideoTrans](https://github.com/jianchang512/pyvideotrans) - Desktop app that translates videos end to end: it transcribes speech, translates subtitles, creates dubbed voices and puts them back into the video. It can run locally with faster-whisper and Ollama. `GPL-3.0` · local or cloud  
  ⚠ By default it sends subtitle text to Google for translation and to Microsoft's online Edge-TTS voice service for dubbing.
- [SoniTranslate](https://github.com/R3gm/SoniTranslate) - Browser app that translates and dubs videos with synced audio, using Whisper transcription, speaker labels, machine or LLM translation and several text-to-speech and voice-imitation engines. `Apache-2.0` · local or cloud  
  Licence note: Speaker labels use gated pyannote models: sign in to Hugging Face and accept their terms first. Optional XTTS voices are under the Coqui Public Model License.  
  ⚠ Sends transcripts to Google Translate and dub text to Microsoft's Edge TTS by default. Downloads YouTube videos, which YouTube's terms may not allow. Copy voices only with consent.
- [Vibe](https://github.com/thewh1teagle/vibe) - Desktop app that transcribes audio and video offline with Whisper, Parakeet or Nemotron models and exports SRT or VTT, with caption lengths suited to reels. `MIT` · local or cloud  
  ⚠ Sends anonymous usage analytics to Aptabase by default (can be turned off in Settings); can fetch audio from YouTube and other sites, whose terms may not allow it.
- [VideoLingo](https://github.com/Huanshere/VideoLingo) - App that transcribes, splits, translates and dubs videos into subtitles and voice-overs, using local speech recognition and an LLM you choose. It has a local HTTP API for scripted runs. `Apache-2.0` · local or cloud  
  ⚠ By default sends subtitle text to the paid OpenLux LLM relay and, for dubbing, to Microsoft's Edge TTS. Downloads YouTube videos; YouTube's terms may not allow it for videos you don't own.
- [Whisper-WebUI](https://github.com/jhj0517/Whisper-WebUI) - Browser app for making SRT, WebVTT or text subtitles with faster-whisper, with voice detection, background-music removal, speaker labels and translation. It has an optional REST API. `Apache-2.0` · local or cloud  
  Licence note: The default NLLB translation model is CC-BY-NC-4.0 (non-commercial). Speaker labels use the gated pyannote model: sign in to Hugging Face and accept its terms first.  
  ⚠ Can download YouTube videos to transcribe them; YouTube's terms may not allow it for videos you don't own.

### Gaming

_Open-source apps for game clips and highlights._

- [AutoShorts](https://github.com/divyaprakash0426/autoshorts) - Python tool that scores long gameplay recordings by audio and motion, optionally with AI analysis, and renders vertical shorts with captions and an optional voiceover. `MIT` · local or cloud  
  Licence note: It credits Binary-Bytes/Auto-YouTube-Shorts-Maker as a base, and that project has no licence file.  
  ⚠ Its example settings upload the whole video to Google Gemini for analysis; set AI_PROVIDER=local to keep everything on your PC.
- [CS Demo Manager](https://github.com/akiver/cs-demo-manager) - Desktop app and CLI for Counter-Strike demos that analyses matches and records videos of chosen rounds or of a player's kills and deaths from the game, ready to clip. `MIT` · runs locally  
  ⚠ Can download match demos from Valve, FACEIT, Renown and 5EPLAY accounts; check those platforms' terms.
- [Half-Life Advanced Effects](https://github.com/advancedfx/advancedfx) - Records Counter-Strike 2 and CS:GO demos as high-quality video with camera control and effects, and exports camera motion to After Effects or Blender. Gives you clean game footage to cut into clips. `MIT` · runs locally  
  Licence note: MIT covers only the project's own code; the README says the licence does not apply to submodules (such as Detours, rapidxml and a fork of Valve's Half-Life SDK), which have their own terms.  
  ⚠ Hooks into the game; its README says it is technically a hack and joining VAC-protected servers with it will probably get you VAC banned. Use it only offline on demos.
- [League Director](https://github.com/RiotGames/leaguedirector) - Riot Games' Windows tool for staging and recording League of Legends replays, with camera control, a keyframe sequencer and video capture. Record the footage here, then clip it. `Apache-2.0` · runs locally  
  Licence note: Riot assets bundled with the installer, such as skybox textures, fall under Riot's legal terms, not Apache-2.0.
- [LeagueRecord](https://github.com/FFFFFFFXXXXXXX/league_record) - Windows tray app that records League of Legends games automatically and saves kills, objectives and your hotkey highlight markers in a JSON file next to each recording, ready for event-based clipping. `GPL-3.0-or-later` · runs locally
- [Segra](https://github.com/Segergren/Segra) - Game recorder that starts with your game, keeps a replay buffer and builds highlights from kills and deaths in supported games. Includes a clip editor with a timeline and optional upload to Segra.tv. `GPL-2.0-only` · local or cloud

### Automation

_Apps that run steps on their own, such as posting a clip once it is ready._

- [Node-RED](https://github.com/node-red/node-red) - Low-code tool for wiring event-driven flows in a browser editor, such as watching a folder for new recordings and calling a local API to start clipping. `Apache-2.0` · runs locally

## Pipelines

### General

_Pipelines for any kind of video._

- [Loud moments, cut on scene changes (example)](https://github.com/ColinGPT9/clips-studio/tree/HEAD/examples/pipelines/scene-cut-highlights) - An example pipeline. It finds stretches that are clearly louder than the rest of the video and starts each clip on the nearest scene cut before it, using FFmpeg only. It knows nothing about what is happening on screen: loud is not always interesting, and quiet highlights are missed. `MIT` · ✓ Official · ✓ Compatible

## Models

### Speech to text

- [Canary 1B v2](https://huggingface.co/nvidia/canary-1b-v2) - NVIDIA's model for transcribing speech in 25 European languages and translating to and from English, with word timestamps. Its card lists only Linux and recent NVIDIA GPUs. `CC-BY-4.0` · runs locally · 99.7k Hugging Face downloads a month
- [distil-large-v3](https://huggingface.co/distil-whisper/distil-large-v3) - A distilled, English-only version of Whisper large-v3 for transcribing speech. Its card reports it 6.3 times faster than large-v3 and within 1% word error rate of it on long recordings. `MIT` · runs locally · 551.2k Hugging Face downloads a month
- [distil-large-v3.5](https://huggingface.co/distil-whisper/distil-large-v3.5) - A distilled, English-only Whisper model for transcribing speech. Its card reports it about 1.5 times faster than large-v3-turbo, slightly more accurate on short audio and about 1% behind on long audio. `MIT` · runs locally · 5.3k Hugging Face downloads a month
- [distil-small.en](https://huggingface.co/distil-whisper/distil-small.en) - The smallest English-only distilled Whisper model, for transcription on machines with very little memory; its own card points most users to larger distil models. `MIT` · runs locally · 9.8k Hugging Face downloads a month
- [faster-whisper-large-v3](https://huggingface.co/Systran/faster-whisper-large-v3) - Whisper large-v3 converted to CTranslate2 format, the build faster-whisper loads when you ask for 'large-v3'. A roughly 3 GB download and slower than the turbo model. `MIT` · runs locally · 1004.4k Hugging Face downloads a month
- [faster-whisper-large-v3-turbo](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) - Whisper large-v3-turbo converted to CTranslate2 for faster-whisper, a good balance of speed and quality for long videos. Formerly mobiuslabsgmbh/faster-whisper-large-v3-turbo, the id faster-whisper's 'turbo' alias still uses. `MIT` · runs locally · 2210.8k Hugging Face downloads a month
- [parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) - NVIDIA's speech recognition model for 25 European languages, with automatic language detection and word-level timestamps. No Chinese, Japanese or Korean; its card targets Linux and NVIDIA GPUs. `CC-BY-4.0` · runs locally · 595.6k Hugging Face downloads a month
- [SenseVoiceSmall](https://huggingface.co/FunAudioLLM/SenseVoiceSmall) - Fast speech recognition model for Mandarin, Cantonese, English, Japanese and Korean that also tags laughter, applause, music and emotion, handy signals for spotting clip moments. `LicenseRef-FunASR-Model-License` · runs locally · 17.5k Hugging Face downloads a month  
  Licence note: The SenseVoice code on GitHub is MIT, but the weights use the FunASR Model License: free use, changes and sharing with attribution; it ends for anyone who maliciously or baselessly attacks the model, and Alibaba may revise it.
- [Whisper distil-large-v3 model for CTranslate2](https://huggingface.co/Systran/faster-distil-whisper-large-v3) - The English-only distil-large-v3 converted for faster-whisper, which loads it by that name. Fast, accurate transcription for English videos; not suited to other languages. `MIT` · runs locally · 53k Hugging Face downloads a month
- [Whisper large-v3](https://huggingface.co/openai/whisper-large-v3) - OpenAI's largest multilingual Whisper model, for transcribing speech in many languages and translating it into English. It is in Transformers format, so faster-whisper needs a CTranslate2 conversion. `Apache-2.0` · runs locally · 3994.8k Hugging Face downloads a month  
  Licence note: Hugging Face lists Apache-2.0, while OpenAI's GitHub releases the Whisper weights under MIT; both are permissive.
- [Whisper medium model for CTranslate2](https://huggingface.co/Systran/faster-whisper-medium) - Whisper medium converted for faster-whisper, which loads it for the size name medium. The next multilingual size up from small for transcription with timestamps. `MIT` · runs locally · 928.7k Hugging Face downloads a month
- [Whisper small model for CTranslate2](https://huggingface.co/Systran/faster-whisper-small) - Whisper small converted for faster-whisper, which loads it for the size name small. Multilingual transcription with timestamps that suits CPU-only machines. `MIT` · runs locally · 3909.1k Hugging Face downloads a month
- [whisper-base](https://huggingface.co/openai/whisper-base) - A small multilingual Whisper model for quick draft transcripts on weak hardware; not accurate enough for final captions. `Apache-2.0` · runs locally · 1723k Hugging Face downloads a month  
  Licence note: Hugging Face lists Apache-2.0, while OpenAI's GitHub releases the Whisper weights under MIT; both are permissive.
- [whisper-large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) - OpenAI's official Whisper large-v3-turbo weights for Transformers: a slimmed-down large-v3 that transcribes much faster for a small loss in quality. faster-whisper and whisper.cpp use converted copies. `MIT` · runs locally · 6348k Hugging Face downloads a month
- [whisper-medium](https://huggingface.co/openai/whisper-medium) - The middle-sized multilingual Whisper model, between small and large, for transcribing and translating speech. `Apache-2.0` · runs locally · 311.9k Hugging Face downloads a month  
  Licence note: Hugging Face lists Apache-2.0, while OpenAI's GitHub releases the Whisper weights under MIT; both are permissive.
- [whisper-small](https://huggingface.co/openai/whisper-small) - A lighter multilingual Whisper model for transcribing speech on machines with little GPU memory or only a CPU, at some cost in accuracy. `Apache-2.0` · runs locally · 3247.1k Hugging Face downloads a month  
  Licence note: Hugging Face lists Apache-2.0, while OpenAI's GitHub releases the Whisper weights under MIT; both are permissive.
- [whisper-tiny](https://huggingface.co/openai/whisper-tiny) - The smallest multilingual Whisper model, handy for testing a transcription pipeline or for very weak hardware; too inaccurate for published captions. `Apache-2.0` · runs locally · 1006.6k Hugging Face downloads a month  
  Licence note: Hugging Face lists Apache-2.0, while OpenAI's GitHub releases the Whisper weights under MIT; both are permissive.
- [whisper.cpp ggml models](https://huggingface.co/ggerganov/whisper.cpp) - OpenAI Whisper models from tiny to large-v3-turbo, including quantized versions, converted to ggml format for whisper.cpp. Lets you transcribe on CPU or GPU without Python; whisper.cpp's download script fetches them here. `MIT` · runs locally · 0 Hugging Face downloads a month

### Sound and audio events

- [Audio Spectrogram Transformer](https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593) - Scores audio against the 527 AudioSet sound classes, including laughter, cheering, applause, crowd and screaming, to help find lively moments in a long video. `BSD-3-Clause` · runs locally · 1077.3k Hugging Face downloads a month
- [CED-Base](https://huggingface.co/mispeech/ced-base) - Xiaomi's audio tagger that scores the 527 AudioSet sound classes, such as laughter, cheering and applause, on variable-length audio, with an ONNX export. `Apache-2.0` · runs locally · 20.3k Hugging Face downloads a month  
  Licence note: The weights and Hugging Face model code are Apache-2.0, but the separate training code at RicherMans/CED is GPL-3.0.
- [clap-htsat-fused](https://huggingface.co/laion/clap-htsat-fused) - Matches audio against any text labels you type, such as crowd cheering or explosion, so you can search a long recording for sounds without training a model. `Apache-2.0` · runs locally · 8191.4k Hugging Face downloads a month  
  Licence note: Its LAION-Audio-630K training audio is marked research-only by the dataset; the model card does not apply that term to the weights.
- [cnn8rnn-audioset-sed](https://huggingface.co/wsntxxn/cnn8rnn-audioset-sed) - A small sound event detector that marks 447 kinds of sounds, such as laughter, applause or gunshots, every 40 ms, so events come with start and end times. `Apache-2.0` · runs locally · 692 Hugging Face downloads a month
- [larger_clap_general](https://huggingface.co/laion/larger_clap_general) - A larger CLAP model trained on general audio, music and speech that matches sounds against text labels you choose, suited to music streams and podcasts. `Apache-2.0` · runs locally · 316k Hugging Face downloads a month  
  Licence note: The card does not name its training data; LAION's related LAION-Audio-630K dataset marks its audio as research-only, a term not stated for these weights.
- [YamNet](https://huggingface.co/qualcomm/YamNet) - A small audio-event classifier that tags short windows with AudioSet classes such as laughter, cheering, applause and explosions, ready-made as ONNX and TFLite files. `MIT` · runs locally · 348 Hugging Face downloads a month  
  Licence note: The MIT terms come from the torch_audioset port; Google's original YAMNet is Apache-2.0, so credit both.
- [Zencastr Laughter Detection](https://huggingface.co/zencastr/laughter-detection) - Finds laughs in podcast audio and returns each one with start, end and score, with presets that favour precision or recall. Comes with ONNX files for a lighter setup. `MIT` · runs locally · 63 Hugging Face downloads a month

### Detection and tracking

- [Grounding DINO tiny](https://huggingface.co/IDEA-Research/grounding-dino-tiny) - Finds objects you name in plain text, such as a ball or a scoreboard, and draws boxes around them without any training. Handy for locating on-screen elements in sampled game or sports frames. `Apache-2.0` · runs locally · 835.2k Hugging Face downloads a month
- [OWLv2](https://huggingface.co/google/owlv2-base-patch16-ensemble) - Google's open-vocabulary detector that boxes whatever you describe in text, such as 'a scoreboard'. Useful for finding the HUD or score area in frames before reading it with an OCR model. `Apache-2.0` · runs locally · 2072.5k Hugging Face downloads a month
- [RT-DETR](https://huggingface.co/PekingU/rtdetr_r50vd) - A real-time object detector that runs directly in Hugging Face Transformers, a permissively licensed way to find people in frames for framing or activity scoring. `Apache-2.0` · runs locally · 166.3k Hugging Face downloads a month
- [Ultralytics YOLO11](https://huggingface.co/Ultralytics/YOLO11) - Detects people and objects, segments them and estimates body poses, useful for tracking speakers or players when reframing a video to vertical. `AGPL-3.0` · runs locally · 23k Hugging Face downloads a month  
  Licence note: Closed-source commercial use needs a paid Ultralytics Enterprise Licence. The Hub tag says agpl-3.0 while the ultralytics package declares AGPLv3 or later.  
  ⚠ The ultralytics package it runs through sends anonymous usage analytics to Google Analytics by default; turn off with yolo settings sync=False.
- [Ultralytics YOLO26](https://huggingface.co/Ultralytics/YOLO26) - Ultralytics' newest YOLO models for detecting people and objects, segmenting them and estimating poses, useful for tracking speakers or players when reframing. `AGPL-3.0` · runs locally · 18.7k Hugging Face downloads a month  
  Licence note: Closed-source commercial use needs a paid Ultralytics Enterprise Licence. The Hub tag says agpl-3.0 while the ultralytics package declares AGPLv3 or later.  
  ⚠ The ultralytics package it runs through sends anonymous usage analytics to Google Analytics by default; turn off with yolo settings sync=False.
- [YuNet](https://huggingface.co/opencv/face_detection_yunet) - A tiny face detector that runs on CPU with OpenCV alone, useful for keeping faces in frame when cropping a video to vertical. `MIT` · runs locally · 0 Hugging Face downloads a month  
  Licence note: Trained on WIDER Face, whose dataset card lists CC BY-NC-ND 4.0; whether that limits commercial use of the weights is legally unsettled.

### Moment and highlight detection

- [R2-Tuning](https://huggingface.co/yeliudev/R2-Tuning) - Finds the parts of a video that match a text query and scores highlights, using a light model built on CLIP. Research code with checkpoints and a single-video inference script. `BSD-3-Clause` · runs locally · 0 Hugging Face downloads a month
- [VideoMind](https://huggingface.co/yeliudev/VideoMind-2B) - A video agent built on Qwen2-VL that answers a question by finding, then double-checking, the moments in a video that match it. Needs a GPU and Linux-oriented dependencies. `BSD-3-Clause` · runs locally · 69 Hugging Face downloads a month  
  Licence note: Its LoRA weights are BSD-3-Clause but need the Qwen2-VL base model, which is Apache-2.0; keep both notices.
- [VideoMind-2B](https://huggingface.co/yeliudev/VideoMind-2B) - Finds the stretch of a video that matches a text query, such as a goal being scored, then checks and re-ranks candidate moments with confidence scores. `BSD-3-Clause` · runs locally · 69 Hugging Face downloads a month  
  Licence note: This repo holds only LoRA adapters; the Qwen2-VL-2B-Instruct base model it needs is downloaded separately and is Apache-2.0.

### Reading text on screen

- [GLM-OCR](https://huggingface.co/zai-org/GLM-OCR) - A small OCR model from Z.ai that reads text, tables and formulas and can fill a JSON schema from an image, such as score and clock fields. It runs locally through Ollama or Transformers; a hosted API is optional. `MIT` · local or cloud · 1844.8k Hugging Face downloads a month  
  Licence note: The model is MIT, but the full SDK pipeline also uses PP-DocLayoutV3 (Apache-2.0) and the card asks users to follow both licences. The SDK code in github.com/zai-org/GLM-OCR is Apache-2.0.
- [PaddleOCR-VL-1.6](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6) - A compact vision-language model built for document parsing: OCR, text spotting, and reading tables, charts and formulas. Its text-spotting mode can pick up on-screen text, but it is heavier than plain OCR models. `Apache-2.0` · runs locally · 47.1k Hugging Face downloads a month
- [PP-OCRv5_mobile_rec](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_rec) - A lightweight PaddleOCR model that reads the text inside cropped text lines in English, Chinese or Japanese. Use it after a text detector to turn score or clock crops into text; it needs the PaddlePaddle runtime. `Apache-2.0` · runs locally · 30.6k Hugging Face downloads a month
- [PP-OCRv5_server_det](https://huggingface.co/PaddlePaddle/PP-OCRv5_server_det) - PaddleOCR's text-detection model that outlines lines of text in an image, including rotated or curved text. Pair it with a recognition model to read scoreboards and timers; it needs the PaddlePaddle runtime. `Apache-2.0` · runs locally · 511.6k Hugging Face downloads a month

### Describing images and video

- [Florence-2-base](https://huggingface.co/microsoft/Florence-2-base) - A small Microsoft vision model that captions frames, reads on-screen text with its position and detects objects, picking the task from a short prompt. Useful for reading scores or labelling moments. `MIT` · runs locally · 3079.6k Hugging Face downloads a month
- [Florence-2-large](https://huggingface.co/microsoft/Florence-2-large) - The larger Florence-2 model, which captions images, detects objects and reads on-screen text with boxes, chosen by task prompt. Its card warns this checkpoint may not be fully trained. `MIT` · runs locally · 389k Hugging Face downloads a month
- [MiniCPM-V 4.5](https://huggingface.co/openbmb/MiniCPM-V-4_5) - An 8B vision-language model that compresses video frames so it can follow long or high-frame-rate video, as well as images and OCR. It can judge whole candidate clips but needs a strong GPU or quantised weights. `Apache-2.0` · runs locally · 92.9k Hugging Face downloads a month
- [Moondream 2](https://huggingface.co/vikhyatk/moondream2) - A compact vision-language model that captions images, answers questions about them, and can detect or point at things such as faces and people. Its detection and pointing could guide vertical reframing. `Apache-2.0` · runs locally · 2180k Hugging Face downloads a month
- [Qwen3-VL-2B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct) - A small Qwen vision-language model that describes images and video and, per its card, can place events in time within long videos. It is also in Ollama's library, which Clips Kitty already uses. `Apache-2.0` · runs locally · 2717.3k Hugging Face downloads a month
- [Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct) - The step-up Qwen3-VL model for richer descriptions of images and video than the 2B version. Available through Ollama, though it will be slow without a GPU. `Apache-2.0` · runs locally · 3264.8k Hugging Face downloads a month
- [SigLIP 2 Base](https://huggingface.co/google/siglip2-base-patch16-224) - Google's image-text embedding model for matching frames to text prompts and comparing frames with each other. Useful for cheap tagging, spotting near-duplicate moments and ranking thumbnail candidates. `Apache-2.0` · runs locally · 1617.7k Hugging Face downloads a month
- [SmolVLM-256M](https://huggingface.co/HuggingFaceTB/SmolVLM-256M-Instruct) - A tiny image-and-text model that captions frames and answers questions about them, small enough for CPU-only PCs and shipped with ONNX weights. Its captions are rough, so treat them as hints. `Apache-2.0` · runs locally · 397.6k Hugging Face downloads a month
- [SmolVLM2 2.2B](https://huggingface.co/HuggingFaceTB/SmolVLM2-2.2B-Instruct) - The larger SmolVLM2 model for describing and answering questions about video and images in English, including reading text in frames. It needs about 5 GB of GPU memory for video. `Apache-2.0` · runs locally · 142.7k Hugging Face downloads a month
- [SmolVLM2-500M-Video](https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct) - A small Hugging Face model that describes and answers questions about short videos and images in English, using under 2 GB of GPU memory. Useful for rating how visually interesting a candidate clip is. `Apache-2.0` · runs locally · 1288.8k Hugging Face downloads a month
- [X-CLIP](https://huggingface.co/microsoft/xclip-base-patch16-zero-shot) - Scores short video windows against text labels you choose for zero-shot tagging of segments. Trained on human-action clips, so results on game footage are unconfirmed. `MIT` · runs locally · 185.5k Hugging Face downloads a month

### Speakers and voice activity

- [speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) - Works out who is speaking when in 16 kHz mono audio, counting and labelling each speaker; pyannote's current open diarization pipeline. Useful for podcasts and interviews where clips follow one voice. `CC-BY-4.0` · runs locally · 5554.9k Hugging Face downloads a month  
  Licence note: Access is gated on Hugging Face: sign in, give your company or university and use case, and agree that pyannote may email you before downloading. Once downloaded it runs offline. Attribution is required.  
  ⚠ Runs through pyannote.audio, which has telemetry switched on by default; set PYANNOTE_METRICS_ENABLED=0 to turn it off.

### Language models

- [EmbeddingGemma](https://huggingface.co/google/embeddinggemma-300m) - Google's small text-embedding model for semantic search in over 100 languages. A pipeline can embed transcript chunks and query phrases to find matching moments or group similar ones. `LicenseRef-Gemma-Terms-of-Use` · runs locally · 3625.2k Hugging Face downloads a month  
  Licence note: Gated: sign in to Hugging Face and accept Google's Gemma Terms of Use first. The terms add a Prohibited Use Policy, and anyone redistributing the weights or a fine-tune must pass those limits on with a Notice file.
- [Gemma 3 4B](https://huggingface.co/google/gemma-3-4b-it) - The previous-generation Gemma model with 4B parameters, reading text and images. Clips Kitty recommends it through Ollama for PCs without a graphics card, though it cannot drive the Ask box. `LicenseRef-Gemma-Terms-of-Use` · runs locally · 1240.1k Hugging Face downloads a month  
  Licence note: Gated: sign in to Hugging Face and accept Google's Gemma Terms of Use first. The terms add a Prohibited Use Policy, and anyone redistributing the weights must pass those limits on with a Notice file.
- [Gemma 4 12B QAT Q4_0 GGUF](https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf) - Google's own 4-bit GGUF of Gemma 4 12B Unified, trained to hold its quality at a much smaller memory size. Suits llama.cpp-based tools on mid-range GPUs. `Apache-2.0` · runs locally · 783.5k Hugging Face downloads a month
- [Gemma 4 12B Unified](https://huggingface.co/google/gemma-4-12B-it) - A mid-size Gemma 4 model with a long context window and text, image and short-audio input. A step up for ranking moments on mid-range GPUs, and available in Ollama. `Apache-2.0` · runs locally · 1737.5k Hugging Face downloads a month
- [Gemma 4 26B A4B](https://huggingface.co/google/gemma-4-26B-A4B-it) - A mixture-of-experts Gemma 4 model that runs about as fast as a 4B model but needs memory for all its weights. Its long context could let a pipeline score a whole stream transcript in one pass. `Apache-2.0` · runs locally · 12473.2k Hugging Face downloads a month
- [Gemma 4 E2B](https://huggingface.co/google/gemma-4-E2B-it) - The smallest Gemma 4 model, reading text and images and writing text, aimed at laptops and low-end PCs. Clips Kitty suggests it through Ollama for machines with under 6 GB of VRAM. `Apache-2.0` · runs locally · 3006k Hugging Face downloads a month
- [Gemma 4 E4B](https://huggingface.co/google/gemma-4-E4B-it) - Google DeepMind's small Gemma 4 model that reads text and images and writes text, sized for ordinary PCs. Clips Kitty recommends it through Ollama for 6 GB of VRAM and for its Ask box. `Apache-2.0` · runs locally · 4419.7k Hugging Face downloads a month
- [Gemma 4 E4B QAT Q4_0 GGUF](https://huggingface.co/google/gemma-4-E4B-it-qat-q4_0-gguf) - Google's own 4-bit GGUF of Gemma 4 E4B, trained to keep quality close to full precision while using far less memory. For llama.cpp-style runtimes; the projector file is only needed for image or audio input. `Apache-2.0` · runs locally · 734.3k Hugging Face downloads a month
- [Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B) - Alibaba Qwen's small text LLM with a switchable thinking mode, runnable through Ollama, LM Studio or llama.cpp. A non-Gemma option for scoring transcript windows; turn thinking off for speed. `Apache-2.0` · runs locally · 8896.9k Hugging Face downloads a month

## Workflows

### ComfyUI

- [ComfyUI-FFmpeg](https://github.com/MoonHugo/ComfyUI-FFmpeg) - ComfyUI nodes that run common FFmpeg jobs on video files, such as cutting, merging, watermarks, picture-in-picture and transitions. Cuts snap to keyframes. `Apache-2.0` · runs locally
- [ComfyUI-VideoHelperSuite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite) - ComfyUI nodes that load videos as frames and combine frames and audio back into video files. The usual base for ComfyUI workflows that reframe or caption clips. `GPL-3.0` · runs locally  
  ⚠ Load Video can download web URLs through yt-dlp when it is installed; YouTube's terms may not allow downloading videos you don't own.

## Integrations

### Streaming

- [Advanced Scene Switcher](https://github.com/WarmUpTill/SceneSwitcher) - OBS plugin that runs condition-and-action macros, for example saving the replay buffer when a video, speech, file or Twitch condition is met, to capture clip moments automatically. `GPL-2.0-only` · runs locally
- [Clips Kitty OBS Plugin](https://github.com/ColinGPT9/clips-kitty-obs-plugin) - An OBS Studio dock that hands your stream to Clips Kitty once it really ends and shows the progress. Nothing runs while you are live. Pre-release. Needs Clips Kitty 1.2.0 or newer. `GPL-2.0-or-later` · ✓ Official · runs locally
- [Firebot](https://github.com/crowbartools/Firebot) - Desktop chat bot for Twitch streamers with commands, timers and event-driven effects, including OBS control and creating Twitch clips. An effect can call a local program or URL when a replay is saved. `GPL-3.0` · runs locally  
  Licence note: The README asks that anything using this code stays under the GPL and credits this repository.
- [Local Stream Marker](https://github.com/honganqi/OBS-Local-Stream-Marker) - OBS script that marks moments in any recording or stream with a hotkey and saves their timestamps, file names and comments to a CSV file, so you can find clip moments later. `MIT` · runs locally
- [LocalVocal](https://github.com/royshil/obs-localvocal) - OBS plugin that transcribes speech on your PC with Whisper while you stream or record. It shows live captions and can save SRT files timed to the recording, with optional translation. `GPL-2.0-or-later` · local or cloud
- [node-red-contrib-obs-ws](https://github.com/lebaston100/node-red-contrib-obs-ws) - Node-RED nodes that connect to OBS Studio through obs-websocket to receive events and send requests, for example to hand a saved replay to a clip maker. `MIT` · runs locally
- [OBS Hadowplay](https://github.com/EZ64cool/obs-hadowplay) - OBS plugin for Windows that turns the replay buffer on when a game is captured and files saved replays, recordings and screenshots into folders named after each game. `GPL-2.0-or-later` · runs locally
- [obs-websocket](https://github.com/obsproject/obs-websocket) - Remote-control API built into OBS Studio. Other programs can use it to start recordings, save the replay buffer, add chapter markers and react when a replay is saved. `GPL-2.0-or-later` · runs locally
- [Replay Source for OBS Studio](https://github.com/exeldro/obs-replay-source) - OBS plugin that keeps the last seconds of a source in memory for instant replays, with slow motion, reverse playback and a hotkey that saves the replay to disk. `GPL-2.0-only` · runs locally
- [Twitchat](https://github.com/Durss/Twitchat) - Browser-based Twitch chat for streamers with moderation, alerts, a clip command and triggers that can control OBS or make HTTP calls when chat or stream events happen. `GPL-3.0` · local or cloud  
  Licence note: The hosted twitchat.fr app limits how many triggers non-premium users can create.  
  ⚠ The main version runs on twitchat.fr and signs in with your Twitch account; self-hosting needs your own Twitch app.

### AI assistants

- [Clips Kitty MCP server and agent skill](https://github.com/ColinGPT9/clips-studio#ask-an-ai-agent-to-do-it) - Lets Claude, Cursor or any MCP client make clips from a link or a file, follow the job and read back the clips, through Clips Kitty's local API. Its tools can also post clips through the YouTube, Upload-Post or WoopSocial accounts connected in Clips Kitty. Ships with Clips Kitty. `AGPL-3.0-or-later` · ✓ Official · runs locally
- [DaVinci Resolve MCP Server](https://github.com/samuelgursky/davinci-resolve-mcp) - MCP server that lets AI agents work in DaVinci Resolve through its scripting API, for example to import a video and add clip moments as timeline markers. `MIT` · runs locally  
  Licence note: Needs DaVinci Resolve, which is proprietary; external scripting needs the paid Studio edition, and the free edition works only through a bridge up to 21.0.x.
- [Kinocut](https://github.com/KyaniteLabs/kinocut) - MCP server, Python library and CLI that give AI agents checked FFmpeg editing tools for trimming, captions, 9:16 resizing, scene detection and silence removal. `Apache-2.0` · runs locally  
  Licence note: The optional object-matte extra uses BiRefNet model weights whose licence was not confirmed.
- [OBS MCP Server](https://github.com/royshil/obs-mcp) - MCP server that lets AI agents control a running OBS Studio, including scenes, recording, streaming and saving the replay buffer. `GPL-2.0-only` · runs locally
- [yt-dlp-mcp](https://github.com/kevinwatt/yt-dlp-mcp) - MCP server built on yt-dlp that lets AI agents search videos, read metadata, comments and subtitles, and download video or audio, optionally trimmed. `MIT` · runs locally  
  ⚠ Downloads videos, subtitles and comments from YouTube and other sites; their terms may not allow it for videos you don't own. It can use your browser cookies.

### Publishing

- [Postiz](https://github.com/gitroomhq/postiz-app) - Self-hostable social media scheduler that posts videos to YouTube, TikTok, Instagram, X and other networks, with a public API for sending finished clips from other tools. `AGPL-3.0` · local or cloud  
  ⚠ Uploads your clips to the social accounts you connect, either from your own server or through the hosted Postiz Cloud.
- [Youtube Uploader](https://github.com/porjo/youtubeuploader) - Uploads a video to YouTube from the command line through the official YouTube Data API, setting title, description, tags, privacy, playlists, thumbnail, captions and a scheduled publish time. `Apache-2.0` · local or cloud  
  ⚠ Uploads your videos and their details to YouTube with your Google account.

### Chat and community

- [Chatterino 2](https://github.com/Chatterino/chatterino2) - Desktop chat client for Twitch that can save channel chat to timestamped log files, a simple way to keep a stream's chat for finding busy moments later. `MIT` · local or cloud
- [TwitchIO](https://github.com/TwitchIO/TwitchIO) - Async Python library for the Twitch API and EventSub with chat-bot commands, for reading chat and stream events as live signals for clipping. `MIT` · local or cloud
- [Twurple](https://github.com/twurple/twurple) - TypeScript libraries for the Twitch Helix API, chat and EventSub, so a clip tool can react to chat, channel point redemptions, subscriptions and follows as they happen. `MIT` · local or cloud

### Games

_Live data from a game, such as kills and rounds, as they happen._

- [Counter-Strike 2 GSI](https://github.com/antonpup/CounterStrike2GSI) - C# library that receives Counter-Strike 2 Game State Integration data and raises events for kills, rounds and bomb plants. Logging them while you record gives exact timestamps for highlights. `GPL-3.0` · runs locally

## Tools

### For developers

- [Clips Kitty SDK for Python](https://github.com/ColinGPT9/clips-studio/tree/HEAD/sdk/python) - Write, check and test Clips Kitty plugins - the manifest validator, a local test host, and a client for the local API. MIT-licensed, so using it puts no licence on your plugin. `MIT` · ✓ Official · runs locally

### Finding moments

_Libraries and command-line tools that pick the best moments in a video._

- [auto-slice-video](https://github.com/timerring/auto-slice-video) - Python CLI and library that finds the busiest stretches of chat or danmaku in an ASS subtitle file and cuts those windows from the video as highlight clips. `MIT` · runs locally
- [Chopify](https://github.com/mehbul/chopify) - Turns a video link into captioned clips: it downloads the video, transcribes it locally, scores moments with a built-in heuristic or a local Ollama model and renders vertical, square or wide clips that end on a full sentence. `MIT` · runs locally  
  ⚠ Downloads videos from YouTube and other sites with yt-dlp; their terms may not allow it for videos you don't own.
- [jevcut](https://github.com/VBS2004/jevcut) - Cuts long talk videos into ranked clips by listing every possible cut point from sentence ends, pauses and speaker changes, then having a hosted model judge each clip. Outputs MP4s, an edit list and an FCPXML timeline. `MIT` · local or cloud  
  Licence note: Judging clips needs an API key for TypeSafe's hosted Jev model (through OpenRouter or the TypeSafe SDK), a paid service under its own terms.  
  ⚠ Sends transcript text to TypeSafe's hosted Jev model, through OpenRouter by default, to judge every clip.
- [Lighthouse](https://github.com/line/lighthouse) - Finds the moments in a video that match a text query, such as a goal or people laughing, and scores each part for how highlight-worthy it is. Videos longer than 150 seconds must be split first. `Apache-2.0` · runs locally  
  Licence note: Pre-trained weights download separately from Google Drive and Zenodo with no stated licence, and were trained on datasets with non-commercial annotation terms (QVHighlights is CC BY-NC-SA 4.0).
- [Twitch AI Clip Miner](https://github.com/jamesbaughnd/twitch-clip-miner) - Finds highlights in Twitch VODs or local videos by combining loudness peaks, hype words and laughter in the transcript, chat speed and facial emotion. Exports the best clips, optionally as vertical video. `MIT` · runs locally  
  ⚠ Downloads Twitch VODs and chat with yt-dlp and TwitchDownloaderCLI; Twitch's terms may not allow it for VODs you don't own.

### Transcription

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) - Python library that transcribes speech with Whisper models faster and with less memory than the original, with word timestamps, voice activity filtering and batched processing. Needs no separate FFmpeg install. `MIT` · runs locally
- [Whisper](https://github.com/openai/whisper) - OpenAI's reference speech recognition tool and Python package: it transcribes speech in many languages, translates it into English and identifies the language. The original behind most Whisper ports. `MIT` · runs locally  
  Licence note: The README says both code and model weights are MIT. Hugging Face copies can differ: openai/whisper-large-v3 is tagged Apache-2.0, so check the licence of the exact repository you pin.
- [whisper-ctranslate2](https://github.com/Softcatala/whisper-ctranslate2) - Command-line transcriber with the same options as OpenAI's whisper tool but running on faster-whisper, adding batched processing, voice activity filtering, speaker labels and live microphone input. `MIT` · runs locally  
  Licence note: Speaker diarization needs pyannote.audio and the gated pyannote/speaker-diarization-community-1 model (CC-BY-4.0): accept its terms on Hugging Face and supply a token.
- [whisper-timestamped](https://github.com/linto-ai/whisper-timestamped) - Extends OpenAI Whisper with word timestamps, a confidence score for every word and segment, voice activity detection and marking of likely disfluencies. Useful for captions and filler-word cleanup. `AGPL-3.0` · runs locally  
  Licence note: The LICENSE file is AGPL-3.0, but the package metadata says GPLv3.
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp) - Native C/C++ build of Whisper with command-line and server programs that transcribe without Python on CPU or GPU, including AMD and Intel cards through Vulkan. Outputs JSON and SRT, with optional voice activity detection. `MIT` · runs locally
- [WhisperX](https://github.com/m-bain/whisperX) - Transcribes speech with faster-whisper, then aligns every word for precise timestamps and can label who is speaking. Handy for word-by-word captions and speaker-aware reframing. `BSD-2-Clause` · runs locally  
  Licence note: Speaker labels use the gated pyannote/speaker-diarization-community-1 model (CC-BY-4.0): sign in to Hugging Face, accept its terms and supply a token. Alignment models for many languages have their own licences.

### Captions and subtitles

- [FFsubsync](https://github.com/smacke/ffsubsync) - Automatically fixes out-of-sync subtitles by shifting and stretching them to match the speech in a video or a correctly timed reference subtitle file. `MIT` · runs locally
- [pycaps](https://github.com/francozanardi/pycaps) - Burns animated word-by-word captions onto videos using CSS-styled templates. It can transcribe with Whisper or reuse an existing Whisper JSON, SRT or VTT transcript. `MIT` · local or cloud

### Translation and dubbing

- [LLM-Subtrans](https://github.com/machinewrapped/llm-subtrans) - Translates SRT, ASS and VTT subtitle files with large language models through a desktop app or command line, and can also transcribe audio. Works with cloud AI providers or a local OpenAI-compatible server such as Ollama. `MIT` · local or cloud  
  ⚠ Sends subtitles, and audio when transcribing, to OpenRouter by default or another cloud AI provider you pick, unless you use a local server or Qwen Local.
- [Open Dubbing](https://github.com/Softcatala/open-dubbing) - Dubs a video into another language from the command line: it separates the voice, identifies speakers, transcribes, translates and generates new speech. It saves an editable file so you can fix lines and re-render. `Apache-2.0` · local or cloud  
  Licence note: Its default NLLB translation and MMS speech models are CC-BY-NC-4.0 (non-commercial only), and its pyannote models are gated: accept their terms on Hugging Face and supply a token.

### Audio and video processing

- [Audio Separator](https://github.com/nomadkaraoke/python-audio-separator) - Splits audio into stems such as vocals and instrumental with models trained for Ultimate Vocal Remover. Useful for removing game sound or background music before transcription. `MIT` · local or cloud  
  Licence note: Model weights download on first use and have no stated licences. Some models are marked VIP, meant only for paying UVR subscribers, so leave them out. The authors ask you to credit UVR when using its models.
- [Demucs](https://github.com/adefossez/demucs) - Splits a soundtrack into vocals, drums, bass and other stems, so you can isolate speech from background music before transcribing or captioning a clip. `MIT` · runs locally  
  Licence note: The pretrained weights on Hugging Face (adefossez/HTDemucs) state no licence of their own; only the repository's MIT licence is confirmed.
- [FFmpeg](https://github.com/FFmpeg/FFmpeg) - The standard command-line toolkit and libraries for decoding, cutting, filtering, encoding and muxing audio and video; most clip makers use it to cut and render clips. `LGPL-2.1-or-later` · runs locally  
  Licence note: Builds made with --enable-gpl or with GPL libraries such as libx264 are GPL-2.0-or-later, and --enable-nonfree builds cannot be redistributed. Check the licence of the FFmpeg build you download.
- [MoviePy](https://github.com/Zulko/moviepy) - Python library for editing video in code: cutting, joining, compositing, adding text and titles and applying effects, reading and writing files through FFmpeg. Slower than calling FFmpeg directly. `MIT` · runs locally
- [pyannote.audio](https://github.com/pyannote/pyannote-audio) - Python toolkit that works out who spoke when in a recording, with speech activity, speaker change and overlap detection. Useful for cutting podcast clips at speaker turns and following the active speaker. `MIT` · local or cloud  
  Licence note: Pretrained pipelines on Hugging Face have their own licences and are often gated; the default community-1 pipeline is CC-BY-4.0 and needs sign-in, accepted terms and a token.  
  ⚠ Sends anonymous usage metrics (pipeline origin, file duration, speaker-count settings) by default; set PYANNOTE_METRICS_ENABLED=0 to turn it off.
- [Silero VAD](https://github.com/snakers4/silero-vad) - A small voice activity detector that finds where speech starts and stops in audio, on CPU or GPU. Useful for trimming silence, cleaning up Whisper input and spotting talk-heavy stretches. `MIT` · runs locally  
  Licence note: The model weights ship in the repository under the same MIT licence. The README badge's 'CC BY-NC 4.0' alt text is out of date; the LICENSE file says MIT.

### Scene detection

- [PySceneDetect](https://github.com/Breakthrough/PySceneDetect) - Finds scene cuts and transitions in a video and lists them as timecodes or CSV, or splits the video at each cut. Useful for snapping clip starts and ends to shot changes. `BSD-3-Clause` · runs locally
- [TransNet V2](https://github.com/soCzech/TransNetV2) - A neural network that detects shot changes in any video and lists where each scene starts and ends. Useful for snapping clip edges to real cuts; TensorFlow, with a PyTorch port. `MIT` · runs locally

### Detection and tracking

- [Light-ASD](https://github.com/Junhua-Liao/Light-ASD) - Detects which visible face is speaking, using both the audio and the picture, with a demo for your own videos. Useful for keeping vertical crops on the speaker; the authors' newer LR-ASD extends it. `MIT` · runs locally  
  Licence note: The demo's S3FD face detector code has no licence and its weights come from Google Drive with no stated licence; swap in a licensed face detector before reusing the demo pipeline.  
  ⚠ Its Columbia evaluation script downloads a test video from YouTube with youtube-dl; YouTube's terms may not allow it. The demo on your own videos uses local files.
- [MediaPipe](https://github.com/google-ai-edge/mediapipe) - Google's on-device machine learning framework with ready-made tasks for face, pose and object detection on desktop, mobile and web. Useful for finding faces and subjects when reframing clips to vertical. `Apache-2.0` · runs locally  
  Licence note: Its AutoFlip reframing solution is legacy and unsupported since March 2023.
- [MMAction2](https://github.com/open-mmlab/mmaction2) - PyTorch toolbox for action recognition, temporal action localization and spatio-temporal action detection, for training models that spot plays or events in sports and game footage. `Apache-2.0` · runs locally
- [RF-DETR](https://github.com/roboflow/rf-detr) - Detects and segments people and objects in video frames, a permissively licensed option for following players or a ball when reframing to vertical. `Apache-2.0` · local or cloud  
  Licence note: The Atto, Femto, Pico, XLarge and 2XLarge detection models need the rfdetr[plus] extension and are under the separate PML 1.0 licence, not Apache-2.0.
- [supervision](https://github.com/roboflow/supervision) - Python toolkit that puts any detector's results into one format and adds helpers for drawing boxes, smoothing detections and reading or writing video frames. Object tracking now lives in Roboflow's separate trackers package. `MIT` · runs locally
- [TrackLab](https://github.com/TrackingLaboratory/tracklab) - A modular framework that chains detectors, pose estimators, re-identification models and trackers to follow people or objects through a video. Its track data can drive player-following vertical crops. `MIT` · runs locally  
  Licence note: Installing it pulls in Ultralytics as a required dependency, which is AGPL-3.0 (or a paid Ultralytics licence); vendored code in plugins/ keeps its own licences.  
  ⚠ Given a web address, it downloads the video with yt-dlp, and its sample config suggests a YouTube link; YouTube's terms may not allow it. Use local files.
- [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) - Python library and command line for YOLO object detection, segmentation and pose estimation that can also track people and objects across video frames. A common base for keeping a subject in frame when reframing. `AGPL-3.0-or-later` · runs locally  
  Licence note: Any plugin that imports it must be AGPL-compatible open source unless you buy an Ultralytics Enterprise licence. Trained models are AGPL-3.0 by default.  
  ⚠ Sends anonymized analytics and crash reports by default (yolo settings sync=False turns them off). Accepts YouTube links as input, which YouTube's terms may not allow.

### Automatic editing

- [Auto-Editor](https://github.com/WyattBlue/auto-editor) - Removes dead air and silent parts from video or audio based on loudness, motion or subtitle matches. Renders the edit or exports a timeline for Premiere, DaVinci Resolve, Final Cut Pro, Shotcut or Kdenlive. `Unlicense` · runs locally  
  Licence note: Without a paid key from app.auto-editor.com, renders above 3200x1800 are downscaled and multi-source timeline export and the 'add' action are locked. The README says release binaries may be under other open-source licences.
- [AutoCut](https://github.com/mli/autocut) - Edits a video by letting you pick lines from its Whisper transcript and cutting the matching segments. `Apache-2.0` · local or cloud

### Game replays and events

_Read what happened in a game, with exact times, from its replay or demo files._

- [Awpy](https://github.com/pnxenopoulos/awpy) - Python library and command line for Counter-Strike 2 demos that returns rounds, kills, damage, grenades and stats as tables. Handy for scoring rounds to find multi-kills and clutches. `MIT` · runs locally
- [Boxcars](https://github.com/nickbabcock/boxcars) - Rust library that parses Rocket League .replay files: the header gives goals and scores, and the network data gives frame-by-frame details. The companion rrrocket tool outputs JSON. `MIT` · runs locally
- [Clarity](https://github.com/skadistats/clarity) - Java parser for Dota 2, CS:GO, CS2 and Deadlock replays that exposes the combat log, game events, entities and an end-of-game summary. One parser can cover kills and fights across several games. `BSD-3-Clause` · runs locally
- [danser-go](https://github.com/Wieku/danser-go) - Plays back osu! beatmaps and replays from osu! stable and lazer and records them to video files through FFmpeg, from a GUI or the command line. `GPL-3.0` · runs locally  
  Licence note: It bundles the proprietary BASS audio library, which is free only for non-commercial use: selling danser-go or a derivative needs a paid BASS licence. Some assets listed in CREDITS.md, such as the logo, have their own terms.
- [demoinfocs-golang](https://github.com/markus-wa/demoinfocs-golang) - Go library that parses Counter-Strike 2 and CS:GO demos and calls your code on game events such as kills and round ends. It can also read live CSTV+ broadcasts. `MIT` · runs locally
- [Demoparser](https://github.com/LaihoE/demoparser) - Fast Counter-Strike 2 demo parser written in Rust and used from Python or JavaScript. Query a demo for events such as kills with their tick numbers to find highlight moments. `MIT` · runs locally
- [FortniteReplayDecompressor](https://github.com/Shiqan/FortniteReplayDecompressor) - Reads Fortnite replay files and lists eliminations with start and end times plus match stats, which a small helper can turn into kill clips. A .NET library published on NuGet as FortniteReplayReader. `MIT AND GPL-3.0-or-later` · runs locally  
  Licence note: The repository LICENSE is MIT, but the bundled OozSharp decompressor (src/OozSharp/Kraken.cs, a port of ooz) is GPL-3.0-or-later and is part of the NuGet library, so redistributing it carries GPL-3.0-or-later duties too.
- [gem](https://github.com/whanyu1212/gem-dota) - Pure Python Dota 2 replay parser that turns .dem files into match objects, tables, JSON or Parquet, covering the combat log, fights, Roshan, towers, wards and runes. Highlight scoring is left to you. `MIT` · runs locally
- [ReplayMod](https://github.com/ReplayMod/ReplayMod) - Minecraft mod that records your sessions so you can replay them from any camera angle, mark moments with a hotkey and render camera paths to video for clipping. `GPL-3.0-or-later` · runs locally
- [subtr-actor](https://github.com/rlrml/subtr-actor) - Reads Rocket League replay files and gives you goals, demolitions, touches, boost pickups and stats timelines with exact times, ready to turn into highlight timestamps. Rust library with Python and JavaScript bindings. `MIT` · runs locally

### Sports analysis

- [FastF1](https://github.com/theOehrly/Fast-F1) - Python package that loads Formula 1 timing data, telemetry, results and schedules as pandas DataFrames, useful for lining up race footage with laps and key moments. `MIT` · local or cloud
- [nba_api](https://github.com/swar/nba_api) - Python client for NBA.com's stats and live data endpoints, including play-by-play and scoreboards, for lining up basketball highlights with game events. `MIT` · local or cloud
- [Roboflow sports](https://github.com/roboflow/sports) - Computer-vision helpers for sports, with a soccer example that detects players, referees, the ball and pitch keypoints, splits players into teams and draws a top-down radar view. Handy for ball-following crops. `MIT` · runs locally  
  Licence note: The soccer example's YOLOv8 detector is Ultralytics AGPL-3.0 (or a paid Ultralytics licence), its weights come from Google Drive with no stated licence, and the Kaggle training-data terms are unconfirmed.
- [SoccerNet Game State Reconstruction](https://github.com/SoccerNet/sn-gamestate) - Tracks everyone on the pitch in a soccer broadcast and estimates each person's pitch position, role, team and jersey number, drawn on a minimap. Useful for player-focused clips; needs a CUDA GPU. `GPL-3.0` · runs locally  
  Licence note: Its baseline detector is Ultralytics YOLOv11 (AGPL-3.0 or a paid Ultralytics licence), and the licences of the baseline weights it downloads automatically were not confirmed.  
  ⚠ Includes a config that downloads a YouTube video with yt-dlp; YouTube's terms may not allow it. Point it at your own local video files instead.
- [SoccerNet-Echoes](https://github.com/SoccerNet/sn-echoes) - Whisper transcripts of soccer broadcast commentary for SoccerNet matches, with English translations and timed segments. Useful for testing transcript-based moment finding; it contains no video. `CC-BY-4.0` · runs locally  
  Licence note: The GitHub repository has no licence file; CC BY 4.0 comes from the official Hugging Face copy (SoccerNet/SN-echoes), so use that copy and give attribution. The match videos are not included and need the SoccerNet NDA.
- [T-DEED](https://github.com/arturxe2/T-DEED) - Spots the exact frames where events happen in sports video (soccer, tennis, diving, gymnastics, figure skating) and writes them with timestamps to JSON. Research code with checkpoints; needs an NVIDIA GPU. `GPL-3.0` · runs locally  
  Licence note: Checkpoints are on Google Drive with no stated licence and were trained on data with its own terms (FineGym is non-commercial; SoccerNet video is behind an NDA). Code adapted from E2E-Spot keeps a BSD-3-Clause notice.
- [TrackNetV3](https://github.com/qaz812345/TrackNetV3) - Tracks the shuttlecock in badminton video and writes its position for every frame to a CSV, with an optional trajectory overlay video. Stretches where the shuttle is visible can be grouped into rallies for clips. `MIT` · runs locally

### Stream recording and downloads

- [biliup](https://github.com/biliup/biliup) - Records live streams and their danmaku chat from Bilibili, Douyin, Twitch, YouTube and other sites around the clock, with a web UI to cut segments and upload them to Bilibili. `MIT` · runs locally  
  Licence note: The LICENSE file is MIT, but the README's disclaimer says the project is for personal learning and research only and that commercial use is prohibited.  
  ⚠ Records streams from Twitch, YouTube, Douyin and other sites, whose terms may not allow it for streams you don't own; uploads use your Bilibili login.
- [Ganymede](https://github.com/Zibbp/ganymede) - Self-hosted server that archives Twitch VODs and live streams together with their chat, including a rendered chat video, in a plain file layout that works without it. `GPL-3.0` · runs locally  
  ⚠ Downloads Twitch VODs, live streams and chat, which Twitch's terms may not allow for channels you don't own; an optional proxy setting is built to archive live streams without ads.
- [Streamlink](https://github.com/streamlink/streamlink) - Captures live streams and VODs from Twitch, Kick, YouTube and many other services and saves them to a file you can clip, or plays them in your video player. Command-line tool and Python library. `BSD-2-Clause` · runs locally  
  ⚠ Records streams and VODs from Twitch, Kick, YouTube and other sites; their terms may not allow it, and its Kick plugin solves a browser challenge that Kick's terms forbid bypassing.
- [twitch-dl](https://github.com/ihabunek/twitch-dl) - Command-line tool that lists a Twitch channel's videos and clips and downloads them over several connections at once. It can also export chat as JSON, as a chat video or as subtitles. `GPL-3.0` · runs locally  
  ⚠ Downloads VODs and clips from Twitch with a built-in client ID; Twitch's terms do not allow automated downloading. Use it only for videos you have the right to download.
- [TwitchDownloader](https://github.com/lay295/TwitchDownloader) - Downloads Twitch VODs, clips and chat (as JSON, HTML or text) and can render the chat into a video. A Windows app plus a cross-platform command-line version. `MIT` · runs locally  
  Licence note: Release binaries bundle third-party parts, including FFmpeg (GPLv2) and Xabe.FFmpeg.Downloader, whose terms include CC BY-NC-SA 3.0 (non-commercial), so redistributing them carries more than MIT duties.  
  ⚠ Downloads VODs, clips and chat from Twitch through an unofficial API; Twitch's terms do not allow automated downloading. For your own VODs, use Twitch's own download option.
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - Command-line downloader for video and audio from thousands of sites, including Twitch and Kick, that can also save YouTube live chat. Use it to fetch a VOD or clip you have rights to before clipping it. `Unlicense` · runs locally  
  Licence note: The repository and PyPI packages are Unlicense, but the standalone executables such as yt-dlp.exe bundle GPLv3+ code, so those binaries are GPLv3+ as a whole.  
  ⚠ Downloads videos from YouTube, Twitch, Kick and many other sites; their terms may not allow it for videos you don't own.

### Running AI locally

- [ComfyUI](https://github.com/Comfy-Org/ComfyUI) - Node-graph app for building and running image and video generation pipelines on your own GPU; the ComfyUI node packs in this catalog run inside it. `GPL-3.0` · local or cloud
- [llama.cpp](https://github.com/ggml-org/llama.cpp) - Runs GGUF language and vision models on CPU or GPU with a built-in OpenAI-compatible server, so moment-picking and captioning prompts can run offline. `MIT` · runs locally
- [Ollama](https://github.com/ollama/ollama) - Downloads and runs open language models such as Gemma and Qwen on your own computer, with a local API that clip tools can call to pick moments or write titles. `MIT` · local or cloud

## Wanted

Nothing is listed for these yet. Ideas for developers, not projects that exist:

- **Pipelines › Any game**: Game events for genres the big clipping apps barely cover, such as fighting games, racing, strategy, card games, MMOs, and retro or emulated games.
- **Pipelines › World of Warcraft**: PvP, Mythic+ and raid highlights, each knowing what a great moment looks like in that mode.
- **Pipelines › Marvel Rivals**: Team fights, multi-kills and ultimates.
- **Pipelines › Minecraft**: Builds, near-deaths and boss fights.
- **Pipelines › Valorant**: Clutches and aces.
- **Pipelines › League of Legends**: Team fights and objective steals.
- **Pipelines › Rocket League**: Goals, saves and aerials.
- **Pipelines › Ice hockey**: Goals, saves and fights from NHL-style broadcasts.

<!-- generated: end -->

## Contributing

Add one small file and open a pull request: [CONTRIBUTING.md](CONTRIBUTING.md) has the format and the criteria. Listing, updating and installing are free, and Clips Kitty takes no share of anything a developer earns.

## Licence

[![CC0](https://mirrors.creativecommons.org/presskit/buttons/88x31/svg/cc-zero.svg)](https://creativecommons.org/publicdomain/zero/1.0/)

The list and its data are [CC0-1.0](LICENSE): to the extent possible under law, the contributors have waived all copyright and related rights to them. This covers the catalog only. Every project listed here keeps its own licence, and nothing here changes it.
