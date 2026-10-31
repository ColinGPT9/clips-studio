# Clips Kitty: Twitch Highlights

**Tagline:** Connect Twitch community signals to concise, editable local clips.

**Main code URL:** https://github.com/ColinGPT9/clips-studio/tree/feature/twitch-vod-binding

**Detector code URL:** https://github.com/ColinGPT9/Grand-Theft-Auto-6-Clips-Kitty-Plugin/tree/improve/gta-concise-moments

**Codex session ID:** `01a179b9-a6c3-7760-bbdf-dabc10d0b1d3`

**Final demo:** `Clips-Kitty-Demo.mp4`, under three minutes. The current
packet is maintained separately from the earlier 2:17 synthetic test recording.
The entrant will upload the final video publicly to YouTube and paste its real URL.

**New contribution and preserved foundation:** [NEW_WORK.md](NEW_WORK.md)
links the new Twitch features to original dated commits and distinguishes the
existing AGPL-3.0 engine. The rules do not require a fresh Git repository.

**Submission page:** https://devpost.com/submit-to/31742-codex-x-twitchcon-hackathon/manage/submissions

## Description

Clips Kitty is a Windows desktop clipping workflow for Twitch streamers and
their communities. This submission focuses on the new viewer-channel Twitch
workflow and GTA detector improvements, built on the existing open-source
desktop engine.

A viewer connects a Twitch account, chooses the channel to watch, and explicitly
starts collecting exact `!clip`, `!chaos` and `!funny` nominations. The host keeps
the authenticated reader separate from the broadcaster, preserves other active
listeners, and matches a finished stream to its recording. The recording-binding
screen requires an explicit timing check before nominations influence source
footage.

The separate GTA detector combines dialogue and event proposals before ranking,
uses source-grounded optional AI review with a bounded budget, and favors concise
moments. The desktop engine retains its shared Gaming layout, captions, audio,
editor and MP4 renderer. Longer output remains configurable. WoopSocial is an
existing optional publishing path; exporting does not publish a clip.

Codex inspected the repositories, implemented the host and detector changes,
created regressions, exercised the actual MCP workflow, rebuilt the backend,
and fixed a selected-channel timestamp-link failure found during real testing.
The included session reference, source changes and demo document that work.

## Verified results and limits

Real Twitch authorization, exact scopes, selected-channel metadata, EventSub
subscriptions, incoming chat delivery, connection persistence after restart,
consumer isolation and a verified archive timestamp link passed. The final
rebuilt backend also passed the real Twitch metadata/link checks.

The previous full host snapshot passed 5,397 tests with 16 skipped. The final
timestamp-link repair passed 212 affected checks and current CI/CodeQL. The
detector candidate passed 358 tests. Actual rendered exports contain audio and
fully decode.

The current demo uses the same xQc GTA V broadcast for its real Twitch
integration footage and its separately processed recording excerpt. The newer
source is native 1920x1080 at 60 fps; the full desktop editor is visible during
highlight playback. The older 480p xQc run and Fanum comparisons remain
historical test evidence, not the latest media in the submission packet.

Live nomination-to-recording clock alignment, native Twitch clip creation and
actual social delivery are not presented as verified. No chat messages, native
Twitch clips or social posts were sent by the demonstration.

## Built with

OpenAI Codex, Python, FastAPI, React, TypeScript, Electron, Twitch OAuth and
EventSub, FFmpeg, the Clips Kitty SDK, local transcription, Ollama and the shared
Gaming renderer.

## Next improvements

Observe the next live stream, verify its nomination/recording alignment, and
extend the private GTA quality evaluation. These are follow-up work and are not
requirements for the demonstrated viewer connection, editor or export flow.

## Submission checklist

- Use the revised full-editor HD demo; the earlier 2:17 synthetic recording is test evidence.
- Use the two branch URLs above; the new work remains in draft PR194 and PR9.
- Paste the description and actual Codex session ID above.
- Fill your own team/contact information and submit the form yourself.

The [official requirements](https://twitchcon-codex.devpost.com/rules) were checked
for the repository, description, session ID and under-three-minute YouTube demo.
No calendar schedule is added here.


## New-project rule and open-source foundation

Section 4 of the official rules requires the submitted new project to be built
during the submission period. Its Intellectual Property subsection explicitly
permits enhancing underlying open-source software while respecting its licenses.
The repository requirement calls for accessible source, assets and setup; it does
not require a newly created Git hosting repository. A repository reset would not
establish that old work is new. This entry identifies the new Twitch enhancement,
preserves the existing engine's license and history, and supplies dated source
changes in NEW_WORK.md. The organizers determine eligibility of that scope.
