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

  useEffect(() => {
    api.languages().then((r) => setLangs(r.languages)).catch(() => {})
    getExportFolder().then(setFolder)
  }, [])

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
        include_video: includeVideo
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
        <button
          className="btn-accent !py-1 ml-auto"
          disabled={busy || picked.length === 0 || !folder}
          onClick={run}
          title={picked.length === 0 ? 'Pick at least one language' : undefined}
        >
          {busy ? t('Queueing…') : t('Export languages')}
        </button>
      </div>
      {notice && <p className="text-xs text-muted">{notice}</p>}
    </div>
  )
}
