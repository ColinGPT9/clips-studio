import { useEffect, useState } from 'react'
import {
  DEFAULT_APPEARANCE,
  FONTS,
  loadAppearance,
  saveAppearance,
  type Appearance
} from '../lib/appearance'
import { api } from '../lib/api'
import { getExportFolder, pickExportFolder, setExportFolder } from '../lib/exportFolder'
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

export default function Settings(): JSX.Element {
  return (
    <div className="p-6 space-y-5 max-w-xl">
      <h2 className="text-2xl font-bold">{t('Settings')}</h2>

      <LanguageCard />

      <AppearanceCard />

      <ExportFolderCard />

      <StorageCard />

      <div className="card text-sm text-muted">
        The active AI model is managed on the <span className="text-ink">Models</span> page. Advanced
        options (scoring weights, tracking, captions) live in <code>config/settings.yaml</code>.
      </div>
    </div>
  )
}
