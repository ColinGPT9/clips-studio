# Roadmap

Where Clips Studio is going, and what is already done. Anything marked shipped
is in a release you can install today, not a plan.

---

## Now: Alpha

The alpha is public. Its purpose is not to be perfect. It is to find out what
breaks on machines that are not the developer's.

**Shipped**

- Windows installer that carries everything: the app, the engine, FFmpeg, the
  AI runtime, and the tracking and transcription models
- GitHub releases with notes written for creators
- [Changelog](CHANGELOG.md) and [Known issues](KNOWN-ISSUES.md)
- Bug reports and feature requests: issue templates, plus an in-app Feedback
  Hub that needs no GitHub account
- GitHub Discussions
- Security scanning: CodeQL, Dependabot, secret scanning, a
  [security policy](SECURITY.md)
- A container image for anyone who wants to work on the engine without
  installing PyTorch
- Website with per-platform pages, sitemap, Open Graph and structured data

**Outstanding**

- Code signing, so Windows stops warning that the installer is unsigned
- Screenshots and a demo video on the website
- Progress reporting for the scoring stage and for update downloads, so working
  cannot be mistaken for hung
- Automated Windows builds, so a dependency bump is provably safe rather than
  hoped-for

## Next: community

Making it worth someone's time to contribute.

- Good first issues and help-wanted issues with enough context to pick up cold
- Contributor recognition
- Example workflows, community showcases, performance benchmarks
- Voting on feature requests

Contributions are wanted in features, bug fixes, documentation, translations,
AI improvements and performance work. Start with
[CONTRIBUTING.md](CONTRIBUTING.md) and
[ARCHITECTURE.md](ARCHITECTURE.md).

## Later

None of this delays the alpha.

- **Gaming and split-screen**, done properly. The framing has to reliably find
  the action, and tracking has to tell a streamer from a character inside the
  game
- **Reaction videos**, which need the app to understand the video being reacted
  to, not just the words spoken over it
- **Android companion app** for Twitch, Kick and local files, within Google
  Play's policies
- Remote rendering management
- Plugin architecture and community extensions
- Creator analytics
- More models, better AI workflows, more platforms where they make sense

---

## Long-term

Clips Studio is a **local-first** creator platform. That word carries the whole
design: your footage, your workflows and your data stay on your machine, and
the app gets better as your hardware does rather than as someone's subscription
tier does.

Consumer AI hardware is improving quickly. The architecture is kept modular so
newer models and faster hardware can be adopted without a redesign. As
machines like the NVIDIA RTX Spark and future AI-accelerated GPUs arrive, the
same application should be able to run larger local models, infer faster,
analyse video better and automate more.

The goal is an open-source creator platform that becomes more capable over time
because local AI hardware does.

## What success looks like

- Creators can install it easily
- It is stable and reliable
- Developers can understand the codebase
- Contributors can submit improvements confidently
- Security follows current GitHub practice
- Documentation answers the common questions
- The website makes the value obvious
- People report bugs, propose features, and send code

In that order, because each one depends on the one before it. Reliable software
earns a good experience, which earns contributors, which earns a community.
