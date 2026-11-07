import { cloneElement, useEffect, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { api, errorText } from '../lib/api'
import { t } from '../lib/i18n'
import { clipLength, formatBytes, hasLimit, limitAfterTick, rememberLimit, watchableUnder } from '../lib/exportJobs'
import type {
  ExportAspect,
  ExportCodec,
  ExportCodecInfo,
  ExportEstimate,
  ExportPreset,
  ExportPresetsInfo,
  ExportResolution
} from '../lib/types'

/** Settings → Export: what the Export buttons make (#177, #203).
 *
 *  Original, the default, saves the clip's own file exactly as before. Custom
 *  makes a new file in the size, shape, quality, format, frame rate and sound
 *  chosen here, with a size limit if its box is ticked, in the background on
 *  the editor lane (video/export_presets.py, server/editor_lane.py). Each
 *  choice is saved as it is made and comes back as the engine keeps it:
 *  checked, clamped, and sizes made even. What each choice does is in its
 *  tooltip, on its name and on the control itself. The main choices keep
 *  their places; what a choice opens (Custom size, Custom shape, a new shape's
 *  Fill, Custom quality, the size limit's MB) takes a row of its own under it. */

const SIZES: [ExportResolution, string][] = [
  ['same', 'Same as clip'],
  ['720p', '720p'],
  ['1080p', '1080p'],
  ['1440p', '1440p'],
  ['2160p', '2160p (4K)'],
  ['custom', 'Custom size']
]
const SHAPES: [ExportAspect, string][] = [
  ['same', 'Same as clip'],
  ['9:16', 'Tall (9:16)'],
  ['16:9', 'Wide (16:9)'],
  ['1:1', 'Square (1:1)'],
  ['4:5', '4:5'],
  ['custom', 'Custom shape']
]
const FILLS: [ExportPreset['fill'], string][] = [
  ['crop', 'Crop to fit'],
  ['blur', 'Blurred background'],
  ['bars', 'Colour bars']
]
const QUALITIES: [ExportPreset['quality'], string][] = [
  ['smallest', 'Smallest file'],
  ['balanced', 'Balanced'],
  ['high', 'High'],
  ['custom', 'Custom']
]
const CODEC_NAMES: Record<ExportCodec, string> = { h264: 'H.264', hevc: 'HEVC', av1: 'AV1' }
const RATES = ['same', 24, 25, 30, 50, 60] as const

type NumberKey = 'width' | 'height' | 'aspect_w' | 'aspect_h' | 'crf' | 'bitrate_kbps' | 'max_mb'

const SELECT = 'input !py-1 text-sm'
// Where a choice sits in the card's two columns. The main choices keep their
// column whatever else is open; what a choice opens takes a whole row.
const LEFT = 'col-start-1'
const RIGHT = 'col-start-2'
const ROW = 'col-span-2'

function same(a: ExportPreset, b: ExportPreset): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

/** One choice: its name above its control, and what it does in the tooltip,
 *  on the control too, so keyboard and screen-reader users get it as well. */
function Field({
  label,
  tip,
  place = '',
  children
}: {
  label: string
  tip: string
  place?: string
  children: ReactElement<{ title?: string }>
}): JSX.Element {
  return (
    <label className={`block min-w-0 ${place}`} title={tip}>
      <span className="label">{label}</span>
      <span className="block mt-1">{cloneElement(children, { title: tip })}</span>
    </label>
  )
}

/** A choice made of more than one control (a width and a height). Each of
 *  its controls carries the tooltip as well. */
function Group({
  label,
  tip,
  place = '',
  children
}: {
  label: string
  tip: string
  place?: string
  children: ReactNode
}): JSX.Element {
  return (
    <div className={`min-w-0 ${place}`} title={tip} role="group" aria-label={label}>
      <span className="label">{label}</span>
      <div className="mt-1 flex items-center gap-1.5">{children}</div>
    </div>
  )
}

export default function ExportSettingsCard(): JSX.Element {
  const [info, setInfo] = useState<ExportPresetsInfo | null>(null)
  const [preset, setPreset] = useState<ExportPreset | null>(null)
  const [codecs, setCodecs] = useState<ExportCodecInfo[] | null>(null)
  const [estimate, setEstimate] = useState<ExportEstimate | null>(null)
  const [error, setError] = useState('')
  // A number being typed, until the field is left or Enter is pressed.
  const [typing, setTyping] = useState<Partial<Record<NumberKey, string>>>({})
  // As the engine last saved it, and as the card shows it. Saves go one at a
  // time, newest choice last, so a slow answer can't put an older one back.
  const saved = useRef<ExportPreset | null>(null)
  const wanted = useRef<ExportPreset | null>(null)
  const sending = useRef(false)

  useEffect(() => {
    api
      .exportPresets()
      .then((p) => {
        // The first answer fills the card. React's development build loads it
        // twice, and the second answer can come seconds later: it must not
        // undo a choice made meanwhile.
        if (wanted.current) return
        saved.current = p.default
        wanted.current = p.default
        setInfo(p)
        setPreset(p.default)
      })
      .catch(() => {
        /* the engine isn't answering: the card keeps saying Checking… */
      })
  }, [])

  const custom = preset?.kind === 'custom'
  // Only once Custom is chosen: the engine's first answer test-encodes with
  // each encoder this PC might have.
  useEffect(() => {
    if (!custom || codecs !== null) return
    api
      .exportCodecs()
      .then((c) => setCodecs(c.codecs))
      .catch(() => setCodecs([]))
  }, [custom, codecs])

  const shown = preset ? JSON.stringify(preset) : ''
  useEffect(() => {
    if (!preset) return
    let live = true
    const timer = setTimeout(() => {
      api
        .exportEstimate({ preset })
        .then((e) => live && setEstimate(e))
        .catch(() => {
          /* a choice still being saved; the next one answers */
        })
    }, 250)
    return () => {
      live = false
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown])

  // The limit shown, a saved preset's included, is the last one used: this
  // browser remembers it, so ticking the box again brings it back.
  const shownLimit = preset?.max_mb ?? null
  useEffect(() => {
    rememberLimit(shownLimit)
  }, [shownLimit])

  const flush = async (): Promise<void> => {
    if (sending.current) return
    sending.current = true
    try {
      while (wanted.current && saved.current && !same(wanted.current, saved.current)) {
        const asked = wanted.current
        try {
          const got = await api.saveExportDefault(asked)
          saved.current = got.export
          if (wanted.current === asked) {
            wanted.current = got.export
            setPreset(got.export)
          }
        } catch (e) {
          setError(errorText(e))
          wanted.current = saved.current
          setPreset(saved.current)
          break
        }
      }
    } finally {
      sending.current = false
    }
  }

  const change = (patch: Partial<ExportPreset>): void => {
    if (!wanted.current) return
    const next = { ...wanted.current, ...patch }
    wanted.current = next
    setPreset(next)
    setError('')
    void flush()
  }

  const commit = (key: NumberKey): void => {
    const text = typing[key]
    setTyping((d) => {
      const rest = { ...d }
      delete rest[key]
      return rest
    })
    if (text === undefined || !wanted.current) return
    const trimmed = text.trim()
    const value = Number(trimmed)
    // Not a number, an empty box included (unticking is what removes a size
    // limit): the saved value shows again.
    if (trimmed === '' || !Number.isFinite(value) || wanted.current[key] === value) return
    change({ [key]: value } as Partial<ExportPreset>)
  }

  if (!info || !preset) {
    return (
      <div className="card space-y-3" aria-label={t('Export settings')}>
        <h3 className="font-semibold">{t('Export')}</h3>
        <p className="text-xs text-muted">{t('Checking…')}</p>
      </div>
    )
  }

  const limit = (key: NumberKey): [number, number] | undefined => info.limits[key]
  const allowed = (key: keyof ExportPresetsInfo['choices'], value: string | number): boolean =>
    (info.choices[key] as (string | number)[] | undefined)?.includes(value) ?? true

  const number = (key: NumberKey, label: string, tip: string, className: string): JSX.Element => (
    <input
      type="number"
      className={`input !py-1 text-sm ${className}`}
      aria-label={label}
      title={tip}
      min={limit(key)?.[0]}
      max={limit(key)?.[1]}
      value={typing[key] ?? String(preset[key] ?? '')}
      onChange={(e) => {
        const value = e.target.value
        setTyping((d) => ({ ...d, [key]: value }))
      }}
      onBlur={() => commit(key)}
      // Enter saves it, and the cursor stays in the box.
      onKeyDown={(e) => {
        if (e.key === 'Enter') commit(key)
      }}
    />
  )

  // Formats: only those this PC can make. Until the engine has checked, H.264
  // (the processor always makes it) and the saved one, so Format can be used
  // at once; the rest join the list when the check is done. The saved one
  // stays listed if this PC can't make it any more, so the select shows what
  // is saved.
  const known = codecs ?? []
  const codec = known.find((c) => c.codec === preset.codec)
  const codecList: ExportCodec[] = codecs === null ? ['h264'] : known.map((c) => c.codec)
  if (!codecList.includes(preset.codec)) codecList.push(preset.codec)
  const madeOn = Boolean(codec?.cpu && codec?.gpu) || preset.encoder !== 'auto'
  const pickCodec = (next: ExportCodec): void => {
    if (codecs === null) {
      // Not checked yet: the engine refuses a choice this PC can't make.
      change({ codec: next })
      return
    }
    const can = known.find((c) => c.codec === next)
    const encoder =
      (preset.encoder === 'gpu' && !can?.gpu) || (preset.encoder === 'cpu' && !can?.cpu) ? 'auto' : preset.encoder
    change({ codec: next, encoder })
  }

  const reshaped = preset.resolution === 'custom' || preset.aspect !== 'same'
  const perMinute = estimate?.per_minute
  const sizeLine =
    estimate && perMinute
      ? estimate.kind === 'range' && perMinute.high > perMinute.low
        ? `${t('About')} ${formatBytes(perMinute.bytes)} ${t('a minute')} (${formatBytes(perMinute.low)}–${formatBytes(perMinute.high)})`
        : `${t('About')} ${formatBytes(perMinute.bytes)} ${t('a minute')}`
      : ''
  const sizeTip = [
    t('About how big one minute of a clip comes out with these choices, for a tall 1080x1920 clip at 60 fps. Where the size depends on the footage it is a range: busy footage, like gaming or fast motion, needs more space than someone talking. On an AMD graphics card, or for AV1 on an Intel one, it is one figure instead, for a typical clip.'),
    custom && preset.max_mb !== null ? t('With a size limit, longer clips are made smaller to fit it, as far as the picture allows.') : ''
  ]
    .filter(Boolean)
    .join(' ')
  // How long a clip can be and still look good under the size limit, when
  // that is shorter than the clips Clips Kitty makes (lib/exportJobs.ts).
  const watchable = custom && preset.max_mb !== null ? watchableUnder(estimate) : null

  const exportAsTip = t('Original saves each clip exactly as Clips Kitty made it, straight away. Custom makes a new file with the choices below. It is made in the background, so you can keep working.')
  const ownSizeTip = t('Your own size in pixels, from 720 to 4096. It sets the shape too. Odd numbers are made even.')
  const ownShapeTip = t('Your own shape, as width to height, each a whole number from 1 to 32.')
  const ownQualityTip = t('Quality number: lower is better and bigger (1 to 51). For H.264 made on the processor, 18 looks like the original, 23 is balanced and 28 is small; other formats and graphics cards may need other numbers. Bitrate: how much each second of picture gets, in kbps. 6000 suits a 1080p clip, and the file comes out close to it.')
  const limitTip = t('Tick the box to set a size limit for each exported file, from 5 to 4000 MB. Unticked, there is no limit. With a limit, a clip that may fit, or one Clips Kitty can’t size up beforehand, is made just as it would be without one, so short clips aren’t made bigger to fill it. A clip it’s sure won’t fit is made smaller to fit its length, but never so small that the picture breaks up. Every finished file is checked: one over the limit is made again to fit, as far as the picture allows, and you get the smallest file made.')
  const slowerTip = t('To stay under your limit, each file is checked and made again if it came out over, so an export can take twice as long or more. Smaller files can also look less sharp.')
  // Off unless ticked: no limit (max_mb null). Ticking saves the last limit
  // used, or 100 the first time; unticking saves no limit and remembers the
  // one it had (lib/exportJobs.ts).
  const limited = hasLimit(preset)
  const tickLimit = (on: boolean): void =>
    change({ max_mb: limitAfterTick(on, wanted.current?.max_mb ?? null, limit('max_mb')) })

  return (
    <div className="card space-y-3" aria-label={t('Export settings')}>
      <h3 className="font-semibold">{t('Export')}</h3>

      <label className="flex items-center gap-3 text-sm" title={exportAsTip}>
        <span>{t('Export as')}</span>
        <select
          className="input !w-40 !py-1"
          aria-label={t('Export as')}
          title={exportAsTip}
          value={preset.kind}
          onChange={(e) => change({ kind: e.target.value === 'custom' ? 'custom' : 'original' })}
        >
          <option value="original">{t('Original')}</option>
          <option value="custom">{t('Custom')}</option>
        </select>
      </label>

      {custom && (
        <div className="grid grid-cols-2 gap-x-3 gap-y-2.5">
          <Field
            place={LEFT}
            label={t('Size')}
            tip={t('The short side of the picture: 720p makes a tall clip 720x1280 and a wide one 1280x720. No side goes past 4096, so a long custom shape can come out smaller. Clips Kitty makes clips at 1080p, so 1440p and 2160p (4K) look no sharper than that.')}
          >
            <select
              className={SELECT}
              aria-label={t('Size')}
              value={preset.resolution}
              onChange={(e) => change({ resolution: e.target.value as ExportResolution })}
            >
              {SIZES.filter(([v]) => allowed('resolution', v)).map(([v, label]) => (
                <option key={v} value={v}>
                  {t(label)}
                </option>
              ))}
            </select>
          </Field>
          {/* A custom size sets the shape too, so Shape gives way to it. */}
          {preset.resolution !== 'custom' && (
            <Field
              place={RIGHT}
              label={t('Shape')}
              tip={t('The shape of the picture: your clip’s own, tall (9:16), wide (16:9), square (1:1), 4:5 or your own.')}
            >
              <select
                className={SELECT}
                aria-label={t('Shape')}
                value={preset.aspect}
                onChange={(e) => change({ aspect: e.target.value as ExportAspect })}
              >
                {SHAPES.filter(([v]) => allowed('aspect', v)).map(([v, label]) => (
                  <option key={v} value={v}>
                    {t(label)}
                  </option>
                ))}
              </select>
            </Field>
          )}

          {preset.resolution === 'custom' && (
            <Group place={ROW} label={t('Width × height')} tip={ownSizeTip}>
              {number('width', t('Width'), ownSizeTip, '!w-24 shrink-0')}
              <span className="text-muted">×</span>
              {number('height', t('Height'), ownSizeTip, '!w-24 shrink-0')}
            </Group>
          )}
          {preset.resolution !== 'custom' && preset.aspect === 'custom' && (
            <Group place={ROW} label={t('Custom shape')} tip={ownShapeTip}>
              {number('aspect_w', t('Shape width'), ownShapeTip, '!w-16 shrink-0')}
              <span className="text-muted">:</span>
              {number('aspect_h', t('Shape height'), ownShapeTip, '!w-16 shrink-0')}
            </Group>
          )}
          {reshaped && (
            <div className={`${ROW} grid grid-cols-2 gap-x-3`}>
              <Field
                label={t('Fill')}
                tip={t('How the picture fills a new shape: crop it to fit, put a blurred copy of the clip behind it, or add bars of one colour.')}
              >
                <select
                  className={SELECT}
                  aria-label={t('Fill')}
                  value={preset.fill}
                  onChange={(e) => change({ fill: e.target.value as ExportPreset['fill'] })}
                >
                  {FILLS.filter(([v]) => allowed('fill', v)).map(([v, label]) => (
                    <option key={v} value={v}>
                      {t(label)}
                    </option>
                  ))}
                </select>
              </Field>
              {preset.fill === 'bars' && (
                <Field label={t('Bar colour')} tip={t('The colour of the bars.')}>
                  <input
                    type="color"
                    className="h-8 w-16 bg-raised rounded-lg cursor-pointer border border-raised"
                    aria-label={t('Bar colour')}
                    value={preset.bar_color.toLowerCase()}
                    onChange={(e) => change({ bar_color: e.target.value })}
                  />
                </Field>
              )}
            </div>
          )}

          <Field
            place={LEFT}
            label={t('Quality')}
            tip={t('Smallest file: small files that still look fine on a phone. Balanced: a good picture at a sensible size. High: the most detail and the biggest files. Custom: set the quality number or the bitrate yourself.')}
          >
            <select
              className={SELECT}
              aria-label={t('Quality')}
              value={preset.quality}
              onChange={(e) => change({ quality: e.target.value as ExportPreset['quality'] })}
            >
              {QUALITIES.filter(([v]) => allowed('quality', v)).map(([v, label]) => (
                <option key={v} value={v}>
                  {t(label)}
                </option>
              ))}
            </select>
          </Field>
          <Field
            place={RIGHT}
            label={t('Format')}
            tip={
              codecs === null
                ? t('Checking what this PC can make…')
                : t('H.264 plays everywhere. HEVC makes smaller files at the same quality, but some older devices and sites can’t play it. AV1 is offered only when your graphics card can make it.')
            }
          >
            <select
              className={SELECT}
              aria-label={t('Format')}
              value={preset.codec}
              onChange={(e) => pickCodec(e.target.value as ExportCodec)}
            >
              {codecList.map((c) => (
                <option key={c} value={c}>
                  {codec || c !== preset.codec || codecs === null
                    ? CODEC_NAMES[c]
                    : `${CODEC_NAMES[c]} (${t('not on this PC')})`}
                </option>
              ))}
            </select>
          </Field>
          {preset.quality === 'custom' && (
            <Group place={ROW} label={t('Custom quality')} tip={ownQualityTip}>
              <select
                className={`${SELECT} !w-auto shrink-0`}
                aria-label={t('Set the quality by')}
                title={ownQualityTip}
                value={preset.rate}
                onChange={(e) => change({ rate: e.target.value === 'bitrate' ? 'bitrate' : 'crf' })}
              >
                <option value="crf">{t('Quality number')}</option>
                <option value="bitrate">{t('Bitrate')}</option>
              </select>
              {preset.rate === 'crf' ? (
                number('crf', t('Quality number'), ownQualityTip, '!w-16 shrink-0')
              ) : (
                <>
                  {number('bitrate_kbps', t('Bitrate in kbps'), ownQualityTip, '!w-24 shrink-0')}
                  <span className="text-xs text-muted">{t('kbps')}</span>
                </>
              )}
            </Group>
          )}
          {madeOn && (
            <Field
              place={RIGHT}
              label={t('Made on')}
              tip={t('Automatic usually uses the graphics card, which is faster. If the graphics card fails while making an H.264 or HEVC export, the processor makes it instead.')}
            >
              <select
                className={SELECT}
                aria-label={t('Made on')}
                value={preset.encoder}
                onChange={(e) => change({ encoder: e.target.value as ExportPreset['encoder'] })}
              >
                <option value="auto">{t('Automatic')}</option>
                {(codec?.cpu || preset.encoder === 'cpu') && <option value="cpu">{t('Processor')}</option>}
                {(codec?.gpu || preset.encoder === 'gpu') && <option value="gpu">{t('Graphics card')}</option>}
              </select>
            </Field>
          )}

          <Field
            place={LEFT}
            label={t('Frame rate')}
            tip={t('Frames a second. It never goes above the clip’s own: a 30 fps clip stays at 30.')}
          >
            <select
              className={SELECT}
              aria-label={t('Frame rate')}
              value={String(preset.fps)}
              onChange={(e) => {
                const v = e.target.value
                change({ fps: v === 'same' ? 'same' : (Number(v) as ExportPreset['fps']) })
              }}
            >
              {RATES.filter((v) => allowed('fps', v)).map((v) => (
                <option key={v} value={String(v)}>
                  {v === 'same' ? t('Same as clip') : `${v} ${t('fps')}`}
                </option>
              ))}
            </select>
          </Field>
          <Field
            place={RIGHT}
            label={t('Sound')}
            tip={t('How much detail the sound keeps. 128 kbps is plenty for speech; music sounds better at 192 or more.')}
          >
            <select
              className={SELECT}
              aria-label={t('Sound')}
              value={preset.audio_kbps}
              onChange={(e) => change({ audio_kbps: Number(e.target.value) })}
            >
              {[...new Set([...info.choices.audio_kbps, preset.audio_kbps])]
                .sort((a, b) => a - b)
                .map((v) => (
                  <option key={v} value={v}>
                    {v} {t('kbps')}
                  </option>
                ))}
            </select>
          </Field>

          <div className={ROW}>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <label className="flex items-center gap-2 cursor-pointer text-sm" title={limitTip}>
                <input
                  type="checkbox"
                  className="size-4 accent-[#38BDF8]"
                  title={limitTip}
                  checked={limited}
                  onChange={(e) => tickLimit(e.target.checked)}
                />
                {t('Keep files under a size limit')}
              </label>
              {/* Always there, as on the size line below, so the marker is
                  announced when it appears; it takes keyboard focus, so its
                  tooltip is read as its description. */}
              <span aria-live="polite" className="text-xs">
                {limited && (
                  <span className="text-warn" tabIndex={0} title={slowerTip}>
                    {'⚠ '}
                    {t('Slower')}
                  </span>
                )}
              </span>
            </div>
            {/* The limit, on the next line, under the label. */}
            {limited && (
              <div className="mt-1 pl-6 flex items-center gap-1.5">
                {number('max_mb', t('Size limit in MB'), limitTip, '!w-28')}
                <span className="text-xs text-muted">{t('MB')}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Always there, a line high, so the cards below don't move when the
          figure arrives. */}
      <p className="text-xs text-muted flex flex-wrap items-center gap-x-3 gap-y-1 min-h-4">
        {sizeLine && <span title={sizeTip}>{sizeLine}</span>}
        {/* The warnings take keyboard focus, so their tooltip is read as their
            description, and a warning that appears is announced. */}
        <span aria-live="polite" className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
          {custom && estimate?.upscale && (
            <span
              className="text-warn"
              tabIndex={0}
              title={t('At this size or shape, the picture of a tall 1080x1920 clip is enlarged, so it looks no sharper.')}
            >
              {'⚠ '}
              {t('Enlarged')}
            </span>
          )}
          {watchable !== null && (
            <span
              className="text-warn"
              tabIndex={0}
              title={`${t('Under this limit, a clip at up to 60 fps still looks good at this size and frame rate if it’s no longer than about')} ${clipLength(watchable, t)}. ${t('A longer clip isn’t squeezed any further, so it can come out over your limit. A smaller Size or frame rate fits longer clips.')}`}
            >
              {'⚠ '}
              {t('Limit too small for long clips')}
            </span>
          )}
          {custom && estimate && !estimate.available && (
            <span className="text-warn" tabIndex={0} title={t('This PC can’t make this format. Choose another one.')}>
              {'⚠ '}
              {t('Not on this PC')}
            </span>
          )}
        </span>
      </p>
      {error && <p className="text-xs text-error">{error}</p>}
    </div>
  )
}
