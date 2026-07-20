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
import { APP_LANGUAGES, appLanguageSetting, setAppLanguage, t } from '../lib/i18n'
import { Folder } from '../components/icons'

// Content languages offered in the dropdown — the transcription/caption
// side accepts any ISO code via settings.yaml; these are the focus markets.
const CONTENT_LANGUAGES = [
  ['auto', 'Auto-detect (per video)'],
  ['en', 'English'],
  ['es', 'Español'],
  ['pt', 'Português'],
  ['hi', 'हिन्दी'],
  ['id', 'Bahasa Indonesia'],
  ['ja', '日本語'],
  ['ar', 'العربية'],
  ['ru', 'Русский'],
  ['de', 'Deutsch'],
  ['fr', 'Français'],
  ['zh', '简体中文'],
  ['vi', 'Tiếng Việt'],
  ['tl', 'Filipino'],
  ['tr', 'Türkçe'],
  ['ur', 'اردو'],
  ['bn', 'বাংলা'],
  ['th', 'ไทย'],
  ['ko', '한국어'],
  ['it', 'Italiano']
] as const

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

      <div className="card text-sm text-muted">
        The active AI model is managed on the <span className="text-ink">Models</span> page. Advanced
        options (scoring weights, tracking, captions) live in <code>config/settings.yaml</code>.
      </div>
    </div>
  )
}
