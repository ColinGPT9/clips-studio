import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText } from '../lib/api'
import type { AutomationStatus, StudioEvent, Watch as WatchRow, WatchPlatform } from '../lib/types'
import { useEvents } from '../lib/useEvents'
import WatchCard from '../components/watch/WatchCard'
import { t } from '../lib/i18n'

const PLACEHOLDER: Record<WatchPlatform, string> = {
  youtube: 'https://www.youtube.com/@channel or @handle',
  twitch: 'https://www.twitch.tv/channel or channel name',
  kick: 'https://kick.com/channel or channel name'
}

/** Watched channels: a creator posts, Clips Kitty clips it, nobody pastes a link.
 *
 *  Everything here is off until switched on, and each channel is its own
 *  go-ahead. The page only shows and edits; the watching itself happens in
 *  the backend (server/automation.py), which keeps going with this page
 *  closed, and, when the tray option is on, with the window closed too. */
export default function Watch({
  onOpenInStudio
}: {
  onOpenInStudio?: (videoId: string) => void
}): JSX.Element {
  const [status, setStatus] = useState<AutomationStatus | null>(null)
  const [watches, setWatches] = useState<WatchRow[] | null>(null)
  const [version, setVersion] = useState(0)
  const [platform, setPlatform] = useState<WatchPlatform>('youtube')
  const [channel, setChannel] = useState('')
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tray, setTray] = useState<boolean | null>(null)
  const inFlight = useRef(false)

  const refresh = useCallback(async (): Promise<void> => {
    if (inFlight.current) return
    inFlight.current = true
    try {
      const [s, w] = await Promise.all([api.automation(), api.watches()])
      setStatus(s)
      setWatches(w)
      setVersion((v) => v + 1)
      setError(null)
    } catch (e) {
      setError(errorText(e))
    } finally {
      inFlight.current = false
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEvents((e: StudioEvent) => {
    if (e.type === 'automation' || e.type === 'queue' || e.type === 'job') void refresh()
  })

  // Belt and braces against a dropped WebSocket, like the Queue page: a
  // watch's "checked 3 min ago" must not freeze because a socket died.
  useEffect(() => {
    const id = setInterval(() => void refresh(), 20000)
    return () => clearInterval(id)
  }, [refresh])

  // The tray option lives in the main process, which decides what closing
  // the window does. An older preload has no tray at all, so ask carefully.
  useEffect(() => {
    const bridge = window.studio?.tray
    if (!bridge) return
    bridge
      .get()
      .then((got) => setTray(got.keepInTray))
      .catch(() => setTray(null))
  }, [])

  const setKeepInTray = async (on: boolean): Promise<void> => {
    const bridge = window.studio?.tray
    if (!bridge) return
    const got = await bridge.set(on)
    setTray(got.keepInTray)
  }

  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await refresh()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const add = async (): Promise<void> => {
    if (!channel.trim()) return
    setAdding(true)
    setError(null)
    try {
      await api.addWatch(platform, channel.trim())
      setChannel('')
      await refresh()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setAdding(false)
    }
  }

  return (
    <div className="p-6 space-y-5 w-full max-w-5xl">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h1 className="text-xl font-bold">{t('Watched channels')}</h1>
        <p className="text-sm text-muted">
          {t('When a channel posts, Clips Kitty clips the new video and publishes the clips the way you set it up.')}
        </p>
      </div>

      <section className="card space-y-3" aria-label={t('Watching')}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="font-semibold">{t('Watch channels')}</h2>
            <p className="text-sm text-muted mt-1 max-w-2xl">
              {status?.enabled
                ? `${t('Each channel is checked every')} ${status.interval_minutes} ${t('minutes while Clips Kitty is open. New videos join the queue by themselves.')}`
                : t('Off. Nothing is checked, queued or published until you switch it on.')}
            </p>
          </div>
          <label className="inline-flex items-center gap-2 text-sm shrink-0">
            <input
              type="checkbox"
              className="size-4 accent-[#38BDF8]"
              checked={Boolean(status?.enabled)}
              disabled={busy || status === null}
              onChange={(e) => void act(() => api.setAutomation(e.target.checked))}
              aria-label={t('Watch channels')}
            />
            {status?.enabled ? t('On') : t('Off')}
          </label>
        </div>
        {tray !== null && (
          <label className="flex items-start gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              className="size-4 mt-0.5 accent-[#38BDF8]"
              checked={tray}
              onChange={(e) => void setKeepInTray(e.target.checked)}
            />
            <span>
              {t('Keep watching when the window is closed')}
              <span className="block text-xs text-muted">
                {t(
                  'Closing the window leaves Clips Kitty running in the system tray. Quit it from the tray icon. Windows going to sleep still stops it.'
                )}
              </span>
            </span>
          </label>
        )}
      </section>

      <section className="card space-y-2" aria-label={t('Add a channel')}>
        <h2 className="font-semibold">{t('Add a channel')}</h2>
        <div className="flex gap-2 flex-wrap">
          <select
            className="input !w-36"
            value={platform}
            onChange={(e) => setPlatform(e.target.value as WatchPlatform)}
            aria-label={t('Platform')}
          >
            <option value="youtube">YouTube</option>
            <option value="twitch">Twitch</option>
            <option value="kick">Kick</option>
          </select>
          <input
            className="input flex-1 min-w-64"
            value={channel}
            placeholder={PLACEHOLDER[platform]}
            onChange={(e) => setChannel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void add()
            }}
            aria-label={t('Channel')}
          />
          <button className="btn-accent" onClick={() => void add()} disabled={adding || !channel.trim()}>
            {adding ? t('Finding the channel…') : t('Add')}
          </button>
        </div>
        <p className="text-xs text-muted">
          {t(
            'Videos already on the channel are listed but not clipped. Only what it posts from now on is, unless you pick one yourself.'
          )}
        </p>
        {platform === 'kick' && (
          <p className="text-xs text-warn">
            {t(
              'Kick has no official way to list a channel’s videos. Clips Kitty uses the same unofficial one its Kick downloads rely on, so it can stop working without notice.'
            )}
          </p>
        )}
      </section>

      {error && <div className="card border-error/40 text-error text-sm">{error}</div>}

      {watches && status && watches.length === 0 && (
        <p className="text-sm text-muted">{t('No channels yet.')}</p>
      )}
      {watches &&
        status &&
        watches.map((w) => (
          <WatchCard
            key={w.id}
            watch={w}
            automation={status}
            version={version}
            onChanged={() => void refresh()}
            onOpenInStudio={onOpenInStudio}
          />
        ))}
      {!watches && !error && <p className="text-sm text-muted">{t('Loading…')}</p>}
    </div>
  )
}
