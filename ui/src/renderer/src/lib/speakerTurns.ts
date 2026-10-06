import type { CaptionLine, SpeakerTurn, Word } from './types'

/** The second speaker's caption colour in the editor, the way the render does
 *  it (video/captions.py: paint_turns, _speakers, build_caption_lines,
 *  tag_lines). The render heard who talks when and saved it with the clip as
 *  speaker_turns; nothing here listens to anything. What a person fixes by
 *  hand is saved beside it as speaker_edits and laid over it.
 *
 *  The editor draws from these functions and nothing else: what it shows is
 *  what the render works out, from the same turns, the same fixes and the
 *  same words. Kept in step with the Python by
 *  tests/test_ui_speaker_turns_sync.py. Words here are the ones
 *  GET /clips/{id}/words gives: clip seconds, cut to the clip, rounded to a
 *  hundredth, which is how the render reads them too. */

/** A word this short has no room for a caption of its own: it goes with the
 *  word beside it (video/captions.py _TINY). */
const TINY = 0.05

/** Turns as the engine reads them: an end after the start, a speaker above 0. */
function turnsOf(turns: readonly SpeakerTurn[] | null | undefined): SpeakerTurn[] {
  const out: SpeakerTurn[] = []
  for (const turn of Array.isArray(turns) ? turns : []) {
    if (!Array.isArray(turn)) continue
    const start = Number(turn[0])
    const end = Number(turn[1])
    const speaker = turn.length > 2 ? Math.trunc(Number(turn[2])) : 1
    if (Number.isFinite(start) && Number.isFinite(end) && end > start && speaker > 0) {
      out.push([start, end, speaker])
    }
  }
  return out
}

/** A clip's speaker_edits as the engine reads them: unlike a turn, one can
 *  name the main speaker (0) and can lie outside the clip. */
function statementsOf(edits: readonly SpeakerTurn[] | null | undefined): SpeakerTurn[] {
  const out: SpeakerTurn[] = []
  for (const edit of Array.isArray(edits) ? edits : []) {
    if (!Array.isArray(edit) || edit.length < 3) continue
    const start = Number(edit[0])
    const end = Number(edit[1])
    const speaker = Math.trunc(Number(edit[2]))
    if (Number.isFinite(start) && Number.isFinite(end) && end > start && speaker >= 0) {
      out.push([start, end, speaker ? 1 : 0])
    }
  }
  return out
}

/** The turns to caption by: what the render heard, with what a person said
 *  in the editor laid over it. Each edit is a statement about its stretch,
 *  whatever was heard there, and a later one overrules an earlier one. */
export function paintTurns(
  turns: readonly SpeakerTurn[] | null | undefined,
  edits: readonly SpeakerTurn[] | null | undefined
): SpeakerTurn[] {
  let held = turnsOf(turns)
  for (const [from, to, speaker] of statementsOf(edits)) {
    const cut: SpeakerTurn[] = []
    for (const [a, b, who] of held) {
      if (a < from) cut.push([a, Math.min(b, from), who])
      if (b > to) cut.push([Math.max(a, to), b, who])
    }
    held = cut
    if (speaker) held.push([from, to, speaker])
  }
  held.sort((x, y) => x[0] - y[0] || x[1] - y[1] || x[2] - y[2])
  const painted: SpeakerTurn[] = []
  for (const [a, b, who] of held) {
    const last = painted[painted.length - 1]
    if (last && last[2] === who && a <= last[1]) last[1] = Math.max(last[1], b)
    else painted.push([a, b, who])
  }
  return painted
}

/** The end of a sentence (video/captions.py _SENTENCE_END). */
const SENTENCE_END = /[.!?…。！？]['")\]]*$/
const endsSentence = (w: Word): boolean => SENTENCE_END.test(String(w.word ?? '').trim())

/** For each word, the word that decides who says it: itself, or for a word
 *  too short to stand alone one of the two words its run of short words sits
 *  between (-1 in a clip of nothing but short words).
 *
 *  Words up to the last one in the run that ends a sentence finish what the
 *  speaker before them was saying; the rest open what comes next. With no
 *  full stop to go by, in the run or on the word before it, the run goes
 *  with the nearer of the two words, the one after when they are as near. */
export function leansOn(words: readonly Word[]): number[] {
  const count = words.length
  const short = words.map((w) => w.end - w.start <= TINY)
  const on = short.map((tiny, i) => (tiny ? -1 : i))
  let i = 0
  while (i < count) {
    if (!short[i]) {
      i++
      continue
    }
    let j = i
    while (j < count && short[j]) j++ // the run is words i to j - 1
    const before = i - 1
    const after = j
    let part: number | null
    if (before < 0 && after >= count) part = null
    else if (before < 0) part = i
    else if (after >= count) part = j
    else {
      let last = -1
      for (let k = i; k < j; k++) if (endsSentence(words[k])) last = k
      if (last >= 0) part = last + 1
      else if (endsSentence(words[before])) part = i
      else part = words[i].start - words[before].end < words[after].start - words[j - 1].end ? j : i
    }
    if (part !== null) {
      on.fill(before, i, part)
      on.fill(after, part, j)
    }
    i = j
  }
  return on
}

/** Who says each word: the speaker of the turn its middle falls in, 0 (the
 *  main speaker) outside every turn; a word too short to stand alone, whoever
 *  says the word it leans on. */
export function speakersOf(words: readonly Word[], turns: readonly SpeakerTurn[] | null | undefined): number[] {
  const spans = turnsOf(turns)
  const own = words.map((w) => {
    const middle = (w.start + w.end) / 2
    const turn = spans.find(([a, b]) => a <= middle && middle < b)
    return turn ? turn[2] : 0
  })
  return leansOn(words).map((i) => (i < 0 ? 0 : own[i]))
}

/** Caption lines from a clip's words, and for each word the line it went
 *  into (-1 where its group has no length: the render burns no caption that
 *  short, so the word is in none). */
function grouped(
  words: readonly Word[],
  size: number,
  turns: readonly SpeakerTurn[] | null | undefined
): { lines: CaptionLine[]; held: number[] } {
  const heard = turnsOf(turns).length > 0
  const who = heard ? speakersOf(words, turns) : words.map(() => 0)
  const short = words.map((w) => w.end - w.start <= TINY)
  const n = Math.max(1, Math.trunc(size))
  const lines: CaptionLine[] = []
  const held = words.map(() => -1)
  let first = 0
  for (let i = 1; i <= words.length; i++) {
    if (i < words.length && who[i] === who[first]) continue
    // What one person says, n words to a group: [from, to) in the list.
    let groups: [number, number][] = []
    for (let g = first; g < i; g += n) groups.push([g, Math.min(i, g + n)])
    if (heard) {
      // Captions that hold a word long enough to stand alone: a group of
      // nothing but shorter words goes in the caption before it (the first
      // of them in the one after). Alone it would be on screen for an
      // instant, or not burned at all, and its colour would be decided by a
      // word in another caption. A caption takes at most three words more
      // than are set for one: a transcript can squeeze a whole burst of
      // words into one instant, and the rest of those stay as they are
      // without the option.
      const limit = n + 3
      const out: [number, number][] = []
      let waiting: [number, number][] = [] // short groups in front of the first caption
      let seen = false // a caption is made
      let room = false // and the last one can still take words
      for (const group of groups) {
        const [g, to] = group
        if (short.slice(g, to).some((tiny) => !tiny)) {
          let from = g
          while (waiting.length && to - waiting[waiting.length - 1][0] <= limit) from = waiting.pop()![0]
          out.push(...waiting, [from, to])
          waiting = []
          seen = room = true
        } else if (!seen) waiting.push(group)
        else if (room && to - out[out.length - 1][0] <= limit) out[out.length - 1][1] = to
        else {
          out.push(group)
          room = false
        }
      }
      if (seen) groups = out // else nothing but short words: as they were
    }
    for (const [g, to] of groups) {
      const start = words[g].start
      let end = words[to - 1].end
      if (heard) {
        // A short word at the end can be timed inside the word before it:
        // the caption stays up until that word has been said.
        let last = to - 1
        while (last > g && short[last]) last--
        for (let k = last; k < to; k++) end = Math.max(end, words[k].end)
      }
      if (end <= start) continue
      held.fill(lines.length, g, to)
      lines.push({
        start,
        end,
        text: words
          .slice(g, to)
          .map((w) => w.word)
          .join(' '),
        ...(who[first] ? { speaker: who[first] } : {})
      })
    }
    first = i
  }
  return { lines, held }
}

/** Caption lines from a clip's words, `size` words to a line, a line ending
 *  where the speaker changes so no caption mixes two people's words. */
export function groupWords(
  words: readonly Word[],
  size: number,
  turns: readonly SpeakerTurn[] | null | undefined = []
): CaptionLine[] {
  return grouped(words, size, turns).lines
}

/** For each word, which of groupWords' lines it is in; -1 for a word in none. */
export function groupOfWords(
  words: readonly Word[],
  size: number,
  turns: readonly SpeakerTurn[] | null | undefined = []
): number[] {
  return grouped(words, size, turns).held
}

/** Lines marked with who says them: `speaker` on a line that is mostly the
 *  other speaker's, and on no other. With the clip's `words`, a line goes by
 *  who says the words in it, each counted for as long as it is spoken, so a
 *  pause inside a caption decides nothing; without, or for a line with no
 *  word in it to go by, by who talks through more of its time.
 *
 *  The words that count are the ones long enough to stand alone (a shorter
 *  one is said with a word beside it, which may be in another line), each in
 *  the line its middle falls in, from the line's start up to its end. That
 *  is the stretch a turn covers, so a statement about a caption's stretch
 *  always decides that caption. */
export function tagLines(
  lines: readonly CaptionLine[],
  turns: readonly SpeakerTurn[] | null | undefined,
  words: readonly Word[] = []
): CaptionLine[] {
  const spans = turnsOf(turns)
  const voices = words
    .filter((w) => w.end - w.start > TINY)
    .map((w) => {
      const middle = (w.start + w.end) / 2
      const turn = spans.find(([a, b]) => a <= middle && middle < b)
      return { middle, spoken: w.end - w.start, speaker: turn ? turn[2] : 0 }
    })
  return lines.map((line) => {
    const { speaker: _old, ...plain } = line
    let held = new Map<number, number>()
    let whole = 0
    for (const { middle, spoken, speaker } of voices) {
      if (middle < line.start || middle >= line.end) continue
      whole += spoken
      if (speaker) held.set(speaker, (held.get(speaker) ?? 0) + spoken)
    }
    if (whole <= 0) {
      held = new Map<number, number>()
      whole = line.end - line.start
      for (const [start, end, speaker] of spans) {
        const shared = Math.min(line.end, end) - Math.max(line.start, start)
        if (shared > 0) held.set(speaker, (held.get(speaker) ?? 0) + shared)
      }
    }
    let best = 0
    let most = 0
    held.forEach((seconds, speaker) => {
      if (seconds > most) [best, most] = [speaker, seconds]
    })
    return most > whole / 2 ? { ...plain, speaker: best } : plain
  })
}

/** Whether the word list accounts for every caption of the clip: lines made
 *  from it, `size` words to a line, read the same as `lines` (the clip's own,
 *  made that way by the engine). It does not where a stretch of the
 *  transcript has no word timings: the render spreads that text out evenly,
 *  and the word list is given none of it, so lines made from the words would
 *  be missing those captions. */
export function wordsMakeLines(words: readonly Word[], lines: readonly CaptionLine[], size: number): boolean {
  const read = (all: readonly CaptionLine[]): string =>
    all.flatMap((line) => String(line.text ?? '').split(/\s+/).filter(Boolean)).join(' ')
  return read(groupWords(words, size)) === read(lines)
}

// ---- fixing it by hand ------------------------------------------------------------
// In the editor a click says who is talking: a statement about a stretch of
// the clip, added to the list the render lays over what it heard. These work
// out which stretch a click means.

/** How far linesOfWords looks for a word in the captions' text: this many
 *  written words on. EDGE: saved line times are rounded to a hundredth
 *  (video/captions.py _EDGE). */
const AHEAD = 8
const EDGE = 0.006

/** The stretch of the clip that is one word's alone: when it is said, cut
 *  back where the middle of another word falls inside it (two people talking
 *  at once), so a statement about this word says nothing about that one. */
export function ownSpan(words: readonly Word[], i: number): [number, number] {
  const middle = (words[i].start + words[i].end) / 2
  let from = words[i].start
  let to = words[i].end
  words.forEach((other, j) => {
    if (j === i || other.end - other.start <= TINY) return
    const theirs = (other.start + other.end) / 2
    // Two words said at the very same moment can't be parted by when they
    // are said: a statement about one is about both, and both show it.
    if (Math.abs(theirs - middle) < 1e-6) return
    if (theirs < middle && theirs >= from) from = (theirs + middle) / 2
    else if (theirs > middle && theirs < to) to = (theirs + middle) / 2
  })
  return [from, to]
}

/** For each word, the saved caption that holds it (-1 for none): the one it
 *  is written in, found by reading the words and the captions' text side by
 *  side, so a word still being said while the next caption is up (two people
 *  at once) stays with its own.
 *
 *  A word that is in no caption as it was said (a muted one is written
 *  "f**k", a retyped one differently) goes by the clock: the caption its
 *  middle falls in, and on the instant one ends and the next begins the one
 *  whose text has the word at that edge. Blank captions hold nothing: the
 *  render burns none. */
export function linesOfWords(words: readonly Word[], lines: readonly CaptionLine[]): number[] {
  const said = lines.map((line) => String(line.text ?? '').trim().toLowerCase())
  const real = (l: number): boolean => lines[l].end > lines[l].start && said[l] !== ''
  // Every word written in the captions, in order, with its caption.
  const written: { token: string; line: number }[] = []
  said.forEach((text, l) => {
    if (real(l)) for (const token of text.split(/\s+/)) written.push({ token, line: l })
  })
  let next = 0
  return words.map((w) => {
    const middle = (w.start + w.end) / 2
    const word = String(w.word ?? '').trim().toLowerCase()
    // Written a few words further on at most (words can have been muted,
    // retyped or taken out), and in a caption that is up when it starts to
    // be said: every word a caption was made from starts inside it. The
    // same word a caption or two later is another word.
    for (let k = next; word !== '' && k < Math.min(written.length, next + AHEAD); k++) {
      const line = lines[written[k].line]
      if (written[k].token === word && w.start >= line.start - EDGE && w.start <= line.end + EDGE) {
        next = k + 1
        return written[k].line
      }
    }
    let held = -1
    for (let l = 0; l < lines.length; l++) {
      if (!real(l) || middle < lines[l].start || middle > lines[l].end) continue
      if (held < 0) held = l
      else if (!said[held].endsWith(word) && said[l].startsWith(word)) held = l
    }
    return held
  })
}

/** A list of statements without the ones that no longer say anything: an
 *  earlier one wholly inside a later one is overruled all the way across. */
export function compactEdits(edits: readonly SpeakerTurn[]): SpeakerTurn[] {
  const all = statementsOf(edits)
  return all.filter(([from, to], k) => !all.some(([a, b], j) => j > k && a <= from && to <= b))
}

// ---- the editor's Fix speakers mode ------------------------------------------------
// Everything that mode shows, and the statements each click makes, worked out
// in one place from what the editor knows about the clip. The editor keeps
// the list of statements and calls these; the tests call them too.

/** What the editor knows about the clip whose speakers are being fixed. */
export interface SpeakerClip {
  /** Its words, as GET /clips/{id}/words gives them. */
  words: readonly Word[]
  /** What the last render heard: the clip's speaker_turns. */
  heard: readonly SpeakerTurn[] | null | undefined
  /** Its own caption lines (GET /clips/{id}/captions); null until they arrive. */
  lines: CaptionLine[] | null
  /** Those lines were saved with the clip: the render keeps them as they are. */
  saved: boolean
  /** The next render will be sent lines: a word is muted or retyped. */
  sending: boolean
  /** Words to a caption as set now, and as the clip's own lines were made. */
  perCaption: number
  storedPerCaption: number
  /** How long the clip is, in seconds. */
  duration: number
}

export interface SpeakerFixing {
  /** Captions are switched whole: the render is given the clip's own lines,
   *  keeps their grouping and colours each of them whole. */
  whole: boolean
  /** The word list accounts for every caption of the clip, so captions made
   *  from it are the clip's captions. Not so where a stretch of the
   *  transcript has no word timings. */
  covered: boolean
  /** The lines the next render will be GIVEN for these turns: the clip's
   *  own, or lines made from the words that end where the speaker changes.
   *  null when it will make its own. */
  given: (turns: readonly SpeakerTurn[]) => CaptionLine[] | null
  /** Who says each word (1 the other speaker, 0 the main one) as it will burn
   *  with this list of fixes: by its caption where the render is given the
   *  lines, else word by word. */
  speakers: (edits: readonly SpeakerTurn[]) => number[]
  /** The stretch of the clip a click on a word speaks for: its caption's, or
   *  the word's own. null where there is nothing to switch. */
  stretch: (i: number) => [number, number] | null
  /** The list after a click on a word: one more statement, saying the
   *  opposite of what the word shows. Clicked again, that statement is taken
   *  back rather than another piled on it. The same list where no statement
   *  about the word would switch it. */
  click: (edits: readonly SpeakerTurn[], i: number) => SpeakerTurn[]
  /** The list after a shift-click: every word from `from` (the last one
   *  clicked) up to `i`, said to be as `from` shows. */
  upTo: (edits: readonly SpeakerTurn[], from: number, i: number) => SpeakerTurn[]
  /** The list after Swap speakers: everything in the clip said to be the
   *  opposite of what it shows. Statements, like any other fix; nothing
   *  "swapped" is kept, which would turn over whenever the clip is heard
   *  differently. */
  swap: (edits: readonly SpeakerTurn[]) => SpeakerTurn[]
}

export function fixSpeakers(clip: SpeakerClip): SpeakerFixing {
  const { words, heard } = clip
  const own = clip.lines ?? []
  // Lines made here from the word list hold only the words in it. Where the
  // clip has captions the list has no words for, its own lines are sent
  // instead (as they are without the second speaker's colour), so none goes
  // missing; the render then treats them as it treats saved ones.
  const covered = clip.saved || clip.lines === null || wordsMakeLines(words, own, clip.storedPerCaption)
  const whole = clip.saved || (clip.sending && !covered)
  const lineOf = whole ? linesOfWords(words, own) : []
  const leans = leansOn(words)

  const given = (turns: readonly SpeakerTurn[]): CaptionLine[] | null =>
    whole ? clip.lines : clip.sending && words.length > 0 ? groupWords(words, clip.perCaption, turns) : null

  const speakers = (edits: readonly SpeakerTurn[]): number[] => {
    const turns = paintTurns(heard, edits)
    const byWord = speakersOf(words, turns).map((who) => (who ? 1 : 0))
    const sent = given(turns)
    if (!sent) return byWord
    const tagged = tagLines(sent, turns, words)
    const held = whole ? lineOf : groupOfWords(words, clip.perCaption, turns)
    // A word in none of the lines sent (said in no time at all, and alone in
    // its group) is burned nowhere: it is shown as the word it leans on.
    return held.map((l, i) => (l >= 0 ? (tagged[l].speaker ? 1 : 0) : whole ? 0 : byWord[i]))
  }

  const stretches = words.map((_, i): [number, number] | null => {
    if (!whole) return leans[i] >= 0 ? ownSpan(words, leans[i]) : null
    return lineOf[i] >= 0 ? [own[lineOf[i]].start, own[lineOf[i]].end] : null
  })
  const stretch = (i: number): [number, number] | null => stretches[i] ?? null

  const click = (edits: readonly SpeakerTurn[], i: number): SpeakerTurn[] => {
    const here = stretch(i)
    if (!here) return [...edits]
    const shown = speakers(edits)[i]
    const last = edits[edits.length - 1]
    const back = edits.slice(0, -1)
    const again = last !== undefined && last[0] === here[0] && last[1] === here[1] && speakers(back)[i] !== shown
    const next: SpeakerTurn[] = again ? back : [...edits, [here[0], here[1], shown ? 0 : 1]]
    // A statement that would not switch the word is not made. That happens
    // where two people talk at once and the render is given the lines: a
    // caption goes by who says most of what is said while it is up.
    return speakers(next)[i] === shown ? [...edits] : next
  }

  const upTo = (edits: readonly SpeakerTurn[], from: number, i: number): SpeakerTurn[] => {
    if (!stretch(i) || !stretch(from)) return click(edits, i)
    // Each word in the range by its own stretch, not one stretch from the
    // first to the last: where two people talk at once that would leave out
    // a word in between and take in one from outside. Stretches that touch
    // are said in one.
    const as = speakers(edits)[from]
    const said: SpeakerTurn[] = []
    for (let k = Math.min(from, i); k <= Math.max(from, i); k++) {
      const here = stretch(k)
      if (!here) continue
      const open = said[said.length - 1]
      if (open && here[0] <= open[1] && here[1] >= open[0]) {
        open[0] = Math.min(open[0], here[0])
        open[1] = Math.max(open[1], here[1])
      } else said.push([here[0], here[1], as])
    }
    // Every word in the range already shows it: nothing to say.
    const before = speakers(edits)
    const next = [...edits, ...said]
    return speakers(next).every((who, k) => who === before[k]) ? [...edits] : next
  }

  const swap = (edits: readonly SpeakerTurn[]): SpeakerTurn[] => {
    const turns = paintTurns(heard, edits)
    const said: SpeakerTurn[] = []
    if (whole) {
      // Caption by caption, each the opposite of what it shows. Two that
      // touch and go the same way are said in one; never across a gap, where
      // a caption going the other way could lie.
      for (const line of tagLines(own, turns, words)) {
        if (!(line.end > line.start)) continue
        const to = line.speaker ? 0 : 1
        const open = said[said.length - 1]
        if (open && open[2] === to && line.start <= open[1]) open[1] = Math.max(open[1], line.end)
        else said.push([line.start, line.end, to])
      }
    } else {
      // Every moment of the clip the other speaker's, but for the stretches
      // that are theirs now. Only the clip: a fix kept from outside it (the
      // clip was trimmed) is left saying what it said.
      const end = words.reduce((latest, w) => Math.max(latest, w.end), clip.duration)
      said.push([0, end, 1])
      for (const [a, b] of turns) {
        if (Math.min(b, end) > Math.max(a, 0)) said.push([Math.max(a, 0), Math.min(b, end), 0])
      }
    }
    return [...edits, ...said]
  }

  return { whole, covered, given, speakers, stretch, click, upTo, swap }
}
