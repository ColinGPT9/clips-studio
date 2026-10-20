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

/** A word's own characters. Whisper words often carry punctuation ("word,"
 *  / "word.") that the caption may not, so a word is matched by these alone;
 *  marks stay, since in Devanagari and Thai a vowel is one. */
const NOT_IN_A_WORD = /[^\p{L}\p{M}\p{N}'’]/gu

/** The first place `word` stands on its own in a line, any case. */
function wordPattern(word: string): RegExp | null {
  const core = word.replace(NOT_IN_A_WORD, '')
  if (!core) return null
  const escaped = core.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`(?<![\\p{L}\\p{M}\\p{N}])${escaped}(?![\\p{L}\\p{M}\\p{N}])`, 'iu')
}

/** A muted word as the caption shows it: its first and last letters kept. */
export function censorMatch(matched: string): string {
  return matched.length <= 2 ? '**' : matched[0] + '*'.repeat(matched.length - 2) + matched[matched.length - 1]
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
        if (re) text = text.replace(re, () => e.to)
      }
    }
    for (const m of mutedWords) {
      if (m.end > line.start && m.start < line.end) {
        const re = wordPattern(m.word)
        if (re) text = text.replace(re, censorMatch)
      }
    }
    return { ...line, text }
  })
}
