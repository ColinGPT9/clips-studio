/** Suggest edits in the editor (plugins/steps.py suggest_edits,
 *  plugins/edit_marks.py): what a plugin's suggestion says, what Use adds to
 *  the creator's edit, what Take it back takes out, and what Apply says was
 *  used.
 *
 *  A suggestion's times are seconds of the video. The editor's are seconds
 *  of the clip, from its start. They are converted with the clip's current
 *  start when the creator presses Use, so a clip trimmed since still gets
 *  the right parts, and nothing added at either end is ever cut.
 *
 *  Use lays a suggestion over the editor's edit: its cuts are added to the
 *  creator's own, its mutes are appended as they are (never merged, so the
 *  creator's word mutes still toggle), the words inside them are hidden in
 *  the captions as a hand word mute hides them, and single values replace
 *  the current ones. What one Use added is kept as a UseDelta, so Take it
 *  back takes out only that, and only where it is still as the suggestion
 *  left it.
 *
 *  Standalone, with type-only imports, so tests run it under Node
 *  (tests/test_ui_edit_suggestions.py) against plugins/edit_marks.py and
 *  video_editor/timeline.py. Words that are shown go through `tr`; without
 *  it they are English, as the editor is. */

import type {
  AppliedEdit,
  EditData,
  MutedWord,
  PluginEdit,
  SuggestedEdit,
  SuggestionValue,
  UsedSuggestions,
  Word
} from './types'

export type Range = [number, number]
type Words = (s: string) => string
const english: Words = (s) => s

/** The render drops a kept piece shorter than this (video_editor/timeline.py MIN_SEGMENT). */
export const MIN_PIECE = 0.25
/** Use is off when the clip would keep less than this many seconds. */
export const MIN_LEFT = 1
/** Two times this close are the same time, and two values this close the
 *  same value (plugins/edit_marks.py NEAR and SAME). */
const NEAR = 0.01
const SAME = 0.001
/** What one render may record (plugins/edit_marks.py clean_used). */
const MAX_USED = 8
const MAX_REMOVED = 64
const MAX_MUTES = 20
const MAX_MUTED_WORDS = 500
const MAX_WORD = 200
const MAX_HOOK_TEXT = 120
const MAX_CROP = 32
/** The editor fields one Use may set besides spans, in plugins/edit_marks.py's order. */
const VALUES: SuggestionValue[] = ['volume', 'fade_in', 'fade_out', 'speed', 'hook', 'crop']
type NumberValue = 'volume' | 'fade_in' | 'fade_out' | 'speed'
const NUMBERS: Record<NumberValue, [number, number, number]> = {
  // [lowest, highest, default], the render's own limits (video_editor/timeline.py)
  volume: [0, 2, 1],
  fade_in: [0, 3, 0],
  fade_out: [0, 3, 0],
  speed: [0.5, 3, 1]
}
const DEFAULT_LAYOUT = 'track'
/** The Layout buttons a suggestion may choose, in the editor's own names. */
const LAYOUTS: Record<string, string> = { track: 'Auto (AI)', letterbox: 'Letterbox', center: 'Center' }

/** The editor's state a Use or Take it back changes: the edit list and the layout. */
export interface EditorState {
  edit: EditData
  layout: string
}

/** The clip, as the editor has it. */
export interface ClipContext {
  /** The clip's start and end now, in seconds of the video. */
  start: number
  end: number
  /** Its transcript words, in seconds of the clip (api.clipWords). Without
   *  them a summary doesn't count the words a mute hides. */
  words?: Word[]
  /** Why a suggested layout can't be used here: a Gaming / Reaction split,
   *  a Vertical Live, or a 16:9 clip. Absent when the Layout buttons show. */
  noLayout?: 'gaming' | 'vertical_live' | 'landscape'
  /** A Highlights clip: its title card moves lower for a hook title. */
  highlights?: boolean
}

/** What one Use added to the editor's edit, in seconds of the clip: the
 *  spans it took out, the mute spans and words it appended, and each value
 *  it replaced, with what it was before. */
export interface UseDelta {
  removed: Range[]
  mutes: Range[]
  muted_words: MutedWord[]
  values: Partial<Record<SuggestionValue, { before: unknown; after: unknown }>>
}

/** A Use or a Take it back, on the Undo step that made it. Undo of a Use
 *  drops its mark; Undo of a Take it back puts back the mark it dropped
 *  (`delta`), or none for a suggestion used in an earlier Apply. */
export interface UseMark {
  id: string
  kind: 'use' | 'take_back'
  delta: UseDelta | null
}

/** One suggestion as a card above the timeline. */
export interface SuggestionCard {
  id: string
  /** "Suggested by {name} {version}". */
  title: string
  /** What it suggests, one line each, in the editor's own names. */
  parts: string[]
  /** The plugin's reason, in quotes; '' when it gave none. */
  reason: string
  /** What the creator should know before pressing anything. */
  notes: string[]
  /** Where it stands: used, added in this session, or nothing left. */
  status: string
  actions: ('use' | 'hide' | 'take_back' | 'make_again')[]
  /** Why Use is off, when it is. */
  useOff: string
}

export const ADDED =
  'Added to your edit. It goes into the clip when you apply your edits. Undo takes it back.'
export const ALREADY = 'This suggestion is already in your edit.'
export const TAKEN_BACK =
  'Taken out of your edit. Your own changes stay. Apply your edits to make the clip without it.'
export const NOTHING_LEFT = 'Nothing of this suggestion is left in your edit.'
export const USED = 'You used this suggestion.'
export const REMADE = 'This clip was made again without your saved edits, so its file doesn’t have them.'

/** The editor's edit list with nothing changed. */
export function defaultEdit(duration: number): EditData {
  return {
    keep: [[0, duration]],
    mutes: [],
    muted_words: [],
    volume: 1,
    mute_all: false,
    fade_in: 0,
    fade_out: 0,
    speed: 1,
    hook: null,
    music: null
  }
}

// ---- spans: sorted, merged [start, end] pairs ----------------------------------------

const isNumber = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)
const round3 = (x: number): number => Math.round(x * 1000) / 1000

function pairs(raw: unknown): Range[] {
  if (!Array.isArray(raw)) return []
  return raw
    .filter((r): r is Range => Array.isArray(r) && r.length === 2 && isNumber(r[0]) && isNumber(r[1]))
    .map(([a, b]): Range => [a, b])
}

/** Pairs as sorted, merged spans longer than NEAR, as plugins/edit_marks.py spans() and the render clean them. */
export function spans(raw: unknown): Range[] {
  const sorted = pairs(raw)
    .filter(([a, b]) => b - a > NEAR)
    .sort((x, y) => x[0] - y[0] || x[1] - y[1])
  const out: Range[] = []
  for (const [a, b] of sorted) {
    const last = out[out.length - 1]
    if (last && a <= last[1] + NEAR) last[1] = Math.max(last[1], b)
    else out.push([a, b])
  }
  return out
}

/** The parts of spans `a` inside spans `b`. */
export function intersect(a: unknown, b: unknown): Range[] {
  const out: Range[] = []
  for (const [s, e] of spans(a))
    for (const [t, u] of spans(b)) {
      const lo = Math.max(s, t)
      const hi = Math.min(e, u)
      if (hi - lo > NEAR) out.push([lo, hi])
    }
  return spans(out)
}

/** The parts of spans `a` outside spans `b`. */
export function minus(a: unknown, b: unknown): Range[] {
  const out: Range[] = []
  for (const [s, e] of spans(a)) {
    let pieces: Range[] = [[s, e]]
    for (const [t, u] of spans(b)) {
      const rest: Range[] = []
      for (const [p, q] of pieces) {
        if (u <= p || t >= q) {
          rest.push([p, q])
          continue
        }
        if (t > p) rest.push([p, t])
        if (u < q) rest.push([u, q])
      }
      pieces = rest
    }
    out.push(...pieces)
  }
  return spans(out)
}

const total = (list: Range[]): number => list.reduce((sum, [a, b]) => sum + (b - a), 0)
const sameTime = (a: Range, b: Range): boolean => Math.abs(a[0] - b[0]) <= NEAR && Math.abs(a[1] - b[1]) <= NEAR

/** A suggestion's spans (seconds of the video) in seconds of the clip,
 *  inside the clip as it is now. */
function clipSpans(raw: unknown, ctx: ClipContext): Range[] {
  const duration = ctx.end - ctx.start
  const shifted = pairs(raw).map(([a, b]): Range => [
    round3(Math.max(0, Math.min(duration, a - ctx.start))),
    round3(Math.max(0, Math.min(duration, b - ctx.start)))
  ])
  return spans(shifted)
}

/** The words whose middle lies inside one of `mutes` (seconds of the clip):
 *  the ones a suggested mute hides in the captions. */
export function wordsInside(words: Word[] | undefined, mutes: Range[]): Word[] {
  return (words ?? []).filter((w) => {
    const middle = (w.start + w.end) / 2
    return mutes.some(([a, b]) => middle >= a - NEAR && middle <= b + NEAR)
  })
}

// ---- the editor's own word mute ------------------------------------------------------
// Here, not in TimelineEditor.tsx, so tests can check that a Use never stops
// a hand word mute toggling.

/** Whether the creator has muted this transcript word. */
export function wordMuted(edit: Pick<EditData, 'muted_words'>, w: Word): boolean {
  return edit.muted_words.some((m) => Math.abs(m.start - w.start) < 0.03 && m.word === w.word)
}

/** The edit with this word muted, or unmuted when it was: its audio and its
 *  caption together. Unmuting removes the mute span that starts where the
 *  word's own span starts. */
export function toggledWord(edit: EditData, w: Word, duration: number): EditData {
  if (wordMuted(edit, w)) {
    return {
      ...edit,
      muted_words: edit.muted_words.filter((m) => !(Math.abs(m.start - w.start) < 0.03 && m.word === w.word)),
      mutes: edit.mutes.filter((m) => !(Math.abs(m[0] - Math.max(0, w.start - 0.04)) < 0.05))
    }
  }
  const range: Range = [
    Number(Math.max(0, w.start - 0.04).toFixed(2)),
    Number(Math.min(duration, w.end + 0.04).toFixed(2))
  ]
  return {
    ...edit,
    muted_words: [...edit.muted_words, { start: w.start, end: w.end, word: w.word }],
    mutes: [...edit.mutes, range]
  }
}

// ---- what a render of the editor's state does to the clip -----------------------------

function hookOf(raw: unknown): { text: string; seconds: number } | null {
  if (!raw || typeof raw !== 'object') return null
  const h = raw as { text?: unknown; seconds?: unknown }
  const text = typeof h.text === 'string' ? h.text.trim() : ''
  if (!text) return null
  const seconds = isNumber(h.seconds) && h.seconds ? h.seconds : 3
  return { text: text.slice(0, MAX_HOOK_TEXT), seconds: Math.max(1, Math.min(10, seconds)) }
}

function numberOf(field: NumberValue, raw: unknown): number {
  const [lo, hi, fallback] = NUMBERS[field]
  return isNumber(raw) ? Math.max(lo, Math.min(hi, raw)) : fallback
}

/** An edit's volume, fades and speed as the render reads them: one value
 *  that isn't a number sends all four to their defaults. */
function numbersOf(edit: Partial<Record<NumberValue, unknown>>): Record<NumberValue, number> {
  const fields = Object.keys(NUMBERS) as NumberValue[]
  const valid = fields.every((k) => edit[k] === undefined || isNumber(edit[k]))
  const out = {} as Record<NumberValue, number>
  for (const k of fields) out[k] = valid ? numberOf(k, edit[k]) : NUMBERS[k][2]
  return out
}

/** Whether two values of a field are the same, as plugins/edit_marks.py _same compares them. */
function same(field: SuggestionValue, a: unknown, b: unknown): boolean {
  if (field === 'hook') {
    const x = hookOf(a)
    const y = hookOf(b)
    if (x === null || y === null) return x === y
    return x.text === y.text && Math.abs(x.seconds - y.seconds) <= SAME
  }
  if (field === 'crop') return typeof a === 'string' && a === b
  return isNumber(a) && isNumber(b) && Math.abs(a - b) <= SAME
}

/** The kept pieces of an edit, as the render keeps them: inside the clip,
 *  merged, each at least MIN_PIECE long. Null when it keeps nothing, which
 *  the render refuses (it then uses none of the edit). */
function keptPieces(edit: Pick<EditData, 'keep'>, duration: number): Range[] | null {
  const keep = Array.isArray(edit.keep) && edit.keep.length > 0 ? edit.keep : [[0, duration]]
  const pieces = intersect(keep, [[0, duration]]).filter(([a, b]) => b - a >= MIN_PIECE)
  return pieces.length > 0 ? pieces : null
}

/** What a render of this state does to the clip, in seconds of the clip
 *  (plugins/edit_marks.py view): what it keeps and takes out, its mutes and
 *  hidden words as the edit holds them, and each value. */
export function viewOf(
  state: EditorState,
  duration: number
): { kept: Range[]; removed: Range[]; mutes: Range[]; words: MutedWord[]; values: Record<SuggestionValue, unknown> } {
  const layout = typeof state.layout === 'string' && state.layout ? state.layout : DEFAULT_LAYOUT
  const out = {
    kept: [[0, duration]] as Range[],
    removed: [] as Range[],
    mutes: [] as Range[],
    words: [] as MutedWord[],
    values: { volume: 1, fade_in: 0, fade_out: 0, speed: 1, hook: null, crop: layout } as Record<SuggestionValue, unknown>
  }
  const edit = state.edit
  if (!edit) return out
  const kept = keptPieces(edit, duration)
  if (kept === null) return out
  if (!(kept.length === 1 && kept[0][0] < 0.05 && kept[0][1] > duration - 0.05)) {
    out.kept = kept
    out.removed = minus([[0, duration]], kept)
  }
  out.mutes = pairs(edit.mutes)
  out.words = (edit.muted_words ?? []).filter((w) => w && isNumber(w.start) && isNumber(w.end))
  Object.assign(out.values, numbersOf(edit))
  out.values.hook = hookOf(edit.hook)
  return out
}

/** The seconds an edit keeps of the clip, before speed: pieces shorter than
 *  MIN_PIECE don't count, and an edit that keeps nothing keeps 0. */
export function keptSeconds(edit: Pick<EditData, 'keep'>, duration: number): number {
  return total(keptPieces(edit, duration) ?? [])
}

/** How long the rendered clip is (video_editor/timeline.py EditList
 *  final_duration): the kept pieces, at the edit's speed. Null when the
 *  edit keeps nothing, which the render refuses. */
export function finalLength(
  edit: Pick<EditData, 'keep'> & Partial<Pick<EditData, NumberValue>>,
  duration: number
): number | null {
  const kept = keptPieces(edit, duration)
  if (kept === null) return null
  // Keeping the entire clip is not a cut: the render keeps all of it.
  const whole = kept.length === 1 && kept[0][0] < 0.05 && kept[0][1] > duration - 0.05
  return (whole ? duration : total(kept)) / numbersOf(edit).speed
}

function emptyDelta(): UseDelta {
  return { removed: [], mutes: [], muted_words: [], values: {} }
}

/** Whether a delta holds nothing. */
export function isEmpty(delta: UseDelta): boolean {
  return (
    delta.removed.length === 0 &&
    delta.mutes.length === 0 &&
    delta.muted_words.length === 0 &&
    Object.keys(delta.values).length === 0
  )
}

/** The part of what a Use added that the editor's state still holds
 *  (plugins/edit_marks.py _still): spans it took out that are still out,
 *  its mute spans and words still there, and values still the suggestion's. */
export function held(delta: UseDelta, state: EditorState, duration: number): UseDelta {
  const now = viewOf(state, duration)
  const mutes = [...now.mutes]
  const words = [...now.words]
  const out = emptyDelta()
  out.removed = intersect(delta.removed, now.removed)
  for (const m of delta.mutes) {
    const i = mutes.findIndex((have) => sameTime(have, m))
    if (i >= 0) out.mutes.push(mutes.splice(i, 1)[0])
  }
  for (const w of delta.muted_words) {
    const i = words.findIndex((have) => have.word === w.word && sameTime([have.start, have.end], [w.start, w.end]))
    if (i >= 0) out.muted_words.push(words.splice(i, 1)[0])
  }
  for (const field of VALUES) {
    const change = delta.values[field]
    if (change && same(field, now.values[field], change.after)) out.values[field] = change
  }
  return out
}

// ---- Use and Take it back ---------------------------------------------------------------

/** The editor's value for a field, cleaned to what the render's limits allow,
 *  so it can be sent as a Use's `before`. */
function current(field: SuggestionValue, state: EditorState): unknown {
  if (field === 'hook') return hookOf(state.edit.hook)
  if (field === 'crop')
    return typeof state.layout === 'string' && state.layout && state.layout.length <= MAX_CROP
      ? state.layout
      : DEFAULT_LAYOUT
  return numberOf(field, state.edit[field])
}

/** The value a suggestion sets for an editor field, as the editor holds it; null when it sets none. */
function suggested(edit: SuggestedEdit, field: SuggestionValue): unknown {
  if (field === 'hook') return hookOf(edit.title_overlay)
  if (field === 'crop') return typeof edit.crop === 'string' && edit.crop in LAYOUTS ? edit.crop : null
  const value = edit[field]
  // Inside the render's limits, as the engine already keeps it, so an Apply can always record it.
  return isNumber(value) ? numberOf(field, value) : null
}

/** The creator's cuts as kept pieces: the edit's keep, or the whole clip. */
function keepOf(edit: EditData, duration: number): Range[] {
  const keep = pairs(edit.keep)
  return keep.length > 0 ? keep : [[0, duration]]
}

/** The kept pieces less the cuts. A piece the cuts leave shorter than
 *  MIN_PIECE goes, as the render drops it; one they don't touch stays. */
function cutFrom(keep: Range[], cuts: Range[]): Range[] {
  const out: Range[] = []
  for (const piece of keep) {
    const left = minus([piece], cuts)
    if (left.length === 1 && sameTime(left[0], piece)) out.push(piece)
    else out.push(...left.filter(([a, b]) => b - a >= MIN_PIECE).map(([a, b]): Range => [round3(a), round3(b)]))
  }
  return out
}

/** Lay a suggestion over the editor's state, as the creator's Use does:
 *  its cuts are added to the creator's own, its mutes appended unmerged with
 *  the words inside them hidden in the captions, and its single values
 *  replace the current ones. The layout is set only where the Layout
 *  buttons show. `delta` is what it added; `changed` is false when all of
 *  it was already there. */
export function applySuggestion(
  state: EditorState,
  edit: SuggestedEdit,
  ctx: ClipContext
): { state: EditorState; delta: UseDelta; changed: boolean } {
  const duration = ctx.end - ctx.start
  const delta = emptyDelta()
  const next: EditData = { ...state.edit }
  let layout = state.layout

  const before = keepOf(state.edit, duration)
  const after = cutFrom(before, clipSpans(edit.cuts, ctx))
  delta.removed = minus(before, after).map(([a, b]): Range => [round3(a), round3(b)])
  if (delta.removed.length > 0) next.keep = after

  const mutes = clipSpans(edit.mutes, ctx)
  delta.mutes = mutes.filter((m) => !state.edit.mutes.some((have) => sameTime(have, m)))
  next.mutes = [...state.edit.mutes, ...delta.mutes]
  for (const w of wordsInside(ctx.words, mutes)) {
    if (wordMuted(next, w)) continue
    const word = { start: w.start, end: w.end, word: w.word }
    delta.muted_words.push(word)
    next.muted_words = [...next.muted_words, word]
  }

  for (const field of VALUES) {
    const value = suggested(edit, field)
    if (value === null || same(field, current(field, state), value)) continue
    if (field === 'crop') {
      if (ctx.noLayout) continue
      layout = value as string
    } else if (field === 'hook') next.hook = value as EditData['hook']
    else next[field] = value as number
    delta.values[field] = { before: current(field, state), after: value }
  }
  return { state: { edit: next, layout }, delta, changed: !isEmpty(delta) }
}

/** Take out what a Use added, from the editor's state as it is now: the
 *  spans it took out come back, but only between the first and last second
 *  the clip keeps now, so a trim made since stays; its own mute spans and
 *  words go, while the creator's stay; and each value goes back only where
 *  the clip still holds the suggestion's. `changed` is false when nothing
 *  of it was left. */
export function takeBack(
  state: EditorState,
  delta: UseDelta,
  ctx: ClipContext
): { state: EditorState; changed: boolean } {
  const duration = ctx.end - ctx.start
  const next: EditData = { ...state.edit }
  let layout = state.layout
  let changed = false

  const keep = keepOf(state.edit, duration)
  const from = Math.min(...keep.map(([a]) => a))
  const to = Math.max(...keep.map(([, b]) => b))
  const back = minus(intersect(delta.removed, [[from, to]]), keep)
  if (back.length > 0) {
    next.keep = spans([...keep, ...back]).map(([a, b]): Range => [round3(a), round3(b)])
    changed = true
  }

  const mutes = [...state.edit.mutes]
  for (const m of delta.mutes) {
    const i = mutes.findIndex((have) => sameTime(have, m))
    if (i >= 0) {
      mutes.splice(i, 1)
      changed = true
    }
  }
  next.mutes = mutes
  const words = [...state.edit.muted_words]
  for (const w of delta.muted_words) {
    const i = words.findIndex((have) => have.word === w.word && Math.abs(have.start - w.start) <= NEAR)
    if (i >= 0) {
      words.splice(i, 1)
      changed = true
    }
  }
  next.muted_words = words

  for (const field of VALUES) {
    const change = delta.values[field]
    if (!change || !same(field, current(field, state), change.after)) continue
    if (field === 'crop') {
      if (ctx.noLayout) continue
      layout = typeof change.before === 'string' && change.before ? change.before : DEFAULT_LAYOUT
    } else if (field === 'hook') next.hook = hookOf(change.before)
    else next[field] = isNumber(change.before) ? change.before : NUMBERS[field][2]
    changed = true
  }
  return { state: { edit: next, layout }, changed }
}

// ---- between the editor's seconds and the video's ----------------------------------------

/** A Use's delta in seconds of the video, as a render records it (`applied`). */
export function toApplied(delta: UseDelta, start: number): AppliedEdit {
  const out: AppliedEdit = {}
  const shift = (list: Range[]): [number, number][] => list.map(([a, b]) => [round3(a + start), round3(b + start)])
  if (delta.removed.length) out.removed = shift(spans(delta.removed)).slice(0, MAX_REMOVED)
  if (delta.mutes.length) out.mutes = shift(delta.mutes).slice(0, MAX_MUTES)
  if (delta.muted_words.length)
    out.muted_words = delta.muted_words
      .filter((w) => w.word.length <= MAX_WORD)
      .slice(0, MAX_MUTED_WORDS)
      .map((w) => ({ start: round3(w.start + start), end: round3(w.end + start), word: w.word }))
  if (Object.keys(delta.values).length) out.values = { ...delta.values }
  return out
}

/** A stored `applied` (seconds of the video) as a delta in seconds of the
 *  clip, with the clip's current start. */
export function fromApplied(applied: AppliedEdit | undefined, start: number): UseDelta {
  const out = emptyDelta()
  if (!applied || typeof applied !== 'object') return out
  const shift = (list: Range[]): Range[] => list.map(([a, b]): Range => [round3(a - start), round3(b - start)])
  out.removed = shift(spans(applied.removed))
  out.mutes = shift(pairs(applied.mutes))
  out.muted_words = (applied.muted_words ?? [])
    .filter((w) => w && isNumber(w.start) && isNumber(w.end))
    .map((w) => ({ start: round3(w.start - start), end: round3(w.end - start), word: String(w.word ?? '') }))
  const values = applied.values && typeof applied.values === 'object' ? applied.values : {}
  for (const field of VALUES) {
    const change = values[field]
    if (change && typeof change === 'object') out.values[field] = change
  }
  return out
}

/** What Apply sends as `suggestions`: each suggestion used in this session
 *  that the clip still has, with what its Use added, in seconds of the
 *  video. Undefined when there is none. */
export function usedForRender(
  uses: Record<string, UseDelta>,
  entries: PluginEdit[] | undefined,
  start: number
): UsedSuggestions | undefined {
  const ids = new Set(validEntries(entries).map((e) => e.id))
  const used = Object.entries(uses)
    .filter(([id]) => ids.has(id))
    .slice(0, MAX_USED)
    .map(([id, delta]) => ({ id, applied: toApplied(delta, start) }))
  return used.length > 0 ? { used } : undefined
}

// ---- Use marks and the Undo history ------------------------------------------------------

/** How many steps Undo keeps (TimelineEditor.tsx push). Use marks live
 *  outside the history, so a mark outlives its step. */
export const UNDO_STEPS = 30

/** The history with one more step, the oldest dropped past UNDO_STEPS. */
export function pushed<T>(history: T[], step: T): T[] {
  return [...history.slice(-UNDO_STEPS), step]
}

/** The Use marks without these suggestions': after Take it back of a
 *  session Use, or Hide. */
export function marksWithout(uses: Record<string, UseDelta>, ids: string[]): Record<string, UseDelta> {
  if (!ids.some((id) => id in uses)) return uses
  const rest = { ...uses }
  for (const id of ids) delete rest[id]
  return rest
}

/** The Use marks after Undo takes back a step that carried `mark`. */
export function marksAfterUndo(uses: Record<string, UseDelta>, mark: UseMark | undefined): Record<string, UseDelta> {
  if (!mark) return uses
  if (mark.kind === 'use') return marksWithout(uses, [mark.id])
  return mark.delta ? { ...uses, [mark.id]: mark.delta } : uses
}

/** The history Reset keeps: each step's edit, without its Use mark, so Undo
 *  can't bring a mark back. Undo may still bring the edit back; the
 *  suggestion is then not recorded as used, which errs the safe way. */
export function withoutMarks<T extends { suggestion?: UseMark }>(history: T[]): T[] {
  return history.map((step) => {
    if (!step.suggestion) return step
    const copy = { ...step }
    delete copy.suggestion
    return copy
  })
}

// ---- what the creator sees ---------------------------------------------------------------

const secs = (x: number): string => String(Math.round(x * 10) / 10)
const times = (x: number): string => String(Math.round(x * 100) / 100)
const percent = (x: number): string => String(Math.round(x * 100))

/** The entries a clip's scores hold that look like suggestions. */
function validEntries(entries: PluginEdit[] | undefined): PluginEdit[] {
  return (entries ?? []).filter(
    (e) => e && typeof e === 'object' && typeof e.id === 'string' && e.edit && typeof e.edit === 'object'
  )
}

/** What a suggestion does, one line per part, in the editor's own names.
 *  With the clip's words, a mute says how many words it hides in the captions. */
export function summaryParts(edit: SuggestedEdit, ctx: ClipContext, tr: Words = english): string[] {
  const out: string[] = []
  const cuts = clipSpans(edit.cuts, ctx)
  if (cuts.length)
    out.push(
      `${tr('Cuts')} ${cuts.length} ${tr(cuts.length === 1 ? 'part' : 'parts')} · ${secs(total(cuts))} ${tr('s shorter')}`
    )
  const mutes = clipSpans(edit.mutes, ctx)
  if (mutes.length) {
    const muted = `${tr('Mutes')} ${mutes.length} ${tr(mutes.length === 1 ? 'part' : 'parts')}`
    if (!ctx.words) out.push(muted)
    else {
      const hidden = wordsInside(ctx.words, mutes).length
      out.push(
        hidden > 0
          ? `${muted} · ${tr('hides')} ${hidden} ${tr(hidden === 1 ? 'word in the captions' : 'words in the captions')}`
          : `${muted} · ${tr('captions are unchanged')}`
      )
    }
  }
  if (isNumber(edit.fade_in) && edit.fade_in > 0) out.push(`${tr('Fades in')} ${secs(edit.fade_in)} s`)
  if (isNumber(edit.fade_out) && edit.fade_out > 0) out.push(`${tr('Fades out')} ${secs(edit.fade_out)} s`)
  if (isNumber(edit.speed) && Math.abs(edit.speed - 1) > SAME) out.push(`${tr('Speed')} ${times(edit.speed)}×`)
  if (isNumber(edit.volume) && Math.abs(edit.volume - 1) > SAME) out.push(`${tr('Volume')} ${percent(edit.volume)}%`)
  const hook = hookOf(edit.title_overlay)
  if (hook) out.push(`${tr('Hook title')}: “${hook.text}” ${tr('for')} ${secs(hook.seconds)} s`)
  if (typeof edit.crop === 'string' && edit.crop in LAYOUTS) out.push(`${tr('Layout')}: ${tr(LAYOUTS[edit.crop])}`)
  return out
}

/** The editor values a Use would replace, each named before it is pressed:
 *  only where the clip holds a value of its own that differs. */
export function replaceNotes(edit: SuggestedEdit, state: EditorState, ctx: ClipContext, tr: Words = english): string[] {
  const out: string[] = []
  for (const [field, name] of [
    ['fade_in', 'fade in'],
    ['fade_out', 'fade out']
  ] as const) {
    const now = numberOf(field, state.edit[field])
    const value = edit[field]
    if (isNumber(value) && now > SAME && !same(field, now, value))
      out.push(`${tr('Replaces your')} ${tr(name)} (${secs(now)} s → ${secs(value)} s)`)
  }
  const speed = numberOf('speed', state.edit.speed)
  if (isNumber(edit.speed) && Math.abs(speed - 1) > SAME && !same('speed', speed, edit.speed))
    out.push(`${tr('Replaces your')} ${tr('speed')} (${times(speed)}× → ${times(edit.speed)}×)`)
  const volume = numberOf('volume', state.edit.volume)
  if (isNumber(edit.volume) && Math.abs(volume - 1) > SAME && !same('volume', volume, edit.volume))
    out.push(`${tr('Replaces your')} ${tr('volume')} (${percent(volume)}% → ${percent(edit.volume)}%)`)
  const hook = hookOf(edit.title_overlay)
  if (hook && hookOf(state.edit.hook) && !same('hook', state.edit.hook, hook)) out.push(tr('Replaces your hook title'))
  const crop = suggested(edit, 'crop')
  if (typeof crop === 'string' && !ctx.noLayout && state.layout && state.layout !== DEFAULT_LAYOUT && state.layout !== crop)
    out.push(`${tr('Replaces your')} ${tr('layout')} (${tr(LAYOUTS[state.layout] ?? state.layout)} → ${tr(LAYOUTS[crop])})`)
  return out
}

/** Whether a suggestion sets nothing but a layout. */
function onlyLayout(edit: SuggestedEdit): boolean {
  return (
    pairs(edit.cuts).length + pairs(edit.mutes).length === 0 &&
    VALUES.every((field) => field === 'crop' || suggested(edit, field) === null)
  )
}

/** Why a suggested layout isn't used on this clip. */
function noLayoutNote(ctx: ClipContext, tr: Words): string {
  if (ctx.noLayout === 'gaming') return tr('Not used on this clip: layout (a Gaming / Reaction clip keeps its split)')
  if (ctx.noLayout === 'vertical_live')
    return tr('Not used on this clip: layout (Vertical Live keeps the stream’s own 9:16 layout)')
  if (ctx.noLayout === 'landscape') return tr('Not used on this clip: layout (a 16:9 clip has no layout to choose)')
  return ''
}

/** Whether a suggestion was made for a clip with another start or end: its
 *  window is kept to 2 decimals, as the clip's start and end are. */
export function madeForAnotherWindow(entry: Pick<PluginEdit, 'window'>, ctx: ClipContext): boolean {
  const w = entry.window
  if (!Array.isArray(w) || !isNumber(w[0]) || !isNumber(w[1])) return false
  return Math.abs(w[0] - ctx.start) > NEAR + 1e-6 || Math.abs(w[1] - ctx.end) > NEAR + 1e-6
}

/** Before Use: what it would leave of the clip with the creator's own cuts.
 *  Use is off when it would leave almost nothing; under the shortest clip
 *  the video was made with, the card warns and the creator decides. */
export function lengthCheck(
  entry: Pick<PluginEdit, 'edit' | 'min_length'>,
  state: EditorState,
  ctx: ClipContext,
  tr: Words = english
): { useOff: string; note: string } {
  const edit = entry.edit
  const cuts = pairs(edit.cuts).length > 0
  if (!cuts && !isNumber(edit.speed)) return { useOff: '', note: '' }
  const duration = ctx.end - ctx.start
  const trial = applySuggestion(state, edit, ctx).state.edit
  if (cuts && keptSeconds(trial, duration) < MIN_LEFT)
    return { useOff: tr('With your own cuts, these would leave almost nothing of the clip.'), note: '' }
  const length = finalLength(trial, duration)
  const floor = isNumber(entry.min_length) ? entry.min_length : 0
  if (length !== null && floor > 0 && length < floor - SAME)
    return {
      useOff: '',
      note: `${tr('With your own cuts, this leaves')} ${length.toFixed(1)} s, ${tr('shorter than the')} ${secs(floor)} s ${tr('shortest clip this video was made with.')}`
    }
  return { useOff: '', note: '' }
}

/** The cards above the timeline: one per suggestion that isn't hidden, in
 *  the order the plugins ran, and the hidden ones' ids. `uses` are the
 *  suggestions used in this session (in seconds of the clip). */
export function suggestionCards(
  entries: PluginEdit[] | undefined,
  state: EditorState,
  uses: Record<string, UseDelta>,
  ctx: ClipContext,
  tr: Words = english
): { cards: SuggestionCard[]; hidden: string[] } {
  const duration = ctx.end - ctx.start
  const cards: SuggestionCard[] = []
  const hidden: string[] = []
  for (const entry of validEntries(entries)) {
    const session = uses[entry.id]
    if (!session && entry.state !== 'new' && entry.state !== 'used') {
      hidden.push(entry.id)
      continue
    }
    const card: SuggestionCard = {
      id: entry.id,
      title: `${tr('Suggested by')} ${entry.name || entry.plugin}${entry.version ? ` ${entry.version}` : ''}`,
      parts: summaryParts(entry.edit, ctx, tr),
      reason: entry.reason ? `“${entry.reason}”` : '',
      notes: [],
      status: '',
      actions: [],
      useOff: ''
    }
    if (session) {
      const left = !isEmpty(held(session, state, duration))
      card.status = tr(left ? ADDED : NOTHING_LEFT)
      card.actions = [left ? 'take_back' : 'hide']
    } else if (entry.state === 'used') {
      const left = !isEmpty(held(fromApplied(entry.applied, ctx.start), state, duration))
      card.status = tr(left ? USED : NOTHING_LEFT)
      card.actions = [left ? 'take_back' : 'hide']
      if (entry.remade) {
        card.notes.push(tr(REMADE))
        card.actions.push('make_again')
      }
    } else {
      card.notes.push(...replaceNotes(entry.edit, state, ctx, tr))
      const spansSuggested = pairs(entry.edit.cuts).length + pairs(entry.edit.mutes).length > 0
      if (spansSuggested && madeForAnotherWindow(entry, ctx))
        card.notes.push(tr('Made before you changed this clip’s start or end. Check the cuts before you apply.'))
      const length = lengthCheck(entry, state, ctx, tr)
      card.useOff = length.useOff
      if (length.note) card.notes.push(length.note)
      if (suggested(entry.edit, 'crop') !== null && ctx.noLayout) {
        card.notes.push(noLayoutNote(ctx, tr))
        if (onlyLayout(entry.edit)) card.useOff = tr('Nothing in this suggestion can be used on this clip.')
      }
      if (hookOf(entry.edit.title_overlay) && ctx.highlights)
        card.notes.push(tr('The title card moves lower to make room for the hook title.'))
      card.actions = ['use', 'hide']
    }
    cards.push(card)
  }
  return { cards, hidden }
}

/** One plain line about a suggestion, for the clip panel in Clip Studio:
 *  who suggested it, what, why, and what came of it. */
export function suggestionLine(entry: PluginEdit, ctx: ClipContext, tr: Words = english): string {
  const who = `${entry.name || entry.plugin}${entry.version ? ` ${entry.version}` : ''}`
  const what = [...summaryParts(entry.edit, ctx, tr), ...(entry.reason ? [entry.reason] : [])].join(' · ')
  const ending =
    entry.state === 'used'
      ? entry.remade
        ? ` (${tr('used, but the clip was made again without it')})`
        : ` (${tr('used')})`
      : entry.state === 'hidden'
        ? ` (${tr('hidden')})`
        : ''
  return `${tr('Edit suggested by')} ${who}: ${what}${ending}`
}

/** Whether a clip has a suggestion the creator hasn't looked at yet (Clip Studio's chip). */
export function hasNewSuggestion(entries: PluginEdit[] | undefined): boolean {
  return validEntries(entries).some((e) => e.state === 'new')
}
