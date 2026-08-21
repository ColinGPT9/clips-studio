import { useEffect, useState } from 'react'
import {
  DEFAULT_APPEARANCE,
  FONTS,
  loadAppearance,
  saveAppearance,
  type Appearance
} from '../lib/appearance'
import { api } from '../lib/api'
import type { Preflight } from '../lib/types'
import { getExportFolder, pickExportFolder, setExportFolder } from '../lib/exportFolder'
import {
  notifyOnFinish,
  notifyOnQueueEmpty,
  setNotifyOnFinish,
  setNotifyOnQueueEmpty
} from '../lib/queueNotifications'
import {
  APP_LANGUAGES,
  LANGUAGE_NAMES,
  appLanguageSetting,
  languageLabel,
  setAppLanguage,
  t
} from '../lib/i18n'
import { Folder } from '../components/icons'

// Content languages offered in the dropdown — the transcription/caption
// side accepts any ISO code via settings.yaml; these are the focus markets.
// Same set the publishing pipeline translates into (multilingual/languages.py).
const CONTENT_LANGUAGES: [string, string][] = [
  ['auto', 'Auto-detect (per video)'],
  ...Object.keys(LANGUAGE_NAMES)
    .map((code): [string, string] => [code, languageLabel(code)])
    .sort((a, b) => a[1].localeCompare(b[1]))
]

function LanguageCard(): JSX.Element {
  const [contentLang, setContentLang] = useState('auto')

  useEffect(() => {
    api.settings().then((s) => setContentLang(s.content_language || 'auto')).catch(() => {})
  }, [])

  return (
    <div className="card space-y-4" aria-label="Language">
      <h3 className="font-semibold">{t('Language')}</h3>
      <div>
        <label htmlFor="app-lang" className="label">
          {t('App language')}
        </label>
        <select
          id="app-lang"
          className="input mt-1"
          value={appLanguageSetting()}
          onChange={(e) => setAppLanguage(e.target.value)}
        >
          {APP_LANGUAGES.map(([code, label]) => (
            <option key={code} value={code}>
              {label}
            </option>
          ))}
        </select>
        <p className="text-xs text-muted mt-1">
          {t('The app follows your Windows language unless you choose one here.')}
        </p>
      </div>
      <div>
        <label htmlFor="content-lang" className="label">
          {t('Content language')}
        </label>
        <select
          id="content-lang"
          className="input mt-1"
          value={contentLang}
          onChange={async (e) => {
            setContentLang(e.target.value)
            try {
              await api.patchSettings({ content_language: e.target.value })
            } catch {
              /* backend not up yet */
            }
          }}
        >
          {CONTENT_LANGUAGES.map(([code, label]) => (
            <option key={code} value={code}>
              {code === 'auto' ? t('Auto-detect (per video)') : label}
            </option>
          ))}
        </select>
        <p className="text-xs text-muted mt-1">
          {t('Transcription and burned-in captions use this language. Auto works for most streams; force it if a bilingual stream gets the wrong captions.')}
        </p>
      </div>
    </div>
  )
}

function ExportFolderCard(): JSX.Element {
  const [folder, setFolder] = useState('')

  useEffect(() => {
    getExportFolder().then(setFolder)
  }, [])

  return (
    <div className="card space-y-3" aria-label="Export location">
      <h3 className="font-semibold">{t('Export location')}</h3>
      <p className="text-xs text-muted">
        {t('Where exported clips are saved. Defaults to your Downloads folder.')}
      </p>
      <div className="flex items-center gap-2">
        <input
          className="input flex-1"
          value={folder}
          onChange={(e) => {
            setFolder(e.target.value)
            setExportFolder(e.target.value)
          }}
          placeholder="e.g. C:\\Users\\you\\Downloads"
          title={folder}
        />
        <button
          className="btn-ghost shrink-0"
          onClick={async () => {
            const chosen = await pickExportFolder()
            if (chosen) setFolder(chosen)
          }}
        >
          <Folder className="mr-1.5" />
          {t('Browse…')}
        </button>
      </div>
    </div>
  )
}

const GB = (bytes: number): string => `${(bytes / 1e9).toFixed(2)} GB`

/** Where the disk went, and a way to get some of it back.
 *
 *  Repeated processing leaves things nothing references — staging files
 *  from renders that failed or were cancelled, `.part` fragments from
 *  interrupted downloads, and an editor preview per clip. None of it shows
 *  anywhere, so the drive just quietly shrinks. */
function StorageCard(): JSX.Element {
  const [info, setInfo] = useState<Awaited<ReturnType<typeof api.storage>> | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState('')

  const load = (): void => {
    api.storage().then(setInfo).catch(() => setInfo(null))
  }
  useEffect(load, [])

  const LABELS: Record<string, string> = {
    partial_downloads: 'Interrupted downloads',
    orphan_downloads: 'Downloads with no video',
    orphan_transcripts: 'Transcripts with no video',
    render_leftovers: 'Leftovers from failed renders',
    orphan_clips: 'Clip files not in your library',
    previews: 'Editor previews (rebuilt on demand)'
  }

  return (
    <div className="card space-y-3" aria-label="Storage">
      <h3 className="font-semibold">{t('Storage')}</h3>
      {!info ? (
        <p className="text-xs text-muted">{t('Checking…')}</p>
      ) : (
        <>
          <div className="space-y-1 text-xs">
            {Object.entries(info.reclaimable)
              .filter(([, v]) => v.files > 0)
              .map(([k, v]) => (
                <div key={k} className="flex justify-between gap-3">
                  <span className="text-muted truncate">{LABELS[k] ?? k}</span>
                  <span className="tabular-nums shrink-0">
                    {v.files} · {GB(v.bytes)}
                  </span>
                </div>
              ))}
            {info.reclaimable_bytes === 0 && (
              <p className="text-muted">{t('Nothing to clean up — no leftovers on disk.')}</p>
            )}
          </div>

          <div className="flex items-center gap-3">
            <button
              className="btn-ghost !py-1 text-xs"
              disabled={busy || info.reclaimable_bytes === 0}
              onClick={async () => {
                setBusy(true)
                setDone('')
                try {
                  const r = await api.storageCleanup()
                  setDone(`Freed ${GB(r.bytes_freed)} across ${r.files_removed} file(s).`)
                  load()
                } catch (e) {
                  setDone(`Error: ${e instanceof Error ? e.message : String(e)}`)
                } finally {
                  setBusy(false)
                }
              }}
            >
              {busy ? t('Cleaning…') : `${t('Free up')} ${GB(info.reclaimable_bytes)}`}
            </button>
            {done && <span className="text-xs text-muted">{done}</span>}
          </div>

          <p className="text-[11px] text-muted/80 border-t border-raised/60 pt-2">
            {t('Source videos')}: {info.sources.files} · {GB(info.sources.bytes)}.{' '}
            {t(
              'These are the biggest thing on disk and are NOT touched — editing, re-rendering and translated burns all read them. Delete a video from the Dashboard to remove its source along with its clips.'
            )}
          </p>
        </>
      )}
    </div>
  )
}

/** Per-video disk usage, with a way to remove everything a finished video
 *  still costs.
 *
 *  The cleanup above only sweeps leftovers, and deleting clips one by one
 *  frees the clip files while leaving the multi-gigabyte source behind — so
 *  a library of finished videos kept growing with no way to reclaim it from
 *  inside the app. This lists what each video actually costs and deletes the
 *  clips, the source and the transcript together. */
function VideoStorageCard(): JSX.Element {
  const [info, setInfo] = useState<Awaited<ReturnType<typeof api.storageVideos>> | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [note, setNote] = useState('')

  const load = (): void => {
    api.storageVideos().then(setInfo).catch(() => setInfo(null))
  }
  useEffect(load, [])

  const remove = async (videoId: string, freed: number): Promise<void> => {
    setBusy(videoId)
    setNote('')
    try {
      await api.deleteVideo(videoId)
      setNote(`${t('Removed')} ${GB(freed)}.`)
      load()
    } catch (e) {
      setNote(`${t('Could not delete')}: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(null)
      setConfirming(null)
    }
  }

  if (!info || info.videos.length === 0) return <></>

  return (
    <div className="card space-y-3" aria-label="Video storage">
      <h3 className="font-semibold">{t('Delete a finished video')}</h3>
      <p className="text-xs text-muted">
        {t(
          'Removes the clips, the source video and the transcript together. The clips you have already exported are files on your computer and are not affected.'
        )}
      </p>
      <div className="max-h-72 overflow-y-auto space-y-1">
        {info.videos.map((v) => (
          <div
            key={v.video_id}
            className="flex items-center gap-3 text-xs border-t border-raised/50 pt-2"
          >
            <div className="flex-1 min-w-0">
              <p className="truncate">{v.title}</p>
              <p className="text-muted">
                {v.clips} {t('clips')} · {t('source')} {GB(v.source_bytes)} ·{' '}
                {t('clips')} {GB(v.clip_bytes)}
              </p>
            </div>
            <span className="tabular-nums shrink-0 font-medium">{GB(v.total_bytes)}</span>
            {confirming === v.video_id ? (
              <>
                <button
                  className="btn-accent !py-1 !px-2 shrink-0"
                  disabled={busy !== null}
                  onClick={() => remove(v.video_id, v.total_bytes)}
                >
                  {busy === v.video_id ? t('Deleting…') : t('Delete everything')}
                </button>
                <button
                  className="btn-ghost !py-1 !px-2 shrink-0"
                  onClick={() => setConfirming(null)}
                >
                  {t('Cancel')}
                </button>
              </>
            ) : (
              <button
                className="btn-ghost !py-1 !px-2 shrink-0"
                disabled={busy !== null}
                onClick={() => setConfirming(v.video_id)}
              >
                {t('Delete')}
              </button>
            )}
          </div>
        ))}
      </div>
      <div className="flex items-center gap-3 border-t border-raised/60 pt-2">
        <span className="text-xs text-muted">
          {t('Total on disk')}: {GB(info.total_bytes)}
        </span>
        {note && <span className="text-xs text-muted">{note}</span>}
      </div>
    </div>
  )
}

function AppearanceCard(): JSX.Element {
  const [appearance, setAppearance] = useState<Appearance>(loadAppearance)

  const update = (patch: Partial<Appearance>): void => {
    const next = { ...appearance, ...patch }
    setAppearance(next)
    saveAppearance(next)
  }

  return (
    <div className="card space-y-4" aria-label="Appearance and accessibility">
      <h3 className="font-semibold">{t('Appearance & accessibility')}</h3>
      <div>
        <label htmlFor="app-font" className="label">
          {t('Font')}
        </label>
        <select
          id="app-font"
          className="input mt-1"
          value={appearance.font}
          onChange={(e) => update({ font: e.target.value })}
        >
          {FONTS.map((f) => (
            <option key={f.label} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="app-scale" className="label">
          Text size — {appearance.scale}%
        </label>
        <input
          id="app-scale"
          type="range"
          min={87.5}
          max={150}
          step={12.5}
          value={appearance.scale}
          onChange={(e) => update({ scale: Number(e.target.value) })}
          className="w-full mt-1 accent-[#38BDF8]"
        />
      </div>
      <div className="flex items-end gap-3">
        <div>
          <label htmlFor="app-color" className="label">
            {t('Text colour')}
          </label>
          <input
            id="app-color"
            type="color"
            value={appearance.textColor}
            onChange={(e) => update({ textColor: e.target.value })}
            className="mt-1 h-10 w-16 bg-raised rounded-lg cursor-pointer border border-raised"
          />
        </div>
        <button className="btn-ghost" onClick={() => update(DEFAULT_APPEARANCE)}>
          {t('Reset to defaults')}
        </button>
      </div>
      <p className="text-xs text-muted">
        Changes apply instantly and are remembered. Keyboard focus outlines and reduced-motion
        preferences are always honored.
      </p>
    </div>
  )
}

/** Manual update check and release channel.
 *
 *  The banner only appears when there IS an update, so this is where somebody
 *  goes to ask, to see why a check failed, or to opt into early builds. */
function UpdateCard(): JSX.Element {
  const [state, setState] = useState<UpdateState | null>(null)
  const [channel, setChannel] = useState('stable')
  const [asked, setAsked] = useState(false)

  useEffect(() => {
    // Guarded for the same reason as the update bar: a preload without this
    // API must degrade to "updates unavailable", never take the window down.
    const updater = window.studio?.update
    if (!updater) {
      setState({ state: 'error', message: 'Updates are unavailable — restart Clips Kitty.' })
      return
    }
    updater.prefs().then((p) => setChannel(p.channel))
    return updater.onState(setState)
  }, [])

  const line = ((): string => {
    if (!asked && !state) return 'Clips Kitty checks for updates when it starts.'
    switch (state?.state) {
      case 'checking':
        return 'Checking…'
      case 'available':
        return `Version ${state.version} is available — see the bar at the top.`
      case 'downloading':
        return `Downloading… ${state.percent ?? 0}%`
      case 'ready':
        return `Version ${state.version} is ready to install.`
      case 'none':
        return "You're on the latest version."
      case 'dev':
        return 'Updates are disabled while running from source.'
      case 'store':
        return 'Installed from the Microsoft Store, which keeps Clips Kitty up to date for you.'
      case 'error':
        return `Could not check: ${state.message ?? 'unknown error'}`
      default:
        return 'Clips Kitty checks for updates when it starts.'
    }
  })()

  return (
    <div className="card space-y-3" aria-label="Updates">
      <h3 className="font-semibold">{t('Updates')}</h3>
      <p className="text-sm text-muted">{line}</p>
      <div className="flex items-center gap-3 flex-wrap">
        <button
          className="btn-ghost !py-1.5"
          disabled={state?.state === 'checking'}
          onClick={() => {
            setAsked(true)
            void window.studio.update.check()
          }}
        >
          {state?.state === 'checking' ? 'Checking…' : t('Check for updates')}
        </button>
        <label className="flex items-center gap-2 text-xs text-muted">
          {t('Release channel')}
          <select
            className="input !w-32 !py-1 text-xs"
            value={channel}
            onChange={(e) => {
              setChannel(e.target.value)
              void window.studio.update.prefs({ channel: e.target.value })
            }}
          >
            <option value="stable">Stable</option>
            <option value="beta">Beta</option>
            <option value="alpha">Alpha</option>
          </select>
        </label>
      </div>
      <p className="text-[11px] text-muted">
        Stable is finished releases only. Beta and Alpha get new features earlier and break
        more often. Nothing downloads or installs without you asking.
      </p>
    </div>
  )
}

/** Re-run first-run setup, and a live view of what the install is missing.
 *  Ollama and the model are deliberately not bundled, so this is where
 *  someone checks whether the app can actually make a clip. */
function SetupCard(): JSX.Element {
  const [pre, setPre] = useState<Preflight | null>(null)
  useEffect(() => {
    api.preflight().then(setPre).catch(() => setPre(null))
  }, [])

  const missing = (pre?.checks ?? []).filter((c) => !c.ok && c.blocking)

  return (
    <div className="card space-y-3" aria-label="Setup">
      <h3 className="font-semibold">{t('Setup')}</h3>
      {pre === null ? (
        <p className="text-sm text-muted">Checking what this install has…</p>
      ) : missing.length === 0 ? (
        <p className="text-sm text-muted">
          Everything Clips Kitty needs is installed and working.
        </p>
      ) : (
        <div className="text-sm">
          <p className="text-red-400 font-medium">
            {missing.length} thing{missing.length === 1 ? '' : 's'} still missing:
          </p>
          <ul className="mt-1.5 space-y-1">
            {missing.map((c) => (
              <li key={c.name} className="text-muted text-xs">
                <span className="text-ink">{c.name}</span> — {c.fix || c.detail}
              </li>
            ))}
          </ul>
        </div>
      )}
      <button
        className="btn-ghost !py-1.5"
        onClick={() => window.dispatchEvent(new Event('open-setup-wizard'))}
      >
        {t('Run setup again')}
      </button>
    </div>
  )
}

/** Notifications for a queue left running unattended. Off by default would
 *  defeat the purpose — the batch takes hours and nobody is watching — so
 *  both start on and can be switched off here. */
function NotificationsCard(): JSX.Element {
  const [onFinish, setOnFinish] = useState(notifyOnFinish())
  const [onEmpty, setOnEmpty] = useState(notifyOnQueueEmpty())
  return (
    <div className="card space-y-3">
      <h3 className="font-semibold">{t('Notifications')}</h3>
      <p className="text-sm text-muted">
        {t('Desktop notifications while a queue processes. Only shown when Clips Kitty is not the window you are looking at.')}
      </p>
      <label className="flex items-center gap-2 cursor-pointer text-sm">
        <input
          type="checkbox"
          className="size-4 accent-[#38BDF8]"
          checked={onFinish}
          onChange={(e) => {
            setOnFinish(e.target.checked)
            setNotifyOnFinish(e.target.checked)
          }}
        />
        {t('When each video finishes')}
      </label>
      <label className="flex items-center gap-2 cursor-pointer text-sm">
        <input
          type="checkbox"
          className="size-4 accent-[#38BDF8]"
          checked={onEmpty}
          onChange={(e) => {
            setOnEmpty(e.target.checked)
            setNotifyOnQueueEmpty(e.target.checked)
          }}
        />
        {t('When the whole queue is done')}
      </label>
    </div>
  )
}

export default function Settings(): JSX.Element {
  return (
    <div className="p-6 space-y-5 max-w-xl">
      <h2 className="text-2xl font-bold">{t('Settings')}</h2>

      <LanguageCard />

      <AppearanceCard />

      <NotificationsCard />

      <ExportFolderCard />

      <StorageCard />
      <VideoStorageCard />

      <SetupCard />

      <UpdateCard />

      <div className="card text-sm text-muted">
        The active AI model is managed on the <span className="text-ink">Models</span> page. Advanced
        options (scoring weights, tracking, captions) live in <code>config/settings.yaml</code>.
      </div>
    </div>
  )
}
