/** The caption lane and the transcript list of the new editor (step 9, part
 *  C): what they need to show the caption lines in program time and to turn
 *  a person's edit into step 5's operations. Pure maths and text over the
 *  engine's answer to GET /projects/{id}/captions, with no imports, so it can
 *  be tested on its own under Node (tests/test_ui_caption_lane.py).
 *
 *  Lines keep the source's time; the lane shows each line's `pieces` (where
 *  it plays in the program, from the same remap the export burns). A block
 *  dropped on the lane is turned back into source times by the engine
 *  (POST /projects/{id}/captions/source_times), never here, so it never
 *  lands a frame off. What the UI sends for each action is the table in
 *  docs/editor-suite/api/step9-captions.md. */

export interface LanePiece {
  start_us: number
  end_us: number
  text: string
}

export interface LaneLine {
  id: string
  in_us: number
  out_us: number
  text: string
  pieces: LanePiece[]
  hidden: boolean
}

export interface LaneSpan {
  src_in_us: number
  src_out_us: number
  start_us: number
  end_us: number
  speed: number
}

/** A caption line as set_captions takes it (a new line has no id: the
 *  engine gives it one). */
export interface SavedLine {
  id?: string
  in_us: number
  out_us: number
  text: string
}

/** One block on the lane: one piece of a line, in program time. */
export interface Block {
  id: string
  /** Which of the line's pieces (in program order). */
  piece: number
  /** How many pieces the line has: a line cut through by an edit has more
   *  than one, and only a line in one piece is retimed or moved. */
  pieces: number
  start: number
  end: number
  text: string
}

export type LaneOp = { op: string } & Record<string, unknown>

/** The lane's blocks, in program order: every piece of every line that
 *  plays (a line whose words are all cut out has none). */
export function blocksOf(lines: LaneLine[]): Block[] {
  const out: Block[] = []
  for (const line of lines) {
    if (line.hidden) continue
    const pieces = [...line.pieces].sort((a, b) => a.start_us - b.start_us)
    pieces.forEach((p, i) => {
      if (p.end_us > p.start_us)
        out.push({ id: line.id, piece: i, pieces: pieces.length, start: p.start_us, end: p.end_us, text: p.text || line.text })
    })
  }
  return out.sort((a, b) => a.start - b.start || a.end - b.end || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
}

/** The blocks that reach into [from, to): the ones drawn as buttons. */
export function blocksIn(blocks: Block[], from: number, to: number): Block[] {
  return blocks.filter((b) => b.start < to && b.end > from)
}

/** The block under program time t (the selected line's first, if it has one there). */
export function blockAt(blocks: Block[], t: number, prefer: string | null): Block | null {
  const under = blocks.filter((b) => b.start <= t && t < b.end)
  return under.find((b) => b.id === prefer) ?? under[0] ?? null
}

/** The lines as set_captions takes them, every one with its id. */
export function savedLines(lines: LaneLine[]): SavedLine[] {
  return lines.map((l) => ({ id: l.id, in_us: l.in_us, out_us: l.out_us, text: l.text }))
}

/** `ops` as one change. Lines made from the transcript (the lane's answer
 *  says `saved: false`) are saved first, with their ids, in the same call,
 *  so the edit is one undo step and the ids it names exist. */
export function withSave(saved: boolean, lines: LaneLine[], ops: LaneOp[]): LaneOp[] {
  return saved ? ops : [{ op: 'set_captions', lines: savedLines(lines) }, ...ops]
}

/** The revision a change built from the lane must be sent at (9-D13), or
 *  null when it can go at the editor's latest one. A change that sends the
 *  whole caption list (Merge, Split, + Caption, and every change made while
 *  the lines still come from the transcript) was built from the lane at
 *  `laneRevision`: sent at a newer revision it would put back the words of a
 *  fix made just before. Pinned to the lane's revision, such a change is not
 *  made once anything changed the project first. A change to one line (its
 *  words, its times) names the line and goes at the latest revision. */
export function laneRevisionFor(ops: LaneOp[], laneRevision: number): number | null {
  return ops.some((o) => o.op === 'set_captions' && 'lines' in o) ? laneRevision : null
}

/** The stretch of the program a span plays at time t, or null (a gap, a
 *  picture, another video: no captions can sit there). */
export function spanAt(spans: LaneSpan[], t: number): LaneSpan | null {
  return spans.find((s) => s.start_us <= t && t < s.end_us) ?? null
}

/** A line in one piece whose source times reach past the stretch of video
 *  its piece plays in (an edit cut off its start or its end, 9-D6): where it
 *  plays, in source time, and the words that play there (the piece's). A
 *  retime, move, split, merge or a fix of its words saves the line as this,
 *  so the words of the cut-off part never come back and the words shown are
 *  the words that burn. null for a line wholly inside its stretch, a line in
 *  several pieces or none. */
export function trimmedLine(line: LaneLine, spans: LaneSpan[]): SavedLine | null {
  if (line.hidden || line.pieces.length !== 1) return null
  const piece = line.pieces[0]
  const span = spanAt(spans, piece.start_us)
  if (!span) return null
  const inUs = Math.max(line.in_us, span.src_in_us)
  const outUs = Math.min(line.out_us, span.src_out_us)
  if ((inUs === line.in_us && outUs === line.out_us) || outUs <= inUs) return null
  return { id: line.id, in_us: inUs, out_us: outUs, text: piece.text.trim() || line.text }
}

/** The words a person is shown to fix a line: the ones that play (a line an
 *  edit cut off shows only its piece's words; a line an edit cut through,
 *  its pieces' words in the source's order, a stretch played twice counted
 *  once, 9-D14). */
export function wordsToEdit(line: LaneLine, spans: LaneSpan[]): string {
  const trim = trimmedLine(line, spans)
  if (trim) return trim.text
  if (line.hidden || line.pieces.length < 2) return line.text
  const bySource = new Map<number, string>()
  for (const p of line.pieces) {
    const span = spanAt(spans, p.start_us)
    const src = span ? Math.round(span.src_in_us + (p.start_us - span.start_us) * span.speed) : p.start_us
    if (p.text.trim() && !bySource.has(src)) bySource.set(src, p.text.trim())
  }
  const words = [...bySource.entries()].sort((x, y) => x[0] - y[0]).map(([, text]) => text).join(' ')
  return words || line.text
}

/** The edit_caption_words entry for new words of a line: a line an edit cut
 *  off is saved as the part that plays (trimmedLine) with them. */
export function wordsEdit(line: LaneLine, spans: LaneSpan[], text: string): Record<string, unknown> {
  const trim = trimmedLine(line, spans)
  return trim ? { line_id: line.id, in_us: trim.in_us, out_us: trim.out_us, text } : { line_id: line.id, text }
}

/** How far a block may go: from the end of the block before it to the start
 *  of the block after it (other lines'; never over another caption), and
 *  inside the stretch of video it plays in (a block never reaches across a
 *  cut, as the engine holds it). */
export function room(blocks: Block[], spans: LaneSpan[], block: Block): { lo: number; hi: number } {
  let lo = 0
  let hi = Number.MAX_SAFE_INTEGER
  for (const b of blocks) {
    if (b.id === block.id) continue
    if (b.end <= block.start) lo = Math.max(lo, b.end)
    else if (b.start >= block.end) hi = Math.min(hi, b.start)
  }
  const span = spanAt(spans, block.start)
  if (span) {
    lo = Math.max(lo, span.start_us)
    hi = Math.min(hi, span.end_us)
  }
  return { lo, hi }
}

/** Where a dragged block goes, before frames and the engine: `mode` is the
 *  edge dragged (left, right) or the whole block (move); `delta` the pointer's
 *  move in µs; `snap` gives the nearest snapping point within reach of a
 *  time, or null. `min` is the shortest a block may be (a frame). The result
 *  stays in its room (above). */
export function dragBlock(
  block: Block,
  mode: 'left' | 'right' | 'move',
  delta: number,
  limits: { lo: number; hi: number },
  min: number,
  snap: (t: number) => number | null
): { start: number; end: number } {
  const { lo, hi } = limits
  if (mode === 'left') {
    let s = block.start + delta
    s = snap(s) ?? s
    return { start: Math.round(Math.min(Math.max(s, lo), block.end - min)), end: block.end }
  }
  if (mode === 'right') {
    let e = block.end + delta
    e = snap(e) ?? e
    return { start: block.start, end: Math.round(Math.max(Math.min(e, hi), block.start + min)) }
  }
  const len = block.end - block.start
  let s = block.start + delta
  const a = snap(s)
  const b = snap(s + len)
  if (a !== null && (b === null || Math.abs(a - s) <= Math.abs(b - s - len))) s = a
  else if (b !== null) s = b - len
  // Kept between the blocks either side (and inside its stretch), its length kept.
  if (hi - lo >= len) s = Math.min(Math.max(s, lo), hi - len)
  else s = lo
  return { start: Math.round(s), end: Math.round(Math.min(s + len, hi)) }
}

/** What to send for a block dropped at program [start, end): which of the
 *  line's source times change (the engine's source_times answer `got`). A
 *  left edge changes only where the line starts, a right edge only where it
 *  ends. A line an edit cut off (`trimmed`, from trimmedLine) is saved as
 *  the part that plays: its other end held to its stretch and the words
 *  that play, so nothing of the cut-off part comes back (9-D6). */
export function retimeEdit(
  lineId: string,
  mode: 'left' | 'right' | 'move',
  got: { in_us: number; out_us: number },
  trimmed: SavedLine | null = null
): Record<string, unknown> {
  if (trimmed) {
    return {
      line_id: lineId,
      in_us: mode === 'right' ? trimmed.in_us : got.in_us,
      out_us: mode === 'left' ? trimmed.out_us : got.out_us,
      text: trimmed.text
    }
  }
  if (mode === 'left') return { line_id: lineId, in_us: got.in_us }
  if (mode === 'right') return { line_id: lineId, out_us: got.out_us }
  return { line_id: lineId, in_us: got.in_us, out_us: got.out_us }
}

function words(text: string): string[] {
  return text.trim().split(/\s+/).filter(Boolean)
}

/** Where the words of a line split at `fraction` of its time: the split that
 *  puts that fraction of its letters in the first part, with at least one
 *  word in each. The transcript's word times are not in the lane's answer,
 *  so this is a guess by length; the words can be fixed afterwards. */
export function splitWords(text: string, fraction: number): [string, string] | null {
  const w = words(text)
  if (w.length < 2) return null
  const total = w.reduce((n, x) => n + x.length, 0)
  let best = 1
  let bestD = Infinity
  let acc = 0
  for (let k = 1; k < w.length; k++) {
    acc += w[k - 1].length
    const d = Math.abs(acc / total - fraction)
    if (d < bestD) {
      bestD = d
      best = k
    }
  }
  return [w.slice(0, best).join(' '), w.slice(best).join(' ')]
}

/** The lines with one split in two at source time `at` (the first part
 *  keeps the line's id, the second is new). A line an edit cut off is split
 *  as the part that plays (trimmedLine: its times held to its stretch, the
 *  words that play), so neither half reaches into the cut (9-D6). null when
 *  `at` isn't inside the line or the line has one word. */
export function splitLine(lines: LaneLine[], id: string, at: number, spans: LaneSpan[] = []): SavedLine[] | null {
  const found = lines.find((l) => l.id === id)
  if (!found) return null
  const line = trimmedLine(found, spans) ?? found
  if (!(line.in_us < at && at < line.out_us)) return null
  const parts = splitWords(line.text, (at - line.in_us) / (line.out_us - line.in_us))
  if (!parts) return null
  const out: SavedLine[] = []
  for (const l of savedLines(lines)) {
    if (l.id !== id) {
      out.push(l)
      continue
    }
    out.push({ id, in_us: line.in_us, out_us: at, text: parts[0] })
    out.push({ in_us: at, out_us: line.out_us, text: parts[1] })
  }
  return out
}

/** The line that follows `id` on the lane and in the video: the next block
 *  after the line's last one, when that line also comes later in the
 *  source (a line moved before it by a reorder is never merged into it) and
 *  the two blocks play in the same stretch of video, so no cut lies between
 *  them (a merged line never covers a stretch that was cut out, 9-D5). */
export function nextLine(lines: LaneLine[], id: string, spans: LaneSpan[]): LaneLine | null {
  const found = following(lines, id)
  return found && found.sameStretch(spans) ? found.next : null
}

/** Whether the selected line would merge with the next one but a cut lies
 *  between them (Merge's tooltip says why it is greyed). */
export function cutBeforeNext(lines: LaneLine[], id: string, spans: LaneSpan[]): boolean {
  const found = following(lines, id)
  return found !== null && !found.sameStretch(spans)
}

function following(lines: LaneLine[], id: string): { next: LaneLine; sameStretch: (spans: LaneSpan[]) => boolean } | null {
  const blocks = blocksOf(lines)
  const own = blocks.filter((b) => b.id === id)
  const line = lines.find((l) => l.id === id)
  if (own.length === 0 || !line) return null
  const last = own[own.length - 1]
  const after = blocks.find((b) => b.id !== id && b.start >= last.end)
  const next = after ? lines.find((l) => l.id === after.id) : undefined
  if (!after || !next || next.in_us < line.out_us) return null
  return {
    next,
    sameStretch: (spans) => {
      const span = spanAt(spans, last.start)
      return span !== null && span === spanAt(spans, after.start)
    }
  }
}

/** The lines with `id` and the line after it (nextLine) made one: from the
 *  first's start to the second's end, the words of both, the first's id. A
 *  line an edit cut off counts as the part that plays (trimmedLine). */
export function mergeLines(lines: LaneLine[], id: string, spans: LaneSpan[]): SavedLine[] | null {
  const found = lines.find((l) => l.id === id)
  const after = nextLine(lines, id, spans)
  if (!found || !after) return null
  const line = trimmedLine(found, spans) ?? found
  const next = trimmedLine(after, spans) ?? after
  const out: SavedLine[] = []
  for (const l of savedLines(lines)) {
    if (l.id === after.id) continue
    if (l.id === id) out.push({ id, in_us: line.in_us, out_us: next.out_us, text: `${line.text.trim()} ${next.text.trim()}`.trim() })
    else out.push(l)
  }
  return out
}

/** Where a new caption at program time t goes: from t (on its frame) for
 *  `length`, up to the next block or the end of its stretch of video. null
 *  when a caption is already there, or no captioned video plays at t. */
export function newBlockAt(blocks: Block[], spans: LaneSpan[], t: number, length: number, min: number): { start: number; end: number } | null {
  const span = spanAt(spans, t)
  if (!span || blocks.some((b) => b.start <= t && t < b.end)) return null
  let end = Math.min(t + length, span.end_us)
  for (const b of blocks) if (b.start > t) end = Math.min(end, b.start)
  return end - t >= min ? { start: t, end } : null
}

/** A main-track item, as the first caption of a project with no captions'
 *  video needs it. */
export interface MainItem {
  media_id: string | null
  /** The media is a video (pictures carry no captions). */
  video: boolean
  start_us: number
  duration_us: number
  source_in_us: number
  source_out_us: number
  speed: number
}

/** The first caption of a project whose captions name no video yet (a
 *  project made by hand): the main track's video item under t gives the
 *  captions' video, and the new line's source times are worked out as the
 *  engine does (compose.caption_source_range: start rounded up, end down,
 *  held to the item). null when no video plays at t. */
export function firstLineAt(
  items: MainItem[],
  t: number,
  length: number,
  min: number
): { media_id: string; start: number; end: number; in_us: number; out_us: number } | null {
  const it = items.find((i) => i.video && i.media_id && i.start_us <= t && t < i.start_us + i.duration_us)
  if (!it || !it.media_id) return null
  const end = Math.min(t + length, it.start_us + it.duration_us)
  if (end - t < min) return null
  const inUs = it.source_in_us + Math.ceil((t - it.start_us) * it.speed - 1e-6)
  const outUs = Math.min(it.source_in_us + Math.floor((end - it.start_us) * it.speed + 1e-6), it.source_out_us)
  return outUs > inUs ? { media_id: it.media_id, start: t, end, in_us: inUs, out_us: outUs } : null
}

/** The lines with a new one added, in source order. */
export function addLine(lines: LaneLine[], line: SavedLine): SavedLine[] {
  const out = savedLines(lines)
  const at = out.findIndex((l) => l.in_us > line.in_us)
  if (at < 0) out.push(line)
  else out.splice(at, 0, line)
  return out
}

/** The lines the transcript list shows (the lane's: those that play), in
 *  program order, with where each first plays, filtered by `query` (any of
 *  its words, case and accents aside). */
export function listLines(lines: LaneLine[], query: string): { line: LaneLine; start: number }[] {
  const fold = (s: string): string =>
    s
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase()
  const q = fold(query.trim())
  const out: { line: LaneLine; start: number }[] = []
  for (const line of lines) {
    if (line.hidden || line.pieces.length === 0) continue
    if (q && !fold(line.text).includes(q)) continue
    out.push({ line, start: Math.min(...line.pieces.map((p) => p.start_us)) })
  }
  return out.sort((a, b) => a.start - b.start)
}
