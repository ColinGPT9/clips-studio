/** The captions as a list, like Premiere Pro's Captions panel (step 9, part
 *  C): every caption line that plays, with where it starts in the video, in
 *  the order it plays. The same lines as the caption lane (lib/captionLane.ts
 *  listLines over the same answer).
 *
 *  - Click a line's time: the playhead goes there (and the line is selected
 *    on the lane).
 *  - Type in a line to fix its words: Enter or leaving the field keeps it,
 *    Esc puts it back. One change, one undo step (edit_caption_words; lines
 *    made from the transcript are saved first in the same change).
 *  - The search box keeps only the lines with those words.
 *  - The line playing now is marked as the video plays. */
import { useEffect, useMemo, useRef, useState } from 'react'
import { t } from '../../lib/i18n'
import { formatTime } from '../../lib/editorProgram'
import type { PlayheadStore } from '../../lib/editorPlayhead'
import type { CaptionLane, Fps, Op, OpsAnswer } from '../../lib/editorTypes'
import { laneRevisionFor, listLines, withSave, wordsEdit, wordsToEdit } from '../../lib/captionLane'

export default function TranscriptPanel({
  lane,
  revision,
  fps,
  playhead,
  selectedLine,
  onSelectLine,
  onSeek,
  apply,
  readOnly,
  locked
}: {
  lane: CaptionLane | null
  revision: number
  fps: Fps
  playhead: PlayheadStore
  selectedLine: string | null
  onSelectLine: (id: string | null) => void
  onSeek: (tUs: number) => void
  apply: (ops: Op[], label?: string, at?: number | null) => Promise<OpsAnswer | null>
  readOnly: boolean
  locked: boolean
}): JSX.Element {
  const [query, setQuery] = useState('')
  const rows = useMemo(() => listLines(lane?.lines ?? [], query), [lane, query])
  const fresh = lane !== null && lane.revision === revision
  const canEdit = fresh && !readOnly && !locked

  // The line playing now: worked out as the playhead moves, drawn again
  // only when it changes.
  const [playing, setPlaying] = useState<string | null>(null)
  const lines = lane?.lines
  useEffect(() => {
    const find = (): void => {
      const now = playhead.get()
      const line = lines?.find((l) => !l.hidden && l.pieces.some((p) => p.start_us <= now && now < p.end_us))
      setPlaying(line ? line.id : null)
    }
    find()
    return playhead.subscribe(find)
  }, [playhead, lines])

  // While playing, the line playing now is kept in view.
  const list = useRef<HTMLUListElement>(null)
  useEffect(() => {
    if (!playing || !playhead.playing()) return
    list.current?.querySelector<HTMLElement>(`[data-line="${CSS.escape(playing)}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [playing, playhead])

  const fix = async (id: string, text: string): Promise<void> => {
    if (!lane) return
    const line = lane.lines.find((l) => l.id === id)
    const words = text.replace(/\s+/g, ' ').trim()
    if (!line || !words || words === wordsToEdit(line, lane.spans).trim()) return
    const ops = withSave(lane.saved, lane.lines, [{ op: 'edit_caption_words', edits: [wordsEdit(line, lane.spans, words)] }])
    // Lines still made from the transcript are saved whole: at the lane's revision (9-D13).
    await apply(ops, t('Edit caption'), laneRevisionFor(ops, lane.revision))
  }

  return (
    <div className="min-h-0 flex-1 flex flex-col" aria-label={t('Captions')}>
      <div className="px-2 py-1.5 border-b border-raised/60">
        <input
          type="search"
          className="input !py-1 text-xs"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t('Search captions')}
          aria-label={t('Search captions')}
          title={t('Shows only the captions with these words')}
        />
      </div>
      {!lane ? (
        <p className="p-3 text-xs text-muted">{t('Opening the project…')}</p>
      ) : rows.length === 0 ? (
        <p className="p-3 text-xs text-muted">{query ? t('No caption has these words.') : t('No captions yet. Add one with "+ Caption" in the timeline.')}</p>
      ) : (
        <ul ref={list} className="min-h-0 flex-1 overflow-y-auto divide-y divide-raised/40" aria-label={t('Caption lines')}>
          {rows.map(({ line, start }) => (
            <li
              key={line.id}
              data-line={line.id}
              className={`flex items-start gap-1.5 px-2 py-1 ${line.id === selectedLine ? 'bg-accent/10' : ''} ${
                line.id === playing ? 'border-l-2 border-accent' : 'border-l-2 border-transparent'
              }`}
            >
              <button
                type="button"
                className="text-[10px] tabular-nums text-muted hover:text-accent pt-1 shrink-0"
                onClick={() => {
                  onSelectLine(line.id)
                  onSeek(start)
                }}
                title={t('Go to this caption')}
              >
                {formatTime(start, fps)}
              </button>
              <LineField
                key={line.id}
                text={wordsToEdit(line, lane.spans)}
                disabled={!canEdit}
                onFocus={() => {
                  onSelectLine(line.id)
                  onSeek(start)
                }}
                onCommit={(text) => void fix(line.id, text)}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** One line's words: typed into, kept on Enter or when left, put back on Esc.
 *  The field keeps the keyboard through all of it: it is keyed by the line
 *  alone (not its words), so a kept fix doesn't rebuild it, and while a
 *  change is being saved it is read-only rather than disabled (a disabled
 *  field drops the focus to the page). */
function LineField({
  text,
  disabled,
  onFocus,
  onCommit
}: {
  text: string
  disabled: boolean
  onFocus: () => void
  onCommit: (text: string) => void
}): JSX.Element {
  const [value, setValue] = useState(text)
  const focused = useRef(false)
  const sent = useRef<string | null>(null)
  // The saved words come back (or another change moved them): shown, unless
  // the person is part-way through typing something else.
  useEffect(() => {
    if (!focused.current || value === sent.current || value === text) {
      setValue(text)
      sent.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text])
  const keep = (): void => {
    if (disabled) return
    if (value.trim() && value !== text && value !== sent.current) {
      sent.current = value
      onCommit(value)
    }
  }
  return (
    <textarea
      className={`flex-1 min-w-0 resize-none bg-transparent text-xs leading-snug rounded px-1 py-0.5 outline-none focus:bg-raised focus:ring-1 focus:ring-accent ${
        disabled ? 'opacity-70' : ''
      }`}
      rows={Math.min(3, Math.max(1, Math.ceil(value.length / 28)))}
      value={value}
      maxLength={500}
      readOnly={disabled}
      aria-disabled={disabled || undefined}
      aria-label={t('Caption words')}
      onFocus={() => {
        focused.current = true
        onFocus()
      }}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => {
        focused.current = false
        if (value.trim() && value !== text) keep()
        else setValue(text)
      }}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Enter') {
          e.preventDefault()
          keep()
        } else if (e.key === 'Escape') {
          e.preventDefault()
          sent.current = null
          setValue(text)
        }
      }}
    />
  )
}
