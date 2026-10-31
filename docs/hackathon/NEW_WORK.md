# New work submitted: Clips Kitty Twitch Highlights

This entry is the Twitch community-to-highlight workflow built during the
hackathon submission period. It enhances the existing open-source Clips Kitty
engine. It does not claim that the entire desktop application, editor,
Marketplace, SDK or publishing infrastructure was created for this event.

## New Twitch work and dated source evidence

The dates below are read from preserved Git commit metadata. Commit links show
the actual changes; these records are evidence, not an organizer eligibility decision.

| Contribution | Date (author offset preserved) | Commit |
|---|---|---|
| Twitch connected accounts, the plugin account gateway and stream workflows (#182) | 2026-10-25T13:45:42-04:00 | [c93ca98](https://github.com/ColinGPT9/clips-studio/commit/c93ca98b9c597260e38a907e401b5f003f4b3e3f) |
| Bind Twitch recordings to viewer nominations in desktop | 2026-10-26T17:06:49-04:00 | [9c4543f](https://github.com/ColinGPT9/clips-studio/commit/9c4543fe5be3c1a526dabfd716f72a49a9c4545b) |
| Allow viewer-authorized EventSub on selected public channels | 2026-10-27T12:20:40-04:00 | [c613304](https://github.com/ColinGPT9/clips-studio/commit/c6133044942eca07eef60aa1fa483e454cfe0275) |
| Collect Twitch nominations from a viewer-selected channel | 2026-10-27T15:40:26-04:00 | [d766dc9](https://github.com/ColinGPT9/clips-studio/commit/d766dc9288da970ad82ee7e0ac6534c8760c0251) |
| Allow selected public channels in desktop VOD timestamp links | 2026-10-27T16:14:40-04:00 | [99ab168](https://github.com/ColinGPT9/clips-studio/commit/99ab1680ab9bdcc344fe72a958d71e36b7c71a9e) |
| Show live Twitch activity and add optional local host narration | 2026-10-27T23:27:35-04:00 | [4583758](https://github.com/ColinGPT9/clips-studio/commit/4583758fb6b68df24d9feae57222116446cbf5fe) |

The new workflow lets a viewer select a broadcaster, receive community
nominations through EventSub, discover the corresponding recording, check its
timing before binding it, and see live chat activity in the desktop app. Codex
session `01a179b9-a6c3-7760-bbdf-dabc10d0b1d3` records implementation and checks.

## Existing foundation and license

The pre-existing engine supplies editing, local model execution, rendering,
export, general Marketplace/SDK and publishing infrastructure. Its Git history
and AGPL-3.0 license are preserved. The SDK has its separate MIT license.
The source archive includes the repository licenses and existing notices.
No repository reset, rewritten commit date or rebranding is used as evidence
that old code is new. GTA detector improvements are separately disclosed in the
linked plugin repository; its frozen behavior remains version 0.5.2.

## What the official rules require

The [official rules](https://twitchcon-codex.devpost.com/rules), section 4,
require a new project built during the submission period and a repository URL
with the source, assets and setup needed for judging. They do not state that
the Git hosting repository itself must be newly created.

The Intellectual Property subsection explicitly permits submissions that use
open-source software and enhance its functionality, subject to its licenses.
This permission does not remove the new-project requirement. The submitted
scope is the new Twitch workflow above, built on the disclosed existing engine.
The organizers make the eligibility determination. If further clarification is
needed, ask whether this specific new enhancement is accepted; simply copying
an older project into a fresh repository would not resolve that question.

## What the demo establishes

Actual viewer authorization, EventSub chat reception, start/stop controls and
visible activity were recorded in the real desktop app on xQc's GTA V stream.
A continuous excerpt of that same broadcast was separately processed by the
installed GTA pipeline and shown in the editor. The demo does not assert that
live nominations generated the selected clip or that live clock alignment and
social delivery were verified. Setup and test evidence are linked in this folder.
