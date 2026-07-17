import { useState } from 'react'
import { api } from '../lib/api'
import type { CaptionStyle } from '../lib/types'
import CaptionStyleControls, { DEFAULT_CAPTION_STYLE } from './CaptionStyleControls'
import BrandingEditor, { setWatermarkEnabled, watermarkSelection } from './WatermarkCard'
import { Folder } from './icons'
import { t } from '../lib/i18n'

const STYLE_KEY = 'generate-caption-style'

function loadSavedStyle(): Required<CaptionStyle> {
  try {
    return { ...DEFAULT_CAPTION_STYLE, ...JSON.parse(localStorage.getItem(STYLE_KEY) ?? '{}') }
  } catch {
    return { ...DEFAULT_CAPTION_STYLE }
  }
}

/** The "paste a link to make clips" bar. Shared by the Dashboard (top) and
 *  Clip Studio so creators always see where to post a link. Processing
 *  progress shows via the global <ProcessingBar>, so this stays lightweight. */
export default function GenerateBar(): JSX.Element {
  const [url, setUrl] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [reprocessUrl, setReprocessUrl] = useState<string | null>(null)
  const [queued, setQueued] = useState(false)
  const [styleOpen, setStyleOpen] = useState(false)
  // "Upload a video file" flow: pick a local file, fill in the same info a
  // downloaded video would have (title / creator / platform), then process.
  const [uploadPath, setUploadPath] = useState<string | null>(null)
  const [uploadTitle, setUploadTitle] = useState('')
  const [uploadChannel, setUploadChannel] = useState(
    localStorage.getItem('upload-channel') ?? ''
  )
  const [uploadPlatform, setUploadPlatform] = useState(
    localStorage.getItem('upload-platform') ?? 'youtube'
  )
  const [uploadBusy, setUploadBusy] = useState(false)
  const [captionStyle, setCaptionStyle] = useState<Required<CaptionStyle>>(loadSavedStyle)
  const [burnCaptions, setBurnCaptions] = useState<boolean>(
    localStorage.getItem('generate-captions') !== 'false'
  )
  const [longClips, setLongClips] = useState<boolean>(
    localStorage.getItem('generate-long-clips') === 'true'
  )
  // Longform: separate horizontal 1920x1080 outputs (same AI, 16:9 render).
  const [longform, setLongform] = useState<boolean>(
    localStorage.getItem('generate-longform') === 'true'
  )
  const [longformMode, setLongformMode] = useState<string>(
    localStorage.getItem('generate-longform-mode') ?? 'short_clips'
  )
  const [watermark, setWatermark] = useState<boolean>(watermarkSelection().enabled)
  // Gaming clips with a facecam: which band the camera goes in (remembered).
  const [splitPosition, setSplitPosition] = useState<'top' | 'bottom'>(
    (localStorage.getItem('generate-split-position') as 'top' | 'bottom') ?? 'top'
  )

  const setStyleField = <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]): void => {
    setCaptionStyle((s) => {
      const next = { ...s, [key]: value }
      localStorage.setItem(STYLE_KEY, JSON.stringify(next))
      return next
    })
  }

  const generate = async (targetUrl?: string, force = false): Promise<void> => {
    const u = (targetUrl ?? url).trim()
    if (!u) return
    setError(null)
    setReprocessUrl(null)
    try {
      const wm = watermarkSelection()
      const res = await api.createJob(u, {
        force,
        captionStyle,
        captions: burnCaptions,
        longClips,
        longform: longform ? { mode: longformMode } : null,
        watermarkProfileId: wm.enabled ? wm.profileId : null,
        splitPosition
      })
      if (res.already_processed) {
        setReprocessUrl(u)
        return
      }
      setUrl('')
      setQueued(true)
      setTimeout(() => setQueued(false), 4000)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="space-y-3">
      <div className="card flex gap-3 items-center flex-wrap">
        <input
          className="input w-80 max-w-full"
          placeholder={t('Paste a YouTube, Twitch, or Kick URL…')}
          aria-label="Video URL to make clips from"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && generate()}
        />
        <label className="flex items-center gap-2 cursor-pointer text-sm shrink-0">
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            checked={burnCaptions}
            onChange={(e) => {
              setBurnCaptions(e.target.checked)
              localStorage.setItem('generate-captions', String(e.target.checked))
            }}
          />
          {t('Captions')}
        </label>
        <button
          className="btn-ghost shrink-0"
          onClick={() => setStyleOpen(!styleOpen)}
          aria-expanded={styleOpen}
          disabled={!burnCaptions}
        >
          {t('Caption style')} {styleOpen ? '▾' : '▸'}
        </button>
        <label
          className="flex items-center gap-2 cursor-pointer text-sm shrink-0"
          title="TikTok monetization requires videos over 1 minute. On: clips run 61-180s. Off: clips are a natural 10-60s."
        >
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            checked={longClips}
            onChange={(e) => {
              setLongClips(e.target.checked)
              localStorage.setItem('generate-long-clips', String(e.target.checked))
            }}
          />
          60s+ <span className="text-muted">{t('(TikTok monetization)')}</span>
        </label>
        <label
          className="flex items-center gap-2 cursor-pointer text-sm shrink-0"
          title="Horizontal 1920x1080 outputs (YouTube, X/Twitter) using the same AI — the vertical Shorts workflow is unchanged"
        >
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            checked={longform}
            onChange={(e) => {
              setLongform(e.target.checked)
              localStorage.setItem('generate-longform', String(e.target.checked))
            }}
          />
          {t('Longform')} <span className="text-muted">(16:9)</span>
        </label>
        <label
          className="flex items-center gap-2 text-sm shrink-0"
          title="Gaming clips with a webcam overlay: which band the camera goes in on the vertical clip. Reaction/IRL clips aren't affected. Changeable per clip in the editor."
        >
          <span className="text-muted">{t('Camera')}</span>
          <select
            className="input !w-24 !py-1 text-sm"
            value={splitPosition}
            onChange={(e) => {
              const v = e.target.value as 'top' | 'bottom'
              setSplitPosition(v)
              localStorage.setItem('generate-split-position', v)
            }}
            aria-label="Facecam position for gaming clips"
          >
            <option value="top">{t('Top')}</option>
            <option value="bottom">{t('Bottom')}</option>
          </select>
        </label>
        <label
          className="flex items-center gap-2 cursor-pointer text-sm shrink-0"
          title="Burn your logo / channel handle into every clip. Configure the branding profile below."
        >
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            checked={watermark}
            onChange={(e) => {
              setWatermark(e.target.checked)
              setWatermarkEnabled(e.target.checked)
            }}
          />
          {t('Watermark')} <span className="text-muted">{t('(branding)')}</span>
        </label>
        <button
          className="btn-ghost shrink-0"
          onClick={async () => {
            const path = await window.studio.pickVideoFile()
            if (path) {
              setUploadPath(path)
              const base = path.split(/[\\/]/).pop() ?? ''
              setUploadTitle(base.replace(/\.[^.]+$/, ''))
              setError(null)
            }
          }}
          title="Make clips from a video file on this computer — e.g. your YouTube video before you publish it"
        >
          <Folder className="mr-1.5" />
          {t('Upload video file')}
        </button>
        <button className="btn-accent shrink-0 ml-auto" onClick={() => generate()}>
          {t('Generate clips')}
        </button>
        {styleOpen && (
          <div className="w-full space-y-3 border-t border-raised/60 pt-3">
            <p className="label">Caption style for all new clips (remembered)</p>
            <CaptionStyleControls idPrefix="gen" style={captionStyle} onChange={setStyleField} />
          </div>
        )}
        {longform && (
          <div className="w-full flex items-center gap-3 flex-wrap border-t border-raised/60 pt-3">
            <p className="label shrink-0">{t('Longform output')}</p>
            <select
              className="input !w-64"
              value={longformMode}
              onChange={(e) => {
                setLongformMode(e.target.value)
                localStorage.setItem('generate-longform-mode', e.target.value)
              }}
              aria-label="Longform output type"
            >
              <option value="short_clips">Short Clips (up to 60s, horizontal)</option>
              <option value="clips_140">Clips (up to 140s — X/Twitter)</option>
              <option value="highlights">Highlights (best-of, 8-20 min by quality)</option>
              <option value="edited_stream">Edited Stream (downtime removed)</option>
            </select>
          </div>
        )}
        {watermark && <BrandingEditor />}
      </div>

      {uploadPath && (
        <div className="card space-y-3">
          <p className="text-sm">
            <span className="font-semibold">Uploading:</span>{' '}
            <span className="text-muted">{uploadPath}</span>
          </p>
          <p className="text-xs text-muted">
            Fill this in like the video was downloaded — it files the video and its clips under
            this creator in your library and the Creators tab.
          </p>
          <div className="flex gap-3 flex-wrap items-end">
            <div className="flex-1 min-w-48">
              <label className="label">{t('Video title')}</label>
              <input
                className="input mt-1"
                value={uploadTitle}
                onChange={(e) => setUploadTitle(e.target.value)}
              />
            </div>
            <div className="flex-1 min-w-40">
              <label className="label">{t('Creator / channel name')}</label>
              <input
                className="input mt-1"
                placeholder="e.g. YourChannel"
                value={uploadChannel}
                onChange={(e) => {
                  setUploadChannel(e.target.value)
                  localStorage.setItem('upload-channel', e.target.value)
                }}
              />
            </div>
            <div>
              <label className="label">{t('Platform')}</label>
              <select
                className="input mt-1 !w-32"
                value={uploadPlatform}
                onChange={(e) => {
                  setUploadPlatform(e.target.value)
                  localStorage.setItem('upload-platform', e.target.value)
                }}
              >
                <option value="youtube">YouTube</option>
                <option value="twitch">Twitch</option>
                <option value="kick">Kick</option>
              </select>
            </div>
            <button
              className="btn-accent shrink-0"
              disabled={uploadBusy}
              onClick={async () => {
                setUploadBusy(true)
                setError(null)
                try {
                  await api.addLocalVideo({
                    path: uploadPath,
                    title: uploadTitle,
                    channel: uploadChannel,
                    platform: uploadPlatform,
                    captions: burnCaptions,
                    captionStyle,
                    longClips
                  })
                  setUploadPath(null)
                  setQueued(true)
                  setTimeout(() => setQueued(false), 4000)
                } catch (e) {
                  setError(e instanceof Error ? e.message : String(e))
                } finally {
                  setUploadBusy(false)
                }
              }}
            >
              {uploadBusy ? t('Importing…') : t('Make clips')}
            </button>
            <button className="btn-ghost shrink-0" onClick={() => setUploadPath(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {queued && <p className="text-sm text-accent px-1">{t('Queued — processing will start shortly.')}</p>}
      {error && <div className="card border-error/40 text-error text-sm">{error}</div>}
      {reprocessUrl && (
        <div className="card flex items-center gap-3 flex-wrap">
          <p className="text-sm flex-1 min-w-64">
            This video was already processed. Generate again with your <b>current settings</b>
            {longClips ? ' (60s+ clips)' : ' (regular clips)'}? Existing clips are kept — new ones
            are added alongside them.
          </p>
          <button className="btn-accent shrink-0" onClick={() => generate(reprocessUrl, true)}>
            {t('Process again')}
          </button>
          <button className="btn-ghost shrink-0" onClick={() => setReprocessUrl(null)}>
            {t('Cancel')}
          </button>
        </div>
      )}
    </div>
  )
}
