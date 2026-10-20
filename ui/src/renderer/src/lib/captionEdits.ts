/** What a word fixed or muted in the editor's transcript does to the clip's
 *  caption text: a retyped word replaces the one Whisper misheard, and a
 *  muted word is censored (f**k), so platform moderation can't read it from
 *  the screen either. Apply sends the lines this makes as caption_lines, and
 *  the editor draws them over the video until then.
 *
 *  Worked out again from the clip's own lines every time, so taking a fix or
 *  a mute back puts the real text back.
 *
 *  A word is found in its line by its letters and digits in any script, not
 *  by \b: \b knows only a-z and 0-9, so an accented word ("perché", "città")
 *  was never found and its fix went nowhere, and "perch" looked like a whole
 *  word inside "perché" (#147).
 *
 *  Standalone, with type-only imports, so tests run it under Node
 *  (tests/test_ui_caption_edits.py). */

import type { CaptionLine, MutedWord } from './types'

/** A user text correction for one transcript word (misheard by Whisper): the
 *  word that starts at `start`, in clip seconds, was `from` and reads `to`. */
export interface WordEdit {
  start: number
  from: string
  to: string
}

/** Punctuation at either end of a word. Whisper words often carry it
 *  ("word," / "word.") where the caption may not, so a word is matched
 *  without it. Only the ends: what is inside is the word's own, and the
 *  caption has it too (the hyphen in "peut-être", the comma in "3,5", the
 *  dots in "U.S.", the zero-width non-joiner inside a Persian word), so
 *  taking it out too left the word never found. Marks stay, since in
 *  Devanagari and Thai a vowel is one. */
const AROUND_A_WORD = /^[^\p{L}\p{M}\p{N}'’]+|[^\p{L}\p{M}\p{N}'’]+$/gu

/** As written: the dots in "U.S." are dots, not any character. */
function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** The first place `word` stands on its own in a line, any case. */
function wordPattern(word: string): RegExp | null {
  const core = word.replace(AROUND_A_WORD, '')
  if (!core) return null
  return new RegExp(`(?<![\\p{L}\\p{M}\\p{N}])${escape(core)}(?![\\p{L}\\p{M}\\p{N}])`, 'iu')
}

/** What was typed for `from`, without the punctuation `from` itself had at
 *  either end. Only the letters are replaced in the line, and the line keeps
 *  its own punctuation around them; the box opens on Whisper's word
 *  ("ciao,"), so what is typed back carries that comma too, and putting it
 *  in as typed left the line reading "ciao,,". Punctuation that was typed
 *  differently from the word's own is kept. */
function typedLetters(from: string, to: string): string {
  const core = from.replace(AROUND_A_WORD, '')
  const at = from.indexOf(core)
  const lead = from.slice(0, at)
  const trail = from.slice(at + core.length)
  let out = to
  if (lead && out.startsWith(lead)) out = out.slice(lead.length)
  if (trail && out.endsWith(trail)) out = out.slice(0, out.length - trail.length)
  return out || to
}

/** A muted word as the caption shows it: its first and last letters kept. */
export function censorMatch(matched: string): string {
  return matched.length <= 2 ? '**' : matched[0] + '*'.repeat(matched.length - 2) + matched[matched.length - 1]
}

/** Whether two spellings are the same word, punctuation at the ends and case aside. */
function sameWord(a: string, b: string): boolean {
  return a.replace(AROUND_A_WORD, '').toLowerCase() === b.replace(AROUND_A_WORD, '').toLowerCase()
}

/** The lines with every fix applied, then every mute. A fix changes the
 *  first place its word stands in each line it starts in (give or take
 *  0.05 s), a mute each line its word is heard in. */
export function applyTextEdits(
  base: readonly CaptionLine[],
  wordEdits: readonly WordEdit[],
  mutedWords: readonly MutedWord[]
): CaptionLine[] {
  return base.map((line) => {
    let text = line.text
    for (const e of wordEdits) {
      if (e.start >= line.start - 0.05 && e.start < line.end + 0.05) {
        const re = wordPattern(e.from)
        // A function, so a $ in what was typed stays as typed: in a
        // replacement string "$&" would put the old word back.
        const to = typedLetters(e.from, e.to)
        if (re) text = text.replace(re, () => to)
      }
    }
    for (const m of mutedWords) {
      if (m.end > line.start && m.start < line.end) {
        // A muted word that was also corrected reads as corrected by now:
        // looked for by Whisper's spelling it was never found, and the
        // corrected word went out uncensored.
        const fix = wordEdits.find((e) => e.start >= m.start - 0.05 && e.start < m.end && sameWord(e.from, m.word))
        const re = wordPattern(fix ? typedLetters(fix.from, fix.to) : m.word)
        if (re) text = text.replace(re, censorMatch)
      }
    }
    return { ...line, text }
  })
}
