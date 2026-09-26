import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText } from '../lib/api'
import type { AutomationStatus, StudioEvent, Watch as WatchRow, WatchPlatform } from '../lib/types'
import { useEvents } from '../lib/useEvents'
import WatchCard from '../components/watch/WatchCard'
import WatchLive from '../components/watch/WatchLive'
import { useWoopAccounts } from '../components/watch/WatchPublishSettings'
import WatchSchedule, { type ScheduleValue } from '../components/watch/WatchSchedule'
import { seedOptions } from '../components/queue/AddVideos'
import type { JobOptions } from '../lib/types'
import { platformLabel, WOOPSOCIAL_PLATFORMS } from '../lib/uploadpost'
import { t } from '../lib/i18n'
import { useAIStatus } from '../lib/useAIStatus'
import OpenRouterPrompt from '../components/OpenRouterPrompt'

type AddMode = 'auto' | 'ask' | 'off'

const ADD_MODES: { id: AddMode; label: string }[] = [
  { id: 'auto', label: 'Clip and publish automatically (hands-off)' },
  { id: 'ask', label: 'Clip, then ask me before publishing' },
  { id: 'off', label: 'Only clip' }
]

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
  onOpenInStudio,
  onOpenCreator
}: {
  onOpenInStudio?: (videoId: string) => void
  onOpenCreator?: (creatorId: number) => void
}): JSX.Element {
  const [status, setStatus] = useState<AutomationStatus | null>(null)
  const aiStatus = useAIStatus()
  const [watches, setWatches] = useState<WatchRow[] | null>(null)
  const [version, setVersion] = useState(0)
  const [platform, setPlatform] = useState<WatchPlatform>('youtube')
  const [channel, setChannel] = useState('')
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tray, setTray] = useState<boolean | null>(null)
  const woop = useWoopAccounts()
  const [addMode, setAddMode] = useState<AddMode | null>(null)
  const [addPlatforms, setAddPlatforms] = useState<string[] | null>(null)
  // The channel just added opens its whole setup.
  const [justAdded, setJustAdded] = useState<number | null>(null)
  // How its clips go out, chosen before Add like in the Publish dialog.
  const [addSchedule, setAddSchedule] = useState<ScheduleValue>({
    max_posts: 0,
    spread: true,
    per_day: 5,
    gap_hours: 1,
    day_start: ''
  })
  // How its clips are made: the Generate bar's own settings to start with,
  // which is what "my settings" means everywhere else in the app.
  const [addClip, setAddClip] = useState<JobOptions>(() => seedOptions())
  const [addHashtags, setAddHashtags] = useState('')
  const [addOnlyMine, setAddOnlyMine] = useState(false)
  const inFlight = useRef(false)
  // Until touched, the add form follows what WoopSocial can do: hands-off with
  // every connected account when it is set up, asking first when it is not.
  const mode: AddMode = addMode ?? (woop?.ready ? 'auto' : 'ask')
  const connected = WOOPSOCIAL_PLATFORMS.filter((id) => woop?.connected.includes(id))
  const platforms = addPlatforms ?? connected

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
      const added = await api.addWatch(platform, channel.trim(), {
        mode,
        platforms,
        ...addSchedule,
        hashtags: addHashtags
          .split(/[\s,]+/)
          .map((h) => h.replace(/^#/, '').trim())
          .filter(Boolean),
        ai_hashtags: !addOnlyMine
      }, addClip)
      setJustAdded(added.id)
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

      {/* The use case cloud AI exists for: a small always-on machine. Only
          the AI moves; watching, downloading, clipping and publishing stay
          on this PC. */}
      {aiStatus &&
        (aiStatus.active.local ? (
          <OpenRouterPrompt
            prominent
            title={t('Running Clips Kitty on a small, always-on PC?')}
            body={t(
              'Use OpenRouter with your own key: the AI runs in the cloud, while this PC watches, downloads, clips and publishes. Billed by OpenRouter to your account.'
            )}
          />
        ) : (
          (() => {
            // A plan signed in to (ChatGPT) only runs unattended jobs once
            // the user has allowed it in Settings → AI; say so here, where a
            // watch would otherwise just fail.
            const plan = aiStatus.signin?.find((s) => s.id === aiStatus.active.provider)
            const label =
              plan?.label ??
              aiStatus.providers.find((p) => p.id === aiStatus.active.provider)?.label ??
              aiStatus.active.provider
            return (
              <div className="space-y-1">
                <p className="text-xs text-muted">
                  {t('AI')}:{' '}
                  <span className="text-ink">
                    {label} · {aiStatus.active.model}
                  </span>{' '}
                  ({plan ? t('your plan') : t('your own key')})
                </p>
                {plan && !plan.automation_allowed && (
                  <p className="text-xs text-amber-400">
                    ⚠{' '}
                    {t(
                      "Watched channels won't use your ChatGPT plan until you allow it in Settings → AI. Videos you clip yourself still use it."
                    )}
                  </p>
                )}
              </div>
            )
          })()
        ))}

      <WatchLive />

      <section className="card space-y-3" aria-label={t('Watching')}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="font-semibold">{t('Watch channels')}</h2>
            <p className="text-sm text-muted mt-1 max-w-2xl">
              {status?.enabled
                ? `${t('Each channel is checked every')} ${status.interval_minutes} ${t('minutes while Clips Kitty is open. Hands-off channels are queued, clipped and published with nobody at the PC, so leave Clips Kitty running.')}`
                : t('Off. Nothing is checked, queued or published until you switch it on.')}
            </p>
          </div>
          <label className="inline-flex items-center gap-2 text-sm shrink-0">
            <input
              type="checkbox"
              className="size-4 accent-[#38BDF8]"
              checked={Boolean(status?.enabled)}
              disabled={busy || status === null}
              onChange={(e) => void act(() => api.setAutomation({ enabled: e.target.checked }))}
              aria-label={t('Watch channels')}
            />
            {status?.enabled ? t('On') : t('Off')}
          </label>
        </div>
        <label className="flex items-start gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            className="size-4 mt-0.5 accent-[#38BDF8]"
            checked={Boolean(status?.delete_sources)}
            disabled={busy || status === null}
            onChange={(e) => void act(() => api.setAutomation({ delete_sources: e.target.checked }))}
          />
          <span>
            {t('Delete each watched video’s download once its clips are published')}
            <span className="block text-xs text-muted">
              {t(
                'Keeps an always-on PC from filling its disk. The clips stay. Re-rendering one of those clips later needs the video downloaded again.'
              )}
            </span>
          </span>
        </label>
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
        <div className="space-y-2">
          <div className="flex items-center gap-3 flex-wrap">
            <span className="label">{t('When it posts')}</span>
            <select
              className="input !w-96"
              value={mode}
              onChange={(e) => setAddMode(e.target.value as AddMode)}
              aria-label={t('When it posts')}
            >
              {ADD_MODES.map((m) => (
                <option key={m.id} value={m.id}>
                  {t(m.label)}
                </option>
              ))}
            </select>
          </div>
          {mode !== 'off' && woop && !woop.ready && (
            <div className="text-sm text-warn flex items-center gap-3 flex-wrap">
              {t('Publishing goes through WoopSocial. Add your WoopSocial API key in Settings first.')}
              <button
                className="btn-ghost !px-2 !py-1 text-xs"
                onClick={() => window.dispatchEvent(new CustomEvent('open-settings'))}
              >
                {t('Open Settings')}
              </button>
            </div>
          )}
          {mode !== 'off' && woop?.ready && (
            <div className="flex items-center gap-x-5 gap-y-2 flex-wrap text-sm">
              <span className="label">{t('Publish to')}</span>
              {connected.length === 0 ? (
                <span className="text-muted">
                  {t('No accounts are connected to WoopSocial yet. Connect them in Settings.')}
                </span>
              ) : (
                connected.map((id) => (
                  <label key={id} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      className="size-4 accent-[#38BDF8]"
                      checked={platforms.includes(id)}
                      onChange={(e) =>
                        setAddPlatforms(
                          e.target.checked
                            ? [...platforms, id]
                            : platforms.filter((x) => x !== id)
                        )
                      }
                    />
                    {platformLabel(id)}
                  </label>
                ))
              )}
            </div>
          )}
          <div className="flex items-center gap-x-5 gap-y-2 flex-wrap text-sm">
            <span className="label">{t('Clips')}</span>
            {(
              [
                ['captions', 'Captions', addClip.captions !== false],
                ['long_clips', '60s+', Boolean(addClip.long_clips)],
                ['podcast', 'Podcast', Boolean(addClip.podcast)],
                ['vertical_live', 'Vertical Live', Boolean(addClip.vertical_live)],
                ['longform', 'Longform', Boolean(addClip.longform)]
              ] as const
            ).map(([key, label, on]) => (
              <label
                key={key}
                className="flex items-center gap-2 cursor-pointer"
                title={
                  key === 'vertical_live'
                    ? t('This channel streams vertically: keep each live’s own 9:16 layout, no face tracking. Videos with no vertical version are skipped.')
                    : undefined
                }
              >
                <input
                  type="checkbox"
                  className="size-4 accent-[#38BDF8]"
                  checked={on}
                  onChange={(e) => {
                    const next = { ...addClip }
                    if (key === 'captions') next.captions = e.target.checked
                    else if (key === 'longform')
                      next.longform = e.target.checked ? { mode: 'short_clips' } : null
                    else next[key] = e.target.checked
                    // Vertical Live keeps the live's own layout, so it can't go
                    // with Podcast (reframes) or Longform (16:9).
                    if (e.target.checked && key === 'vertical_live') {
                      delete next.podcast
                      next.longform = null
                    } else if (e.target.checked && (key === 'podcast' || key === 'longform')) {
                      delete next.vertical_live
                    }
                    setAddClip(next)
                  }}
                />
                {t(label)}
              </label>
            ))}
            <span className="text-xs text-muted">
              {t('Starts from your Generate settings. Caption style and more are in Clip settings after you add it.')}
            </span>
          </div>
          {mode !== 'off' && (
            <>
              <WatchSchedule value={addSchedule} onChange={setAddSchedule} />
              <div className="text-sm space-y-1">
                <label className="label block" htmlFor="add-watch-tags">
                  {t('Hashtags on every post')}
                </label>
                <input
                  id="add-watch-tags"
                  className="input"
                  value={addHashtags}
                  placeholder="#creatorname #twitch"
                  onChange={(e) => setAddHashtags(e.target.value)}
                />
                <label className="flex items-center gap-2 cursor-pointer text-sm">
                  <input
                    type="checkbox"
                    className="size-4 accent-[#38BDF8]"
                    checked={addOnlyMine}
                    onChange={(e) => setAddOnlyMine(e.target.checked)}
                  />
                  {t('Only use my hashtags')}
                  <span className="text-muted text-xs">{t('(leave out the ones the AI picks)')}</span>
                </label>
              </div>
            </>
          )}
        </div>
        <p className="text-xs text-muted">
          {mode === 'auto'
            ? t(
                'From now on, each new video is queued, clipped and published on the schedule above, with nobody at the PC. Videos already on the channel are listed but not clipped. Per-platform settings and caption text open after you add it.'
              )
            : t(
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
            onOpenCreator={onOpenCreator}
            openSetup={w.id === justAdded}
          />
        ))}
      {!watches && !error && <p className="text-sm text-muted">{t('Loading…')}</p>}
    </div>
  )
}
