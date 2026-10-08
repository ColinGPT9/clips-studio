# Keyword rater

An example Clips Kitty plugin that works on a video's moments after they are found, laid out the way a plugin's own repository would be. Copy this folder to start your own plugin that rates or understands moments.

## What it does

Clips Kitty finds a video's moments first, or Sports, Gaming scoring or a pipeline does. When you choose this plugin under **Rate & understand** as you add a video, it is handed those moments and reads what is said during each one. When the commentary says one of your words:

- **Rate.** It adds `bonus` points to the moment's score, never going above 100. Scores decide which clips are made and their order, and which are posted when a channel posts only the best few.
- **Understand.** It adds a note for each word said, up to 5 for one moment: `The commentary says "{word}" here`. Clips Kitty gives notes like this to the AI when it writes a new clip's title, description and hashtags.

Chosen only to rate, it only rates. Chosen only to understand, it only notes. Chosen for both with the same settings, it runs once and does both.

A moment where none of your words is said gets no answer, so it keeps its score. A word matches as a whole word in any case, so `win` doesn't match `window`. A moment it rates is held to the job's minimum score, like any rated moment: if its new score is still under the minimum, the moment is set aside, unless it is a must-have.

For Quarkbloom Arena (a made-up game), the words could be `quark burst, triple bloom, arena wipe`.

**What it does not do.** It only knows what is said. It doesn't know what happens on screen, or whether a word was said about this moment at all, and it never finds moments of its own. Treat it as a demonstration of rating and understanding, not as a judge of what makes a good clip.

With no words set, it stops with an error, and Clips Kitty makes the clips without it.

## Files

```text
clipskitty.yaml   the manifest: moments and transcript in, ratings and context (notes) out
src/main.py       reads what is said in each moment, rates it and notes the words
LICENSE           MIT
```

`src/main.py` imports only the Python standard library and `clipskitty_sdk`, which Clips Kitty puts on the plugin's path when it runs it.

## Settings

| Setting | Default | |
|---|---|---|
| `words` | none | Words or phrases to listen for, separated by commas |
| `bonus` | 15 | Points a moment gains when one of them is said (1 to 50) |

## Permissions

`transcript.read`, to read what is said during each moment. It runs on your PC and sends nothing anywhere.

## Trying it

With a Clips Kitty checkout's `sdk/python` on `PYTHONPATH`:

```text
python -m clipskitty_sdk validate .
python -m clipskitty_sdk run . --transcript transcript.json --set "words=quark burst, triple bloom"
```

`transcript.json` is `{"language": "en", "segments": [{"start": 0, "end": 4.5, "text": "..."}]}`. Without `--moments`, the plugin gets five sample moments spread through the video; with no video, the end of the transcript gives the video's length. Pass `--moments moments.json` (a list of `{"start": ..., "end": ..., "score": ...}`, or a finder's `result.json`) to hand it moments of your own. No video is needed.

## Requirements

Nothing for creators: in the installed app it runs on Clips Kitty's own Python. To develop it you need Python 3.10 or newer.
