# Editor suite

This folder holds the plan for Clips Kitty's new editor and the record of the work on it. Phase 2 builds the essential editor in 15 small steps, closing issues #192, #144, #190, #177, #203, #174 and #155, plus a built-in voice-over. The work stops after Phase 2; later phases start only on Colin's go.

| File | What it is |
|---|---|
| [`state.json`](state.json) | The tracker, and the source of truth: phases, steps, issue links, dependencies, acceptance criteria, results and defects. |
| [`STATUS.md`](STATUS.md) | A readable view of `state.json`: what is in progress and what comes next. |
| [`DECISIONS.md`](DECISIONS.md) | Numbered decision records: context, decision, why, what was rejected, and how to undo it. |
| [`RESEARCH.md`](RESEARCH.md) | The research behind the plan: outside projects and their licences, assets, models and AI providers. |
| [`USER-GUIDE.md`](USER-GUIDE.md) | The guide for creators, filled in as each feature ships. It is the only document the black-box tester reads. |
| [`BENCHMARKS.md`](BENCHMARKS.md) | How to run the golden renders and the render benchmark that every step is compared against. |

How a step moves: it is built, then checked by QC, a black-box tester, a performance check and a UX check, one after another. A step that users see then waits for Colin's click-through and gets a short narrated promo video. Each step lands as one pull request.

Plain auto-clipping must not change. Step 0's guards (argv snapshots, golden renders, an import guard) check every later step.

When a step finishes, its results and defects go into `state.json`, and `STATUS.md` is updated to match.
