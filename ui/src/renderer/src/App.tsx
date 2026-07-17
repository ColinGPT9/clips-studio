import { useState } from 'react'
import Dashboard from './pages/Dashboard'
import ClipStudio from './pages/ClipStudio'
import Creators from './pages/Creators'
import Models from './pages/Models'
import Settings from './pages/Settings'
import FeedbackHub from './components/FeedbackHub'
import ModelSwitcher from './components/ModelSwitcher'
import { t } from './lib/i18n'

type Page = 'dashboard' | 'studio' | 'creators' | 'models' | 'settings'

const GITHUB_URL = 'https://github.com/ColinGPT9/clips-studio'

const NAV: { id: Page; label: string; icon: string }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: '◧' },
  { id: 'studio', label: 'Clip Studio', icon: '✂' },
  { id: 'creators', label: 'Creators', icon: '◉' },
  { id: 'models', label: 'Models', icon: '⬢' },
  { id: 'settings', label: 'Settings', icon: '⚙' }
]

export interface StudioTarget {
  videoId: string
  clipId?: number
}

export default function App(): JSX.Element {
  const [page, setPage] = useState<Page>('dashboard')
  const [studioTarget, setStudioTarget] = useState<StudioTarget | null>(null)

  const openInStudio = (videoId: string, clipId?: number): void => {
    setStudioTarget({ videoId, clipId })
    setPage('studio')
  }

  return (
    <div className="flex h-screen">
      <aside className="w-52 shrink-0 bg-surface border-r border-raised/60 flex flex-col">
        <div className="px-5 py-5">
          <h1 className="text-lg font-bold">
            Clips <span className="text-accent">Studio</span>
          </h1>
          <p className="text-xs text-muted mt-0.5">{t('local-first AI clipping')}</p>
        </div>
        <nav className="flex-1 px-3 space-y-1">
          {NAV.map((item) => (
            <button
              key={item.id}
              onClick={() => setPage(item.id)}
              className={`w-full text-left px-3 py-2.5 rounded-lg flex items-center gap-3 transition-colors ${
                page === item.id
                  ? 'bg-accent/15 text-accent font-medium'
                  : 'text-muted hover:bg-raised hover:text-ink'
              }`}
            >
              <span aria-hidden>{item.icon}</span>
              {t(item.label)}
            </button>
          ))}
        </nav>
        {/* Pinned below the nav: reachable from every page — bugs don't
            only happen on the Dashboard. */}
        <div className="px-3 pb-1">
          <FeedbackHub />
        </div>
        <ModelSwitcher />
        <div className="px-5 py-4 border-t border-raised/60">
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-muted hover:text-accent transition-colors"
          >
            <span className="font-semibold">{t('Open source')}</span> — {t('view & modify on GitHub')} ↗
          </a>
          <p className="text-[10px] text-muted/60 mt-1.5">{t('100% local · no cloud AI')}</p>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">
        {page === 'dashboard' && <Dashboard onOpenInStudio={openInStudio} />}
        {page === 'studio' && (
          <ClipStudio target={studioTarget} onTargetConsumed={() => setStudioTarget(null)} />
        )}
        {page === 'creators' && <Creators />}
        {page === 'models' && <Models />}
        {page === 'settings' && <Settings />}
      </main>
    </div>
  )
}
