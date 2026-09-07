import { useMemo, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'

/** Thumbnail picker, in the shape YouTube Studio uses: a few frames suggested
 *  from the video, plus your own image.
 *
 *  Two things worth knowing, both surfaced in the UI rather than buried:
 *  a custom thumbnail needs a phone-verified channel, and the Shorts feed
 *  ignores custom thumbnails entirely — they show on the watch page and on
 *  your channel, which is still worth having.
 */

interface Props {
  clipId: number
  duration: number
  currentTime: number
  value: string | null
  onChange: (thumbnail: string | null) => void
  disabled?: boolean
}

export default function YouTubeThumbnail({
  clipId,
  duration,
  currentTime,
  value,
  onChange,
  disabled
}: Props): JSX.Element {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [chosenAt, setChosenAt] = useState<number | null>(null)

  // The same three positions Studio offers. Recomputed only when the clip
  // changes, so the <img> elements are not rebuilt on every playhead move.
  const suggestions = useMemo(() => {
    const length = duration > 0 ? duration : 0
    return [0.25, 0.5, 0.75].map((fraction) => length * fraction)
  }, [duration, clipId])

  const pick = async (at: number): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      const { thumbnail } = await api.chooseThumbnail(clipId, { t: at })
      onChange(thumbnail)
      setChosenAt(at)
    } catch (e) {
      setNotice(String(e))
    } finally {
      setBusy(false)
    }
  }

  const upload = async (): Promise<void> => {
    // The dialog runs in Electron's main process, which reads the file there
    // and hands back the bytes. Nothing sends a path to the backend.
    const picked = await window.studio.pickThumbnailImage()
    if (!picked) return
    if ('error' in picked) {
      setNotice(
        picked.error === 'too-large'
          ? t("That image is over YouTube's 2 MB limit for thumbnails.")
          : t('That image could not be read.')
      )
      return
    }
    setBusy(true)
    setNotice('')
    try {
      const { thumbnail } = await api.chooseThumbnail(clipId, { image: picked.data })
      onChange(thumbnail)
      setChosenAt(null)
    } catch (e) {
      setNotice(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  return (
    <fieldset className="border border-raised/60 rounded-lg p-3 space-y-2">
      <legend className="label px-1">{t('Thumbnail')}</legend>

      <div className="grid grid-cols-3 gap-2">
        {suggestions.map((at, i) => (
          <button
            key={i}
            type="button"
            disabled={disabled || busy || duration <= 0}
            onClick={() => pick(at)}
            aria-label={`${t('Use the frame at')} ${at.toFixed(1)}s`}
            className={`relative rounded-md overflow-hidden border-2 transition-colors ${
              chosenAt === at ? 'border-accent' : 'border-transparent hover:border-raised'
            }`}
          >
            <img
              src={api.clipFrameUrl(clipId, at)}
              alt=""
              loading="lazy"
              className="w-full aspect-video object-cover bg-raised"
            />
          </button>
        ))}
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="btn-ghost !px-2.5 !py-1 text-xs"
          disabled={disabled || busy}
          onClick={() => pick(currentTime)}
        >
          {t('Use current frame')}
        </button>
        <button
          type="button"
          className="btn-ghost !px-2.5 !py-1 text-xs"
          disabled={disabled || busy}
          onClick={upload}
        >
          {t('Upload an image…')}
        </button>
        {value && (
          <button
            type="button"
            className="btn-ghost !px-2.5 !py-1 text-xs"
            disabled={disabled || busy}
            onClick={() => {
              onChange(null)
              setChosenAt(null)
            }}
          >
            {t('Clear')}
          </button>
        )}
      </div>

      {value && (
        <p className="text-[11px] text-success">{t('Thumbnail ready to upload.')}</p>
      )}
      {notice && <p className="text-[11px] text-error">{notice}</p>}
      <p className="text-[11px] text-muted">
        {t(
          'Custom thumbnails need a phone-verified YouTube channel, and the Shorts feed does not show them — they appear on the watch page and your channel. JPEG or PNG, up to 2 MB.'
        )}
      </p>
    </fieldset>
  )
}
