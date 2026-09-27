# Translating and testing Clips Kitty in your language

**The person who wrote this app only speaks English.**

Clips Kitty ships in 19 languages, and the maintainer cannot tell whether 18
of them read naturally, use the right words for video editing, or make sense to
somebody who actually speaks the language. Machine translation gets the meaning
across and still sounds wrong, and there is no way to notice that from the
outside.

So corrections are wanted, and being blunt is welcome. If something is
understandable but no native speaker would say it that way, that is worth
reporting. You are not being rude; you are doing the thing this document exists
to ask for.

**Reporting a problem is a complete contribution.** You never have to open a
pull request. Saying "this word is wrong, here is the right one" is enough.

---

## The languages

Each language has its own issue. Find yours here, or in
[the issue list](https://github.com/ColinGPT9/clips-studio/issues?q=is%3Aissue+is%3Aopen+label%3Atranslation).

| Complete, needs **checking** | Half-finished, needs **finishing** |
|---|---|
| [العربية (Arabic)](https://github.com/ColinGPT9/clips-studio/issues/52) | [ไทย (Thai)](https://github.com/ColinGPT9/clips-studio/issues/46) |
| [Deutsch (German)](https://github.com/ColinGPT9/clips-studio/issues/53) | [Tagalog (Filipino)](https://github.com/ColinGPT9/clips-studio/issues/47) |
| [Español (Spanish)](https://github.com/ColinGPT9/clips-studio/issues/54) | [اردو (Urdu)](https://github.com/ColinGPT9/clips-studio/issues/49) |
| [Français (French)](https://github.com/ColinGPT9/clips-studio/issues/55) | [Tiếng Việt (Vietnamese)](https://github.com/ColinGPT9/clips-studio/issues/50) |
| [हिन्दी (Hindi)](https://github.com/ColinGPT9/clips-studio/issues/56) | |
| [Bahasa Indonesia (Indonesian)](https://github.com/ColinGPT9/clips-studio/issues/57) | |
| [日本語 (Japanese)](https://github.com/ColinGPT9/clips-studio/issues/58) | |
| [Português (Portuguese)](https://github.com/ColinGPT9/clips-studio/issues/59) | |
| [Русский (Russian)](https://github.com/ColinGPT9/clips-studio/issues/60) | |

বাংলা (Bengali), Italiano (Italian), 한국어 (Korean), Türkçe (Turkish) and 中文
(Chinese) were finished by contributors
([#95](https://github.com/ColinGPT9/clips-studio/pull/95),
[#90](https://github.com/ColinGPT9/clips-studio/pull/90),
[#45](https://github.com/ColinGPT9/clips-studio/issues/45),
[#96](https://github.com/ColinGPT9/clips-studio/pull/96),
[#107](https://github.com/ColinGPT9/clips-studio/pull/107)); a check by
another speaker is just as welcome for those.

The complete ones need **checking**. The half-finished ones stopped at 57
strings and need **finishing**: `ko.json` has the 125 strings the app has
long had, so comparing your file with it shows what is missing. Strings added
to the app since then show in English until someone translates them.
That is a known gap, not something to report.

Your language not listed? [Ask for it](https://github.com/ColinGPT9/clips-studio/issues/61).

## Where the words live

```
ui/src/renderer/src/locales/<code>.json
```

A flat JSON file: the English string is the key, your language is the value.

```json
{
  "Loading clips…": "Cargando clips…",
  "No clips generated.": "No se generaron clips."
}
```

Keys must match the English exactly, including punctuation and the `…`
character. A key that does not match is simply not found, and the app falls
back to English.

## Setting the language

Settings, then the language selector. It defaults to your operating system's
language, so it may already be set. Changing it does not need a restart.

## What to look for

Ordinary translation problems first: wrong words, bad grammar, missing
translations, and text that has clearly been run through a machine.

Then the ones that are easy to miss:

- **Technical vocabulary.** "Clip", "render", "timeline", "caption"
  and "watermark" often have an established word among video editors in your
  language that is not the dictionary translation.
- **Consistency.** The same English word should not become three different
  words in three screens.
- **Text that outgrows its button.** German runs long, and a translation that
  is correct but three times the length breaks the layout. Report it; a
  shorter wording is a real fix.
- **Right-to-left.** Arabic and Urdu should read right to left throughout,
  including punctuation and numbers.
- **Labels that do not match what the button does.** A correct translation of
  the wrong word is still wrong.

## Reporting

Post in your language's issue. This format is easy to act on:

```
Current:   [what the app says now]
Suggested: [what it should say]
Why:       [one line]
```

Anything from one string to a hundred is welcome.

## Testing the AI, if you want to

Optional, and a much bigger job than the wording. Skip it freely.

Clips Kitty transcribes speech, picks moments, and writes titles, all locally.
Whether it does that well in your language is unknown, and nobody has measured
it. If you want to find out, run a video that is naturally spoken in your
language, a podcast, an interview, a talking-head video, and report what
happened.

Useful to know:

- did transcription get the words right, and the timings
- were the clips it picked sensible moments
- were the titles in **your** language, or did it produce English
- were the burned-in subtitles accurate

Two honest caveats:

- **Only three models have ever been tested**: `gemma:7b`, `gemma3:4b` and
  `gemma3:12b`. Everything else in the app is listed because it is a sensible
  size and freely licensed, not because it has been measured. Qwen is worth
  trying for non-English content, since it is reputed to be strong
  multilingually. That is reputation, not a measurement, which is exactly the
  gap worth closing. See
  [#38](https://github.com/ColinGPT9/clips-studio/issues/38).
- **AI dubbing works in an installed copy as of 1.1.3.** The speech engine
  ships with the app. The voice for a language is downloaded the first time
  that language is dubbed (~60 MB), so the first run needs a connection.
  Languages with no Piper voice (Filipino, Thai, Korean) are subtitle-only,
  and the app says so rather than skipping them silently.

Say plainly which kind of problem you found, because the fixes are unrelated:

| Kind | Example |
|---|---|
| Translation | a button says the wrong thing |
| AI / model | the model misread a common expression |
| Layout | correct words, broken button |
| Transcription | the words were heard wrong |

## Sending a fix

If you want to make the change yourself:

1. Fork, and edit `ui/src/renderer/src/locales/<code>.json`.
2. Run it: `cd ui && npm install && npm run dev`, then switch to your language.
3. Open a pull request that mentions the issue number.

Change only your own locale file. Nothing else needs touching, and a pull
request that changes just one JSON file is quick to review and quick to merge.

[CONTRIBUTING.md](../CONTRIBUTING.md) covers the general setup.

## A note on reviewing

Do not assume the existing translation is right because it is already shipped.
Most of it has never been read by somebody who speaks the language. That is the
entire reason for asking.
