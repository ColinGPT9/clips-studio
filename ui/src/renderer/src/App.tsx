import { useEffect, useState } from 'react'
import Dashboard from './pages/Dashboard'
import ClipStudio from './pages/ClipStudio'
import Creators from './pages/Creators'
import Models from './pages/Models'
import Queue from './pages/Queue'
import Settings from './pages/Settings'
import Watch from './pages/Watch'
import FeedbackHub from './components/FeedbackHub'
import ModelSwitcher from './components/ModelSwitcher'
import SetupWizard, { setupDone } from './components/SetupWizard'
import UpdateBanner from './components/UpdateBanner'
import { activeLocale, t } from './lib/i18n'
import { api } from './lib/api'
import type { StudioEvent } from './lib/types'
import { useEvents } from './lib/useEvents'
import { useQueueNotifications } from './lib/queueNotifications'
import mascot from './assets/mascot.png'

type Page = 'dashboard' | 'queue' | 'watch' | 'studio' | 'creators' | 'models' | 'settings'

const GITHUB_URL = 'https://github.com/ColinGPT9/clips-studio'

const NAV: { id: Page; label: string; icon: string }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: '◧' },
  { id: 'queue', label: 'Queue', icon: '≡' },
  { id: 'watch', label: 'Watched channels', icon: '◎' },
  // Renamed in 1.1.3. The previous label matched an existing product closely
  // enough to block the Microsoft Store listing, so do not change it back —
  // "Clip Editor" is also just what the page is.
  { id: 'studio', label: 'Clip Editor', icon: '✂' },
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
  const [creatorTarget, setCreatorTarget] = useState<number | null>(null)
  // First run: walk the creator through what the installer can't bundle
  // (Ollama, a model) before they hit a video that fails for want of it.
  // Settings can re-open it, so this is not a one-shot.
  const [wizard, setWizard] = useState(!setupDone())
  useEffect(() => {
    const open = (): void => setWizard(true)
    window.addEventListener('open-setup-wizard', open)
    return () => window.removeEventListener('open-setup-wizard', open)
  }, [])
  // Jump to the queue from anywhere (the Generate bar links here after
  // queueing). Same window-event mechanism as the setup wizard above —
  // this app navigates by state, not a router.
  useEffect(() => {
    const open = (): void => setPage('queue')
    window.addEventListener('open-queue', open)
    return () => window.removeEventListener('open-queue', open)
  }, [])
  // Same door for Settings: the editor's YouTube panel sends people here to
  // connect an account, rather than duplicating the setup flow inside a tab.
  useEffect(() => {
    const open = (): void => setPage('settings')
    window.addEventListener('open-settings', open)
    return () => window.removeEventListener('open-settings', open)
  }, [])
  // Mounted at the shell, not on the queue page: the point of a notification
  // is to reach someone who is NOT looking at the queue.
  useQueueNotifications()
  // How many channels are being watched, for the live dot beside "Watched
  // channels", so it shows from every page that the PC is on the job.
  const [watching, setWatching] = useState(0)
  useEffect(() => {
    const load = (): void => {
      api
        .automation()
        .then((s) => setWatching(s.enabled ? s.watching : 0))
        .catch(() => setWatching(0))
    }
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [])
  useEvents((e: StudioEvent) => {
    if (e.type === 'automation' && !('activity' in e) && !('doing' in e)) {
      api
        .automation()
        .then((s) => setWatching(s.enabled ? s.watching : 0))
        .catch(() => {})
    }
  })
  // Language switches re-render the tree IN PLACE (no reload): the page
  // state lives here, so the user stays wherever they were (e.g. Settings).
  const [locale, setLocale] = useState(activeLocale())
  useEffect(() => {
    const onLang = (): void => setLocale(activeLocale())
    window.addEventListener('app-language-changed', onLang)
    return () => window.removeEventListener('app-language-changed', onLang)
  }, [])

  const openInStudio = (videoId: string, clipId?: number): void => {
    setStudioTarget({ videoId, clipId })
    setPage('studio')
  }

  // A watched channel's card opens its creator profile, already selected.
  const openCreator = (creatorId: number): void => {
    setCreatorTarget(creatorId)
    setPage('creators')
  }

  return (
    <div className="flex h-screen" key={locale}>
      <aside className="w-52 shrink-0 bg-surface border-r border-raised/60 flex flex-col">
        {/* Same lockup as the website header: Clippy, then the name and
            tagline stacked beside him. `min-w-0` on the text column so a
            longer translated tagline wraps instead of pushing Clippy out of
            the sidebar — several of the 19 locales are wordier than English. */}
        <div className="px-5 py-5 flex items-center gap-2.5">
          <img
            src={mascot}
            alt=""
            width={34}
            height={34}
            className="shrink-0 w-[34px] h-[34px]"
          />
          <div className="min-w-0">
            <h1 className="text-lg font-bold leading-tight">
              Clips <span className="text-accent">Kitty</span>
            </h1>
            <p className="text-xs text-muted mt-px">{t('local-first AI clipping')}</p>
          </div>
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
              {item.id === 'watch' && watching > 0 && (
                <span
                  className="ml-auto relative flex size-2.5"
                  title={`${t('Watching')} ${watching}`}
                  aria-label={t('Watching')}
                >
                  <span className="absolute inline-flex size-full rounded-full bg-success opacity-60 animate-ping" />
                  <span className="relative inline-flex size-2.5 rounded-full bg-success" />
                </span>
              )}
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
          {/* The AGPL expects anyone running the program to be able to find
              its source. The link above is that offer, so it names the
              licence rather than leaving "open source" to mean anything. */}
          <p className="text-[10px] text-muted/60 mt-1.5">{t('AGPL-3.0 · 100% local · no cloud AI')}</p>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto flex flex-col">
        <UpdateBanner />
        {page === 'dashboard' && <Dashboard onOpenInStudio={openInStudio} />}
        {page === 'queue' && <Queue onOpenInStudio={(videoId) => openInStudio(videoId)} />}
        {page === 'watch' && (
          <Watch onOpenInStudio={(videoId) => openInStudio(videoId)} onOpenCreator={openCreator} />
        )}
        {page === 'studio' && (
          <ClipStudio target={studioTarget} onTargetConsumed={() => setStudioTarget(null)} />
        )}
        {page === 'creators' && (
          <Creators initialSelected={creatorTarget} onTargetConsumed={() => setCreatorTarget(null)} />
        )}
        {page === 'models' && <Models />}
        {page === 'settings' && <Settings />}
      </main>
      {wizard && (
        <SetupWizard
          onClose={() => {
            setWizard(false)
            setPage('studio')
          }}
        />
      )}
    </div>
  )
}
