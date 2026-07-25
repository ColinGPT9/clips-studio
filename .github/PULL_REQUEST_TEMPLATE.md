<!-- Thanks for contributing to Clips Studio. Keep this short — a few honest
     sentences beat a filled-in form. Delete any section that doesn't apply. -->

## What this changes

<!-- One or two sentences. What behaviour is different after this PR? -->

Fixes #

## Why

<!-- The problem, not the patch. What was going wrong, or what couldn't you do? -->

## How you tested it

<!-- Be specific — "ran a 2h Twitch VOD, clips still land on sentence boundaries"
     is useful; "works fine" isn't. If it touches clipping, tracking, captions, or
     rendering, please test on a REAL video, not just a unit-sized one. -->

- [ ] `npm run typecheck` passes (in `ui/`)
- [ ] Tried the affected flow in the running app
- [ ] Tested on a real video — platform and length:

## Anything reviewers should know

<!-- Trade-offs you made, things you weren't sure about, follow-ups you left out.
     Flagging a known limitation here is much better than it being found later. -->

---

<!-- A few things that will come up in review, so you can save a round trip:

  * Comments should explain WHY, not what. Match the style around you.
  * Learned data must never be able to lower a clip's score — score
    contributions from accumulated knowledge stay additive, capped, and
    disableable (see ARCHITECTURE.md §14).
  * The LLM proposes, deterministic code disposes. Parsing and validation
    belong in Python, not in a prompt.
  * Optional subsystems fail soft: a broken side feature should degrade that
    feature, never break a pipeline run.
  * Changing scoring defaults, thresholds, or weights? Say what you tested it
    against — those numbers are tuned on real footage.
-->
