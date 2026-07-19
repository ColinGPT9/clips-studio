import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { getExportFolder, pickExportFolder, setExportFolder } from '../lib/exportFolder'
import { Folder } from './icons'
import { t } from '../lib/i18n'

/** Optional multilingual publishing for a finished clip.
 *
 *  Writes a subtitle track per language beside the clip (the naming
 *  YouTube expects for per-language captions), so one clip reaches viewers
 *  in every language picked. Runs entirely on this PC through the model
 *  already installed — no account, no API, no cost.
 *
 *  Nothing here touches the clip: it only adds files next to it. */

const REMEMBER = 'multilingual-languages'
// Clipping models are chosen for judgement; translation wants multilingual
// strength, and Qwen is markedly better at it than Gemma. Offered, never
// required — the app keeps working with whatever is installed.
const RECOMMENDED = 'qwen2.5:7b'

export default function MultilingualExport({ clipId }: { clipId: number }): JSX.Element {
  const [langs, setLangs] = useState<{ code: string; name: string; native: string }[]>([])
  const [picked, setPicked] = useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(REMEMBER) ?? '[]')
    } catch {
      return []
    }
  })
  const [folder, setFolder] = useState('')
  const [includeVideo, setIncludeVideo] = useState(true)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [burnIn, setBurnIn] = useState(false)
  const [installed, setInstalled] = useState<string[]>([])
  const [transModel, setTransModel] = useState('')
  const [pulling, setPulling] = useState(false)

  useEffect(() => {
    api.languages().then((r) => setLangs(r.languages)).catch(() => {})
    getExportFolder().then(setFolder)
    api.models().then((m) => setInstalled(m.installed.map((i) => i.name))).catch(() => {})
    api.settings().then((st) => setTransModel(st.translation_model || '')).catch(() => {})
  }, [])

  const hasQwen = installed.some((n) => n.startsWith('qwen'))
  const useQwen = async (): Promise<void> => {
    const name = installed.find((n) => n.startsWith('qwen')) ?? RECOMMENDED
    await api.patchSettings({ translation_model: name })
    setTransModel(name)
    setNotice(`Translation will use ${name}.`)
  }

  const toggle = (code: string): void => {
    setPicked((prev) => {
      const next = prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
      localStorage.setItem(REMEMBER, JSON.stringify(next))
      return next
    })
  }

  const run = async (): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      const res = await api.translateClips({
        clip_ids: [clipId],
        languages: picked,
        folder,
        include_video: includeVideo,
        burn: burnIn
      })
      setNotice(
        `Queued — ${res.languages.length} language(s). Subtitle files land in your export folder; watch the Dashboard activity feed.`
      )
    } catch (e) {
      setNotice(`Error: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="border border-raised/60 rounded-lg p-3 space-y-3">
      <div>
        <p className="font-medium text-sm">{t('Publish in other languages')}</p>
        <p className="text-xs text-muted mt-0.5">
          {t(
            'Adds a subtitle track per language next to the clip — upload them with the video so each viewer sees their own language. Runs on this PC, free.'
          )}
        </p>
      </div>

      <div className="flex gap-1.5 flex-wrap">
        {langs.map((l) => (
          <button
            key={l.code}
            onClick={() => toggle(l.code)}
            className={`px-2.5 py-1 rounded-full text-xs border ${
              picked.includes(l.code)
                ? 'border-accent text-accent bg-accent/10'
                : 'border-raised text-muted hover:text-ink'
            }`}
            title={l.name}
          >
            {l.native}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-2 flex-wrap text-xs">
        <input
          className="input !w-44 !py-1"
          value={folder}
          onChange={(e) => {
            setFolder(e.target.value)
            setExportFolder(e.target.value)
          }}
          placeholder="export folder"
          title={folder}
        />
        <button
          className="btn-ghost !py-1"
          onClick={async () => {
            const chosen = await pickExportFolder()
            if (chosen) setFolder(chosen)
          }}
          aria-label="Choose export folder"
        >
          <Folder />
        </button>
        <label className="flex items-center gap-1.5 cursor-pointer text-muted">
          <input
            type="checkbox"
            className="size-3.5 accent-[#38BDF8]"
            checked={includeVideo}
            onChange={(e) => setIncludeVideo(e.target.checked)}
          />
          {t('Copy the video too')}
        </label>
        <label
          className="flex items-center gap-1.5 cursor-pointer text-muted"
          title="TikTok, Reels and Shorts don't read subtitle files — this makes one video per language with the captions painted in. Slower: the clip is re-rendered once without captions first."
        >
          <input
            type="checkbox"
            className="size-3.5 accent-[#38BDF8]"
            checked={burnIn}
            onChange={(e) => setBurnIn(e.target.checked)}
          />
          {t('Burn captions in (TikTok/Reels)')}
        </label>
        <button
          className="btn-accent !py-1 ml-auto"
          disabled={busy || picked.length === 0 || !folder}
          onClick={run}
          title={picked.length === 0 ? 'Pick at least one language' : undefined}
        >
          {busy ? t('Queueing…') : t('Export languages')}
        </button>
      </div>
      {/* Translation quality depends on the model — offer the better one. */}
      {!transModel && (
        <p className="text-xs text-muted border-t border-raised/60 pt-2">
          {hasQwen ? (
            <>
              {t('Qwen is installed and translates noticeably better than the clipping model.')}{' '}
              <button className="text-accent hover:underline" onClick={useQwen}>
                {t('Use it for translation')}
              </button>
            </>
          ) : (
            <>
              {t('Translations use your clipping model. For better quality, install')}{' '}
              <code>{RECOMMENDED}</code> ({t('about 4.7 GB, one time, stays on your PC')}){' '}
              <button
                className="text-accent hover:underline disabled:opacity-50"
                disabled={pulling}
                onClick={async () => {
                  setPulling(true)
                  setNotice(`Downloading ${RECOMMENDED} — watch the Models page for progress.`)
                  try {
                    await api.pullModel(RECOMMENDED)
                    await api.patchSettings({ translation_model: RECOMMENDED })
                    setTransModel(RECOMMENDED)
                    setNotice(`${RECOMMENDED} installed and set as the translation model.`)
                  } catch (e) {
                    setNotice(`Install failed: ${e instanceof Error ? e.message : String(e)}`)
                  } finally {
                    setPulling(false)
                  }
                }}
              >
                {pulling ? t('Installing…') : t('Install & use')}
              </button>
            </>
          )}
        </p>
      )}
      {transModel && (
        <p className="text-xs text-muted border-t border-raised/60 pt-2">
          {t('Translating with')} <code>{transModel}</code>{' '}
          <button
            className="text-accent hover:underline"
            onClick={async () => {
              await api.patchSettings({ translation_model: '' })
              setTransModel('')
            }}
          >
            {t('use the clipping model instead')}
          </button>
        </p>
      )}
      {notice && <p className="text-xs text-muted">{notice}</p>}
    </div>
  )
}
