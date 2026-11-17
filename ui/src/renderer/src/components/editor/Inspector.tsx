/** The inspector, on the right: the selected item's settings, or the
 *  project's when nothing is selected.
 *
 *  Every change is one of step 5's operations (set_item_props, trim_item,
 *  move_item, set_canvas, set_project_props), sent when a slider is let go or
 *  a field is left, so one change is one undo step. An AI item (an imported
 *  clip's tracked crop or Gaming layout) shows its framing as "AI framing",
 *  never as crop numbers, with "Reset to AI framing" once it was changed. */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { t } from '../../lib/i18n'
import { formatLength, formatTime, frameAt, isAiItem, programLength } from '../../lib/editorProgram'
import { parseTime } from '../../lib/editorTimeline'
import type { CaptionLane, Fps, Framing, Item, Op, Project, ProjectDoc, Track } from '../../lib/editorTypes'
import { CANVAS_CHOICES } from '../../lib/editorTypes'
import type { CaptionStyle, FilterName } from '../../lib/types'
import { SNAP_ROWS } from '../../lib/captionLayer'
import { blocksOf } from '../../lib/captionLane'
import { captionFontWeight, useBundledFonts } from '../../lib/captionLook'
import FilterPicker from '../FilterPicker'
import CaptionStyleControls, { CAPTION_FONTS, DEFAULT_CAPTION_STYLE } from '../CaptionStyleControls'

type Apply = (ops: Op[], label?: string) => Promise<unknown>

function Row({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="flex justify-between gap-3 text-xs">
      <span className="text-muted">{label}</span>
      <span className="tabular-nums truncate">{value}</span>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }): JSX.Element {
  return (
    <section className="space-y-2 border-t border-raised/60 pt-2" aria-label={title}>
      <h4 className="text-[11px] font-semibold text-muted uppercase tracking-wide">{title}</h4>
      {children}
    </section>
  )
}

/** A slider with its number. The value shown follows the slider while it is
 *  dragged; the change is sent once, when it is let go (or a key that moved
 *  it is let go, or the number is typed). */
function Slider({
  label,
  tip,
  value,
  min,
  max,
  step,
  unit,
  disabled,
  onCommit
}: {
  label: string
  tip: string
  value: number
  min: number
  max: number
  step: number
  unit: string
  disabled?: boolean
  onCommit: (v: number) => void
}): JSX.Element {
  const [shown, setShown] = useState(value)
  const [typed, setTyped] = useState<string | null>(null)
  useEffect(() => setShown(value), [value])
  const commit = (v: number): void => {
    const c = Math.min(max, Math.max(min, v))
    if (Math.abs(c - value) > 1e-9) onCommit(c)
    else setShown(value)
  }
  return (
    <label className="block text-xs" title={tip}>
      <span className="flex justify-between gap-2">
        <span className="text-muted">{label}</span>
        <input
          type="text"
          inputMode="decimal"
          className="w-14 bg-transparent text-right tabular-nums outline-none border-b border-transparent focus:border-accent"
          value={typed ?? String(Math.round(shown * 100) / 100)}
          disabled={disabled}
          aria-label={`${label} (${unit})`}
          title={tip}
          onChange={(e) => setTyped(e.target.value)}
          onBlur={() => {
            if (typed !== null && Number.isFinite(Number(typed)) && typed.trim() !== '') commit(Number(typed))
            setTyped(null)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
            if (e.key === 'Escape') {
              setTyped(null)
              ;(e.target as HTMLInputElement).blur()
            }
          }}
        />
      </span>
      <input
        type="range"
        className="w-full accent-[var(--color-accent)]"
        min={min}
        max={max}
        step={step}
        value={shown}
        disabled={disabled}
        aria-label={label}
        title={tip}
        onChange={(e) => setShown(Number(e.target.value))}
        onPointerUp={(e) => commit(Number((e.target as HTMLInputElement).value))}
        onKeyUp={(e) => commit(Number((e.target as HTMLInputElement).value))}
      />
    </label>
  )
}

/** A time typed as the viewer shows it (m:ss.ff), or seconds. */
function TimeField({
  label,
  tip,
  value,
  fps,
  disabled,
  onCommit
}: {
  label: string
  tip: string
  value: number
  fps: Fps
  disabled?: boolean
  onCommit: (us: number) => void
}): JSX.Element {
  const [typed, setTyped] = useState<string | null>(null)
  const [bad, setBad] = useState(false)
  return (
    <label className="flex items-center justify-between gap-2 text-xs" title={tip}>
      <span className="text-muted">{label}</span>
      <input
        type="text"
        className={`input !py-0.5 !px-1.5 !w-24 text-xs text-right tabular-nums ${bad ? '!border-red-400' : ''}`}
        value={typed ?? formatTime(value, fps)}
        disabled={disabled}
        aria-label={label}
        aria-invalid={bad}
        title={tip}
        onChange={(e) => {
          setTyped(e.target.value)
          setBad(false)
        }}
        onBlur={() => {
          if (typed !== null) {
            const us = parseTime(typed, fps)
            if (us === null) {
              setBad(true)
              return
            }
            // Only a time on another frame is a change: the field shows the
            // frame, so typing back what it shows (or any time inside that
            // frame) never moves the edge (8-D8).
            if (frameAt(us, fps) !== frameAt(value, fps)) onCommit(us)
          }
          setTyped(null)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
          if (e.key === 'Escape') {
            setTyped(null)
            setBad(false)
          }
        }}
      />
    </label>
  )
}

const ADJUSTS: { key: string; label: string; min: number; max: number; neutral: number }[] = [
  { key: 'brightness', label: 'Brightness', min: -50, max: 50, neutral: 0 },
  { key: 'contrast', label: 'Contrast', min: 50, max: 200, neutral: 100 },
  { key: 'saturation', label: 'Saturation', min: 0, max: 300, neutral: 100 },
  { key: 'temperature', label: 'Temperature (cool ↔ warm)', min: -100, max: 100, neutral: 0 },
  { key: 'tint', label: 'Tint (green ↔ magenta)', min: -100, max: 100, neutral: 0 },
  { key: 'sharpen', label: 'Sharpen', min: 0, max: 100, neutral: 0 },
  { key: 'vignette', label: 'Vignette', min: 0, max: 100, neutral: 0 }
]

const ALIGNS: [NonNullable<Framing['align']>, string][] = [
  ['center', 'Centre'],
  ['top', 'Top'],
  ['bottom', 'Bottom'],
  ['left', 'Left'],
  ['right', 'Right']
]

/** The framing the imported clip had (its tracked crop or Gaming layout),
 *  for "Reset to AI framing": null when the item is not on the clip's own
 *  video, or the project wasn't made from a clip. */
export function aiFramingOf(doc: ProjectDoc, item: Item): Framing | null {
  const ctx = (doc.context ?? {}) as { origin?: string; media_id?: string; imported?: { render_opts?: Record<string, unknown> } }
  if (ctx.origin !== 'clip' || item.media_id !== ctx.media_id) return null
  const opts = ctx.imported?.render_opts ?? {}
  const out: Framing = {}
  if ('crop' in opts) out.crop = opts.crop as string
  if ('gaming' in opts) out.gaming = opts.gaming
  return out
}

function ItemSettings({
  project,
  track,
  item,
  apply,
  focusText,
  onTextFocused
}: {
  project: Project
  track: Track
  item: Item
  apply: Apply
  focusText: string | null
  onTextFocused?: () => void
}): JSX.Element {
  const doc = project.document
  const fps = doc.canvas.fps
  const ro = project.read_only
  const m = item.media_id ? doc.media[item.media_id] : undefined
  const isText = track.kind === 'text' || item.text !== null
  const isPicture = m?.kind === 'image'
  const isVideoTrack = track.kind === 'video'
  const isMain = track.id === 'V1'
  // AI framing is the main track's (step 7: compose.is_ai_item is about V1).
  const ai = isMain && isAiItem(doc, item)
  const aiFraming = isMain ? aiFramingOf(doc, item) : null
  const set = (fields: Record<string, unknown>, label: string): void =>
    void apply([{ op: 'set_item_props', item_id: item.id, ...fields }], label)
  const framing = item.framing ?? {}
  const fit = framing.fit === 'fill' ? 'fill' : 'fit'
  const tr = item.transform ?? {}
  const adjust = item.color?.adjust ?? {}
  const name = item.text?.content || m?.name || item.id
  const hasSound = !isText && !isPicture && (track.kind === 'audio' || (m?.has_audio ?? false))

  return (
    <>
      <h3 className="text-xs font-semibold truncate" title={name}>
        {name}
      </h3>
      <Row label={t('Track')} value={track.id === 'V1' ? t('Main track') : track.name || track.id} />
      <Row label={t('Length')} value={formatTime(item.duration_us, fps)} />

      <Section title={t('Timing')}>
        {!isText && !isPicture && item.loop !== true ? (
          <>
            <TimeField
              label={t('Starts in its video')}
              tip={t('Where this part starts in its own video. Type a time as m:ss.ff (minutes, seconds, frames) or in seconds.')}
              value={item.source_in_us}
              fps={m?.fps ?? fps}
              disabled={ro}
              onCommit={(us) => void apply([{ op: 'trim_item', item_id: item.id, source_in_us: us }], t('Trim'))}
            />
            <TimeField
              label={t('Ends in its video')}
              tip={t('Where this part ends in its own video. Type a time as m:ss.ff (minutes, seconds, frames) or in seconds.')}
              value={item.source_out_us}
              fps={m?.fps ?? fps}
              disabled={ro}
              onCommit={(us) => void apply([{ op: 'trim_item', item_id: item.id, source_out_us: us }], t('Trim'))}
            />
          </>
        ) : (
          <TimeField
            label={t('Ends at')}
            tip={t('When it leaves the video. Type a time as m:ss.ff (minutes, seconds, frames) or in seconds.')}
            value={item.start_us + item.duration_us}
            fps={fps}
            disabled={ro || isMain}
            onCommit={(us) => void apply([{ op: 'trim_item', item_id: item.id, end_us: us }], t('Trim'))}
          />
        )}
        {!isMain && (
          <TimeField
            label={t('Starts at')}
            tip={t('When it comes in, in the video. Type a time as m:ss.ff (minutes, seconds, frames) or in seconds.')}
            value={item.start_us}
            fps={fps}
            disabled={ro}
            onCommit={(us) => void apply([{ op: 'move_item', item_id: item.id, start_us: us }], t('Move'))}
          />
        )}
      </Section>

      {isText && item.text && <TextSettings item={item} readOnly={ro} focus={focusText === item.id} onFocused={onTextFocused} apply={apply} />}

      {!isText && (track.kind === 'video' || track.kind === 'audio') && (
        <Section title={t('Speed and sound')}>
          {!isPicture && item.loop !== true && track.kind === 'video' && (
            <Slider
              label={t('Speed')}
              tip={t('How fast this part plays: 1 is normal, 2 twice as fast, 0.5 half speed (0.25 to 4)')}
              value={item.speed}
              min={0.25}
              max={4}
              step={0.05}
              unit="×"
              disabled={ro}
              onCommit={(v) => set({ speed: Math.round(v * 100) / 100 }, t('Speed'))}
            />
          )}
          {hasSound && (
            <Slider
              label={t('Volume')}
              tip={t('How loud this part is: 100 is as recorded, 0 silent, 200 twice as loud')}
              value={Math.round(item.volume * 100)}
              min={0}
              max={200}
              step={1}
              unit="%"
              disabled={ro}
              onCommit={(v) => set({ volume: Math.round(v) / 100 }, t('Volume'))}
            />
          )}
          <div className="grid grid-cols-2 gap-2">
            {(['in_us', 'out_us'] as const).map((k) => (
              <Slider
                key={k}
                label={k === 'in_us' ? t('Fade in') : t('Fade out')}
                tip={
                  k === 'in_us'
                    ? t('Seconds the picture and sound take to come in from black and silence')
                    : t('Seconds the picture and sound take to go to black and silence')
                }
                value={Math.round((item.fades?.[k] ?? 0) / 1e5) / 10}
                min={0}
                max={Math.max(0.1, Math.floor(item.duration_us / 1e5) / 10)}
                step={0.1}
                unit="s"
                disabled={ro}
                onCommit={(v) => set({ fades: { ...item.fades, [k]: Math.round(v * 1e6) } }, k === 'in_us' ? t('Fade in') : t('Fade out'))}
              />
            ))}
          </div>
        </Section>
      )}

      {isVideoTrack && !isText && (
        <Section title={t('Picture')}>
          <label className="flex items-center justify-between gap-2 text-xs" title={t('How the picture fills the canvas')}>
            <span className="text-muted">{t('Framing')}</span>
            <select
              className="input !py-0.5 !w-36 text-xs"
              value={ai ? 'ai' : fit}
              disabled={ro}
              aria-label={t('Framing')}
              title={t('Fit shows the whole picture with a background around it; Fill fills the canvas and cuts off the edges')}
              onChange={(e) => {
                const v = e.target.value
                if (v === 'ai' && aiFraming) set({ framing: aiFraming }, t('Reset to AI framing'))
                else if (v === 'fit') set({ framing: { fit: 'fit', align: framing.align ?? 'center', ...(isMain ? { pad: framing.pad ?? 'blur' } : {}) } }, t('Framing'))
                else if (v === 'fill') set({ framing: { fit: 'fill', align: framing.align ?? 'center' } }, t('Framing'))
              }}
            >
              {(ai || aiFraming) && <option value="ai">{t('AI framing')}</option>}
              <option value="fit">{t('Fit')}</option>
              <option value="fill">{t('Fill')}</option>
            </select>
          </label>
          {ai ? (
            <p className="text-[11px] text-muted" title={t('The AI follows the speaker, as when the clip was made. Pause shows the exact frame.')}>
              {t('Framed by the AI, as when the clip was made.')}
            </p>
          ) : (
            <>
              <label className="flex items-center justify-between gap-2 text-xs" title={t('Which part of the picture stays in view')}>
                <span className="text-muted">{t('Position')}</span>
                <select
                  className="input !py-0.5 !w-36 text-xs"
                  value={framing.align ?? 'center'}
                  disabled={ro}
                  aria-label={t('Position')}
                  title={t('Which part of the picture stays in view')}
                  onChange={(e) => set({ framing: { ...framing, fit, align: e.target.value } }, t('Framing'))}
                >
                  {ALIGNS.map(([v, l]) => (
                    <option key={v} value={v}>
                      {t(l)}
                    </option>
                  ))}
                </select>
              </label>
              {isMain && fit === 'fit' && (
                <label className="flex items-center justify-between gap-2 text-xs" title={t('What fills the canvas around the picture')}>
                  <span className="text-muted">{t('Background')}</span>
                  <span className="flex items-center gap-1.5">
                    <select
                      className="input !py-0.5 !w-24 text-xs"
                      value={framing.pad && framing.pad !== 'blur' ? 'colour' : 'blur'}
                      disabled={ro}
                      aria-label={t('Background')}
                      title={t('What fills the canvas around the picture')}
                      onChange={(e) =>
                        set({ framing: { ...framing, fit: 'fit', pad: e.target.value === 'blur' ? 'blur' : '#000000' } }, t('Framing'))
                      }
                    >
                      <option value="blur">{t('Blurred')}</option>
                      <option value="colour">{t('Colour')}</option>
                    </select>
                    {framing.pad && framing.pad !== 'blur' && (
                      <input
                        type="color"
                        className="w-7 h-6 bg-transparent"
                        value={framing.pad}
                        disabled={ro}
                        aria-label={t('Background colour')}
                        title={t('Background colour')}
                        onChange={(e) => set({ framing: { ...framing, fit: 'fit', pad: e.target.value.toUpperCase() } }, t('Framing'))}
                      />
                    )}
                  </span>
                </label>
              )}
              {aiFraming && (
                <button
                  type="button"
                  className="btn-ghost !py-1 !px-2 text-xs w-full"
                  disabled={ro}
                  onClick={() => set({ framing: aiFraming }, t('Reset to AI framing'))}
                  title={t('Frame this part as the AI did when the clip was made')}
                >
                  {t('Reset to AI framing')}
                </button>
              )}
            </>
          )}
        </Section>
      )}

      {isVideoTrack && !isMain && !isText && (
        <Section title={t('Place')}>
          <Slider
            label={t('Left / right')}
            tip={t('Moves it sideways: 0 is centred, -50 half the canvas to the left, 50 half to the right')}
            value={Math.round((tr.x ?? 0) * 100)}
            min={-100}
            max={100}
            step={1}
            unit="%"
            disabled={ro}
            onCommit={(v) => set({ transform: { ...tr, x: v / 100 } }, t('Place'))}
          />
          <Slider
            label={t('Up / down')}
            tip={t('Moves it up or down: 0 is centred, -50 half the canvas up, 50 half down')}
            value={Math.round((tr.y ?? 0) * 100)}
            min={-100}
            max={100}
            step={1}
            unit="%"
            disabled={ro}
            onCommit={(v) => set({ transform: { ...tr, y: v / 100 } }, t('Place'))}
          />
          <Slider
            label={t('Size')}
            tip={t('How big it is: 100 fills the canvas as framed')}
            value={Math.round((tr.scale ?? 1) * 100)}
            min={5}
            max={400}
            step={1}
            unit="%"
            disabled={ro}
            onCommit={(v) => set({ transform: { ...tr, scale: v / 100 } }, t('Size'))}
          />
          <Slider
            label={t('Rotation')}
            tip={t('Turns it clockwise, in degrees')}
            value={Math.round(tr.rotation ?? 0)}
            min={-180}
            max={180}
            step={1}
            unit="°"
            disabled={ro}
            onCommit={(v) => set({ transform: { ...tr, rotation: v } }, t('Rotation'))}
          />
          <Slider
            label={t('Opacity')}
            tip={t('How solid it is: 100 solid, 0 invisible')}
            value={Math.round((tr.opacity ?? 1) * 100)}
            min={0}
            max={100}
            step={1}
            unit="%"
            disabled={ro}
            onCommit={(v) => set({ transform: { ...tr, opacity: v / 100 } }, t('Opacity'))}
          />
        </Section>
      )}

      {isVideoTrack && !isText && (
        <Section title={t('Colour')}>
          <div className="[&_.grid]:!grid-cols-3 [&_.grid]:!gap-1 [&_.h-10]:!h-6 [&_p]:!px-1 [&_p]:!text-[10px] [&_p]:truncate">
            <FilterPicker
              value={((item.color?.filter as FilterName | undefined) ?? 'none') as FilterName}
              onChange={(f) => !ro && set({ color: { ...item.color, filter: f } }, t('Color filter'))}
            />
          </div>
          {ADJUSTS.map((a) => (
            <Slider
              key={a.key}
              label={t(a.label)}
              tip={t(a.label)}
              value={Math.round((adjust[a.key] ?? a.neutral / 100) * 100)}
              min={a.min}
              max={a.max}
              step={1}
              unit="%"
              disabled={ro}
              onCommit={(v) => set({ color: { ...item.color, adjust: { ...adjust, [a.key]: v / 100 } } }, t(a.label))}
            />
          ))}
          <button
            type="button"
            className="btn-ghost !py-1 !px-2 text-xs w-full"
            disabled={ro || (!item.color?.filter && Object.keys(adjust).length === 0)}
            onClick={() => set({ color: {} }, t('Colour'))}
            title={t('Take off the filter and every colour change of this part')}
          >
            {t('Reset colour')}
          </button>
        </Section>
      )}
    </>
  )
}

const FPS_CHOICES: [string, Fps][] = [
  ['24', { num: 24, den: 1 }],
  ['25', { num: 25, den: 1 }],
  ['29.97', { num: 30000, den: 1001 }],
  ['30', { num: 30, den: 1 }],
  ['50', { num: 50, den: 1 }],
  ['59.94', { num: 60000, den: 1001 }],
  ['60', { num: 60, den: 1 }]
]

function ProjectSettings({ project, apply }: { project: Project; apply: Apply }): JSX.Element {
  const doc = project.document
  const fps = doc.canvas.fps
  const ro = project.read_only
  const [name, setName] = useState<string | null>(null)
  const shape =
    CANVAS_CHOICES.find((c) => {
      const [w, h] = c.split(':').map(Number)
      return Math.abs(doc.canvas.width / doc.canvas.height - w / h) < 0.01
    }) ?? ''
  const fpsKey = FPS_CHOICES.find(([, f]) => f.num * fps.den === fps.num * f.den)?.[0] ?? ''
  return (
    <>
      <h3 className="text-xs font-semibold text-muted">{t('Project')}</h3>
      <label className="block text-xs" title={t('The name of this project in the Projects list')}>
        <span className="text-muted">{t('Name')}</span>
        <input
          type="text"
          className="input !py-1 text-xs mt-1"
          maxLength={120}
          value={name ?? doc.name}
          disabled={ro}
          aria-label={t('Name')}
          title={t('The name of this project in the Projects list')}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => {
            if (name !== null && name.trim() && name !== doc.name) void apply([{ op: 'set_project_props', name: name.trim() }], t('Name'))
            setName(null)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
            if (e.key === 'Escape') setName(null)
          }}
        />
      </label>
      <label className="flex items-center justify-between gap-2 text-xs" title={t('The shape of the video: 9:16 for TikTok, Reels and Shorts, 16:9 for YouTube')}>
        <span className="text-muted">{t('Canvas')}</span>
        <select
          className="input !py-0.5 !w-32 text-xs"
          value={shape}
          disabled={ro}
          aria-label={t('Canvas')}
          title={t('The shape of the video: 9:16 for TikTok, Reels and Shorts, 16:9 for YouTube')}
          onChange={(e) => void apply([{ op: 'set_canvas', preset: e.target.value }], t('Canvas'))}
        >
          {!shape && <option value="">{`${doc.canvas.width}×${doc.canvas.height}`}</option>}
          {CANVAS_CHOICES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
      <Row label={t('Size')} value={`${doc.canvas.width}×${doc.canvas.height}`} />
      <label className="flex items-center justify-between gap-2 text-xs" title={t('Frames a second of the finished video')}>
        <span className="text-muted">{t('Frame rate')}</span>
        <select
          className="input !py-0.5 !w-32 text-xs"
          value={fpsKey}
          disabled={ro}
          aria-label={t('Frame rate')}
          title={t('Frames a second of the finished video')}
          onChange={(e) => {
            const f = FPS_CHOICES.find(([k]) => k === e.target.value)?.[1]
            if (f) void apply([{ op: 'set_canvas', fps: f }], t('Frame rate'))
          }}
        >
          {!fpsKey && <option value="">{Math.round((fps.num / fps.den) * 100) / 100}</option>}
          {FPS_CHOICES.map(([k]) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </label>
      <Row label={t('Length')} value={formatLength(programLength(doc))} />
    </>
  )
}

// ---- step 9: captions and text ----------------------------------------------------------------

/** Where a caption placed by name sits, as % of the frame (lib/captionLayer.ts SNAP_ROWS). */
function namedPlace(position: unknown): [number, number] {
  const row = SNAP_ROWS.find((r) => r.id === position) ?? SNAP_ROWS.find((r) => r.id === 'bottom')
  return [50, row ? row.y : 75]
}

/** Position x and y as numbers (% of the frame), and "Reset position".
 *  Without a dragged place the fields show where it stands, greyed. */
function PositionFields({
  at,
  named,
  disabled,
  onCommit,
  onReset
}: {
  at: { x: number; y: number } | null
  named: [number, number]
  disabled: boolean
  onCommit: (at: { x: number; y: number }) => void
  onReset: () => void
}): JSX.Element {
  const [typed, setTyped] = useState<{ x?: string; y?: string }>({})
  const shown = (k: 'x' | 'y'): string => typed[k] ?? (at ? String(Math.round(at[k] * 100) / 100) : '')
  const commit = (): void => {
    const num = (k: 'x' | 'y', fallback: number): number => {
      const v = typed[k]
      if (v === undefined || v.trim() === '' || !Number.isFinite(Number(v))) return fallback
      return Math.min(100, Math.max(0, Math.round(Number(v) * 100) / 100))
    }
    const base = at ?? { x: named[0], y: named[1] }
    const next = { x: num('x', base.x), y: num('y', base.y) }
    setTyped({})
    if (!at || next.x !== at.x || next.y !== at.y) {
      if (typed.x !== undefined || typed.y !== undefined) onCommit(next)
    }
  }
  const field = (k: 'x' | 'y', label: string, tip: string): JSX.Element => (
    <label className="flex items-center justify-between gap-2 text-xs" title={tip}>
      <span className="text-muted">{label}</span>
      <span className="flex items-center gap-1">
        <input
          type="text"
          inputMode="decimal"
          className="input !py-0.5 !px-1.5 !w-16 text-xs text-right tabular-nums"
          value={shown(k)}
          placeholder={String(k === 'x' ? named[0] : named[1])}
          disabled={disabled}
          aria-label={`${label} (%)`}
          title={tip}
          onChange={(e) => setTyped((cur) => ({ ...cur, [k]: e.target.value }))}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
            if (e.key === 'Escape') setTyped({})
          }}
        />
        <span className="text-muted">%</span>
      </span>
    </label>
  )
  return (
    <div className="space-y-1.5">
      {field('x', t('Across'), t('Where its middle is, from the left edge (0) to the right edge (100). Drag it on the video to place it by hand.'))}
      {field('y', t('Down'), t('Where its middle is, from the top (0) to the bottom (100). Drag it on the video to place it by hand.'))}
      <button
        type="button"
        className="btn-ghost !py-1 !px-2 text-xs w-full"
        disabled={disabled || !at}
        onClick={onReset}
        title={t('Put it back in its place by name (top, middle or bottom)')}
      >
        {t('Reset position')}
      </button>
    </div>
  )
}

function placeOf(value: unknown): { x: number; y: number } | null {
  const v = value as { x?: unknown; y?: unknown } | null | undefined
  return v && typeof v === 'object' && typeof v.x === 'number' && typeof v.y === 'number' ? { x: v.x, y: v.y } : null
}

/** The caption layer's settings (the caption layer or a caption line is
 *  selected): the step-3 caption controls as they are, and the position.
 *  Every change sends the whole style (set_captions), keeping a dragged
 *  place unless "Reset position" (or a Position by name) takes it off. A
 *  burst of changes (a slider, a colour being picked) is sent once, when it
 *  rests. */
function CaptionSettings({
  project,
  lane,
  line,
  onRetime,
  apply
}: {
  project: Project
  lane: CaptionLane | null
  line: string | null
  onRetime?: (start: number, end: number) => void
  apply: Apply
}): JSX.Element {
  const doc = project.document
  const ro = project.read_only
  const own = doc.captions?.style
  const base: Record<string, unknown> = own && typeof own === 'object' ? (own as Record<string, unknown>) : (lane?.style ?? {})
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null)
  const timer = useRef<number | undefined>()
  const current: Record<string, unknown> = { ...base, ...(draft ?? {}) }
  const style = { ...DEFAULT_CAPTION_STYLE, ...current } as Required<CaptionStyle>
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const send = (next: Record<string, unknown>, label: string, now = false): void => {
    setDraft(next)
    window.clearTimeout(timer.current)
    const go = (): void => {
      const clean = Object.fromEntries(Object.entries(next).filter(([, v]) => v !== undefined))
      void apply([{ op: 'set_captions', style: clean }], label).then(() => setDraft(null))
    }
    if (now) go()
    else timer.current = window.setTimeout(go, 350)
  }
  const change = <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]): void => {
    if (ro) return
    // A position by name takes off a dragged place (else it would do nothing).
    const patch: Record<string, unknown> = key === 'position' ? { position: value, x: undefined, y: undefined } : { [key]: value }
    send({ ...current, ...patch }, t('Caption style'))
  }
  const at = placeOf(current)
  // The selected caption line's times in the video, as on the lane. A line
  // an edit cuts in parts is retimed on the main track only (as on the lane).
  const fps = doc.canvas.fps
  const pieces = line && lane && lane.revision === project.revision ? blocksOf(lane.lines).filter((b) => b.id === line) : []
  const block = pieces[0]
  const whole = pieces.length === 1 && block.pieces === 1
  const partsTip = t('An edit cuts this caption in parts: its words can be changed, its times only on the main track.')
  return (
    <>
      <h3 className="text-xs font-semibold">{t('Captions')}</h3>
      {block && (
        <Section title={t('Timing')}>
          <TimeField
            label={t('Starts at')}
            tip={whole ? t('When this caption comes in, in the video. Type a time as m:ss.ff (minutes, seconds, frames) or in seconds.') : partsTip}
            value={block.start}
            fps={fps}
            disabled={ro || !whole || !onRetime}
            onCommit={(us) => onRetime?.(us, block.end)}
          />
          <TimeField
            label={t('Ends at')}
            tip={whole ? t('When this caption leaves the video. Type a time as m:ss.ff (minutes, seconds, frames) or in seconds.') : partsTip}
            value={pieces[pieces.length - 1].end}
            fps={fps}
            disabled={ro || !whole || !onRetime}
            onCommit={(us) => onRetime?.(block.start, us)}
          />
        </Section>
      )}
      <div className="space-y-3 text-xs [&_.input]:!text-xs [&_.input]:!py-1">
        <CaptionStyleControls
          idPrefix="new-editor-captions"
          style={style}
          onChange={change}
          onPick={(next) => !ro && send({ ...current, ...next, x: current.x, y: current.y }, t('Caption style'), true)}
          hideWordsPerCaption={lane?.saved === true}
        />
      </div>
      <Section title={t('Position')}>
        <PositionFields
          at={at}
          named={namedPlace(style.position)}
          disabled={ro}
          onCommit={(p) => send({ ...current, x: p.x, y: p.y }, t('Move captions'), true)}
          onReset={() => send({ ...current, x: undefined, y: undefined }, t('Reset position'), true)}
        />
      </Section>
    </>
  )
}

/** A text item's settings: its words, its kind (text or hook), font, size,
 *  colour and position. */
function TextSettings({
  item,
  readOnly,
  focus,
  onFocused,
  apply
}: {
  item: Item
  readOnly: boolean
  focus: boolean
  onFocused?: () => void
  apply: Apply
}): JSX.Element {
  const text = item.text as NonNullable<Item['text']> & { font?: string; size?: number; color?: string; role?: string; position?: unknown }
  const bundled = useBundledFonts()
  const [content, setContent] = useState<string | null>(null)
  const area = useRef<HTMLTextAreaElement>(null)
  const colourTimer = useRef<number | undefined>()
  useEffect(() => () => window.clearTimeout(colourTimer.current), [])
  // Just added with "+ Text": its words are ready to type over.
  useEffect(() => {
    if (!focus) return
    area.current?.focus()
    area.current?.select()
    // Once only: the text selected again later keeps the focus where it is.
    onFocused?.()
  }, [focus])
  const set = (fields: Record<string, unknown>, label: string): void =>
    void apply([{ op: 'set_text', item_id: item.id, ...fields }], label)
  const at = placeOf(text.position)
  const value = content ?? text.content
  return (
    <Section title={t('Text')}>
      <label className="block text-xs" title={t('The words on the video. Enter keeps them, Shift+Enter starts a new line.')}>
        <span className="text-muted">{t('Words')}</span>
        <textarea
          ref={area}
          className="input !py-1 text-xs mt-1 resize-none"
          rows={Math.min(4, Math.max(2, value.split('\n').length))}
          maxLength={500}
          value={value}
          disabled={readOnly}
          aria-label={t('Words')}
          onChange={(e) => setContent(e.target.value)}
          onBlur={() => {
            const next = (content ?? '').replace(/\r/g, '').trim()
            if (content !== null && next && next !== text.content) set({ content: next }, t('Edit text'))
            setContent(null)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              ;(e.target as HTMLTextAreaElement).blur()
            }
            if (e.key === 'Escape') {
              setContent(null)
              window.setTimeout(() => (e.target as HTMLTextAreaElement).blur(), 0)
            }
          }}
        />
      </label>
      <label className="flex items-center justify-between gap-2 text-xs" title={t('Hook: the line at the start that makes people keep watching. Text: any other words on the video.')}>
        <span className="text-muted">{t('Kind')}</span>
        <select
          className="input !py-0.5 !w-36 text-xs"
          value={text.role === 'hook' ? 'hook' : 'text'}
          disabled={readOnly}
          aria-label={t('Kind')}
          title={t('Hook: the line at the start that makes people keep watching. Text: any other words on the video.')}
          onChange={(e) => set({ role: e.target.value }, t('Kind'))}
        >
          <option value="text">{t('Text')}</option>
          <option value="hook">{t('Hook')}</option>
        </select>
      </label>
      <label className="flex items-center justify-between gap-2 text-xs" title={t("The caption fonts. Default is Arial Black, the font of your clips' hooks.")}>
        <span className="text-muted">{t('Font')}</span>
        <select
          className="input !py-0.5 !w-36 text-xs"
          value={text.font ?? ''}
          disabled={readOnly}
          aria-label={t('Font')}
          title={t("The caption fonts. Default is Arial Black, the font of your clips' hooks.")}
          style={text.font ? { fontFamily: `'${text.font}', sans-serif`, fontWeight: captionFontWeight(text.font, bundled) } : undefined}
          onChange={(e) => set({ font: e.target.value || null }, t('Font'))}
        >
          <option value="">{t('Default')}</option>
          <optgroup label={t('Windows fonts')}>
            {CAPTION_FONTS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </optgroup>
          {bundled.length > 0 && (
            <optgroup label={t('Fonts that come with Clips Kitty')}>
              {bundled.map((f) => (
                <option key={f.family} value={f.family}>
                  {f.family}
                </option>
              ))}
            </optgroup>
          )}
        </select>
      </label>
      <Slider
        label={t('Size')}
        tip={t('How big the letters are. Drag a corner of the text on the video to change it there.')}
        value={typeof text.size === 'number' ? text.size : 96}
        min={8}
        max={400}
        step={1}
        unit="px"
        disabled={readOnly}
        onCommit={(v) => set({ size: Math.round(v) }, t('Resize text'))}
      />
      <label className="flex items-center justify-between gap-2 text-xs" title={t('Text colour')}>
        <span className="text-muted">{t('Text colour')}</span>
        <input
          type="color"
          className="w-9 h-6 bg-transparent"
          defaultValue={text.color ?? '#FFFFFF'}
          key={text.color ?? '#FFFFFF'}
          disabled={readOnly}
          aria-label={t('Text colour')}
          title={t('Text colour')}
          onChange={(e) => {
            const c = e.target.value.toUpperCase()
            window.clearTimeout(colourTimer.current)
            colourTimer.current = window.setTimeout(() => set({ color: c }, t('Text colour')), 350)
          }}
        />
      </label>
      <h4 className="text-[11px] font-semibold text-muted uppercase tracking-wide pt-1">{t('Position')}</h4>
      <PositionFields
        at={at}
        named={namedPlace(typeof text.position === 'string' ? text.position : 'top')}
        disabled={readOnly}
        onCommit={(p) => set({ position: p }, t('Move text'))}
        onReset={() => set({ position: null }, t('Reset position'))}
      />
    </Section>
  )
}

export default function Inspector({
  project,
  selected,
  apply,
  captionLayer,
  lane,
  focusText,
  onTextFocused,
  captionLine = null,
  onRetimeCaption
}: {
  project: Project
  selected: string[]
  /** Changes go through the project's operations. */
  apply: Apply
  /** Step 9: the caption layer (or a caption line) is selected. */
  captionLayer: boolean
  /** The caption lines and the style as drawn (useCaptionLayer). */
  lane: CaptionLane | null
  /** A text just added with "+ Text": its words are ready to type. */
  focusText: string | null
  /** The Words box took that focus: it is not taken again. */
  onTextFocused?: () => void
  /** The caption line selected on the lane or in the list, and how its
   *  times change (the lane's, so typing a time lands as a drag does). */
  captionLine?: string | null
  onRetimeCaption?: (start: number, end: number) => void
}): JSX.Element {
  const doc = project.document
  const found =
    selected.length === 1
      ? doc.tracks.flatMap((tr) => tr.items.map((it) => ({ tr, it }))).find((x) => x.it.id === selected[0])
      : undefined
  return (
    <aside className="min-h-0 overflow-y-auto bg-surface p-3 space-y-3" aria-label={t('Inspector')}>
      {found ? (
        <ItemSettings key={found.it.id} project={project} track={found.tr} item={found.it} apply={apply} focusText={focusText} onTextFocused={onTextFocused} />
      ) : captionLayer && selected.length === 0 ? (
        <CaptionSettings project={project} lane={lane} line={captionLine} onRetime={onRetimeCaption} apply={apply} />
      ) : selected.length > 1 ? (
        <p className="text-xs text-muted">
          {selected.length} {t('items selected')}
        </p>
      ) : (
        <ProjectSettings project={project} apply={apply} />
      )}
    </aside>
  )
}
