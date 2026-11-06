import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, errorText } from '../lib/api'
import { t } from '../lib/i18n'
import { formatBytes } from '../lib/exportJobs'
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
 *  chosen here, with an optional size limit, in the background on the editor
 *  lane (video/export_presets.py, server/editor_lane.py). Each choice is saved
 *  as it is made and comes back as the engine keeps it: checked, clamped, and
 *  sizes made even. What each choice does is in its tooltip. */

const SIZES: [ExportResolution, string][] = [
  ['same', 'Same as clip'],
  ['240p', '240p'],
  ['360p', '360p'],
  ['480p', '480p'],
  ['540p', '540p'],
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

function same(a: ExportPreset, b: ExportPreset): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

/** One choice: its name above its control, and what it does in the tooltip. */
function Field({ label, tip, children }: { label: string; tip: string; children: ReactNode }): JSX.Element {
  return (
    <label className="block min-w-0" title={tip}>
      <span className="label">{label}</span>
      <span className="block mt-1">{children}</span>
    </label>
  )
}

/** A choice made of more than one control (a width and a height). */
function Group({ label, tip, children }: { label: string; tip: string; children: ReactNode }): JSX.Element {
  return (
    <div className="min-w-0" title={tip} role="group" aria-label={label}>
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
        saved.current = p.default
        wanted.current = p.default
        setInfo(p)
        setPreset(p.default)
      })
      .catch(() => setInfo(null))
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
    if (key === 'max_mb' && trimmed === '') {
      if (wanted.current.max_mb !== null) change({ max_mb: null })
      return
    }
    const value = Number(trimmed)
    // Not a number: the saved value shows again.
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

  const number = (key: NumberKey, label: string, className: string, placeholder?: string): JSX.Element => (
    <input
      type="number"
      className={`input !py-1 text-sm ${className}`}
      aria-label={label}
      min={limit(key)?.[0]}
      max={limit(key)?.[1]}
      placeholder={placeholder}
      value={typing[key] ?? String(preset[key] ?? '')}
      onChange={(e) => {
        const value = e.target.value
        setTyping((d) => ({ ...d, [key]: value }))
      }}
      onBlur={() => commit(key)}
      onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
    />
  )

  // Formats: only those this PC can make. The saved one stays listed if this
  // PC can't make it any more, so the select shows what is saved.
  const known = codecs ?? []
  const codec = known.find((c) => c.codec === preset.codec)
  const codecList: ExportCodec[] = [...known.map((c) => c.codec)]
  if (!codec) codecList.push(preset.codec)
  const madeOn = Boolean(codec?.cpu && codec?.gpu) || preset.encoder !== 'auto'
  const pickCodec = (next: ExportCodec): void => {
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
    t('About how big one minute of a clip comes out with these choices, for a tall 1080x1920 clip at 60 fps. For the quality choices it is a range, because busy footage, like gaming or fast motion, needs more space than someone talking.'),
    custom && preset.max_mb !== null ? t('With a size limit, longer clips are made smaller to stay under it.') : ''
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className="card space-y-3" aria-label={t('Export settings')}>
      <h3 className="font-semibold">{t('Export')}</h3>

      <label
        className="flex items-center gap-3 text-sm"
        title={t('Original saves each clip exactly as Clips Kitty made it, straight away. Custom makes a new file with the choices below. It is made in the background, so you can keep working.')}
      >
        <span>{t('Export as')}</span>
        <select
          className="input !w-40 !py-1"
          aria-label={t('Export as')}
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
            label={t('Size')}
            tip={t('The short side of the picture: 720p makes a tall clip 720x1280 and a wide one 1280x720. Clips Kitty makes clips at 1080p, so 1440p and 2160p (4K) are enlarged from that and look no sharper.')}
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
          {preset.resolution === 'custom' ? (
            <Group
              label={t('Width × height')}
              tip={t('Your own size in pixels, from 128 to 4096. It sets the shape too. Odd numbers are made even.')}
            >
              {number('width', t('Width'), 'min-w-0')}
              <span className="text-muted">×</span>
              {number('height', t('Height'), 'min-w-0')}
            </Group>
          ) : (
            <Field
              label={t('Shape')}
              tip={t('The shape of the picture: your clip’s own, tall (9:16), wide (16:9), square (1:1) or 4:5.')}
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

          {preset.resolution !== 'custom' && preset.aspect === 'custom' && (
            <Group label={t('Custom shape')} tip={t('Your own shape, as width to height, each from 1 to 32.')}>
              {number('aspect_w', t('Shape width'), 'min-w-0')}
              <span className="text-muted">:</span>
              {number('aspect_h', t('Shape height'), 'min-w-0')}
            </Group>
          )}

          {reshaped && (
            <>
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
            </>
          )}

          <Field
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
          {preset.quality === 'custom' && (
            <Group
              label={t('Custom quality')}
              tip={t('Quality number: lower is better and bigger. 18 looks like the original, 23 is balanced and 28 is small (1 to 51). Bitrate: how much each second of picture gets, in kbps. 6000 suits a 1080p clip, and the file comes out close to it.')}
            >
              <select
                className={`${SELECT} min-w-0`}
                aria-label={t('Set the quality by')}
                value={preset.rate}
                onChange={(e) => change({ rate: e.target.value === 'bitrate' ? 'bitrate' : 'crf' })}
              >
                <option value="crf">{t('Quality number')}</option>
                <option value="bitrate">{t('Bitrate')}</option>
              </select>
              {preset.rate === 'crf'
                ? number('crf', t('Quality number'), '!w-16 shrink-0')
                : number('bitrate_kbps', t('Bitrate in kbps'), '!w-24 shrink-0')}
            </Group>
          )}

          <Field
            label={t('Format')}
            tip={
              codecs === null
                ? t('Checking what this PC can make…')
                : t('H.264 plays everywhere. HEVC makes smaller files at the same quality, but some older devices and sites can’t play it. AV1 is offered only when your graphics card can make it. Only the formats this PC can make are listed.')
            }
          >
            <select
              className={SELECT}
              aria-label={t('Format')}
              value={preset.codec}
              disabled={codecs === null}
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
          {madeOn && (
            <Field
              label={t('Made on')}
              tip={t('Automatic uses the graphics card when it can, which is faster, and the processor when it can’t. An export the graphics card can’t make is made on the processor instead.')}
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

          <Group
            label={t('Keep under')}
            tip={t('Leave it empty for no limit. With a limit (5 to 4000 MB), Clips Kitty picks the quality that fits each clip’s length, checks the finished file, and makes it once more a little smaller if it came out over. Short clips aren’t made bigger to fill it.')}
          >
            {number('max_mb', t('Size limit in MB'), '!w-28', t('no limit'))}
            <span className="text-xs text-muted">{t('MB')}</span>
          </Group>
        </div>
      )}

      {sizeLine && (
        <p className="text-xs text-muted flex flex-wrap items-center gap-x-3 gap-y-1">
          <span title={sizeTip}>{sizeLine}</span>
          {custom && estimate?.upscale && (
            <span
              className="text-warn"
              title={t('Bigger than the clip. Clips Kitty makes clips at 1080x1920, so at this size or shape the picture is enlarged from that: the file gets bigger, but it looks no sharper.')}
            >
              {'⚠ '}
              {t('Enlarged')}
            </span>
          )}
          {custom && estimate && !estimate.available && (
            <span className="text-warn" title={t('This PC can’t make this format. Choose another one.')}>
              {'⚠ '}
              {t('Not on this PC')}
            </span>
          )}
        </p>
      )}
      {error && <p className="text-xs text-error">{error}</p>}
    </div>
  )
}
