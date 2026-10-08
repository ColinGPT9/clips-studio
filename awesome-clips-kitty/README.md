# Awesome Clips Kitty

Pipelines, plugins, integrations, apps and tools that people have built with [Clips Kitty](https://github.com/ColinGPT9/clips-studio), the free app that turns long videos into short clips on your own PC. Everything here runs inside Clips Kitty or uses its local API or SDK. A project that does neither is not listed, however good it is.

This is a curated directory, not a list of everything: a maintainer checks each entry against the [inclusion criteria](CONTRIBUTING.md#what-gets-in) before it is listed in its section. Entries added by their authors and not checked yet are shown separately, under "Not yet checked". The same data, in [`registry/`](registry/), is what the Clips Kitty Marketplace shows, so adding a project here also makes it discoverable inside the app.

**How a project relates to Clips Kitty**

- **Built for Clips Kitty**: runs inside Clips Kitty. These are the pipelines and plugins you install from the Marketplace.
- **Built with Clips Kitty**: a separate app or tool that uses Clips Kitty's local API or SDK.

Like how another clipping app picks its moments? That belongs here as a pipeline: one that picks moments its way inside Clips Kitty, with that project's licence notice. A link to the other app is not an entry.

**Labels**

- **✓ Official**: made and maintained by the Clips Kitty project.
- **✓ Compatible**: this version passed Clips Kitty's automated checks (its manifest is valid, it installs, its requirements are met, and it runs on a sample video and gives an answer Clips Kitty accepts). A technical label, not a security review.
- **★ Featured**: picked by hand by a maintainer.
- Everything else is **Community**: made by someone outside the project, and nobody at Clips Kitty has read its code.

The numbers measure different things and are never added together: Clips Kitty installs, stars on GitHub, and a model's Hugging Face downloads. Every project keeps its own licence, shown like `MIT`, with a licence note where its models or parts have other terms. A ⚠ marks what to know before using a project: it sends your videos, audio or transcripts to an online service by default, downloads from sites whose terms may not allow it, has usage tracking switched on, or is archived or has had no commits for over a year.

<!-- generated: everything from here to the end marker comes from registry/ -->

## Contents

- [Pipelines](#pipelines)
  - [General](#general)
- [Integrations](#integrations)
  - [Streaming](#streaming)
  - [AI assistants](#ai-assistants)
- [Tools](#tools)
  - [For developers](#for-developers)
- [Wanted](#wanted)

## Pipelines

### General

_Pipelines for any kind of video._

- [Loud moments, cut on scene changes (example)](https://github.com/ColinGPT9/clips-studio/tree/HEAD/examples/pipelines/scene-cut-highlights) - An example pipeline. It finds stretches that are clearly louder than the rest of the video and starts each clip on the nearest scene cut before it, using FFmpeg only. It knows nothing about what is happening on screen: loud is not always interesting, and quiet highlights are missed. `MIT` · ✓ Official · ✓ Compatible

## Integrations

### Streaming

- [Clips Kitty OBS Plugin](https://github.com/ColinGPT9/clips-kitty-obs-plugin) - An OBS Studio dock that hands your stream to Clips Kitty once it really ends and shows the progress. Nothing runs while you are live. Pre-release. Needs Clips Kitty 1.2.0 or newer. `GPL-2.0-or-later` · ✓ Official · runs locally

### AI assistants

- [Clips Kitty MCP server and agent skill](https://github.com/ColinGPT9/clips-studio#ask-an-ai-agent-to-do-it) - Lets Claude, Cursor or any MCP client make clips from a link or a file, follow the job and read back the clips, through Clips Kitty's local API. Its tools can also post clips through the accounts connected in Clips Kitty. Ships with Clips Kitty. `AGPL-3.0-or-later` · ✓ Official · runs locally

## Tools

### For developers

- [Clips Kitty SDK for Python](https://github.com/ColinGPT9/clips-studio/tree/HEAD/sdk/python) - Write, check and test Clips Kitty plugins - the manifest validator, a local test host, and a client for the local API. MIT-licensed, so using it puts no licence on your plugin. `MIT` · ✓ Official · runs locally

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
