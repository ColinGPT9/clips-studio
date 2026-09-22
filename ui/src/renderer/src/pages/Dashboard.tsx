import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import Assistant from '../components/Assistant'
import NoClipsExplanation from '../components/NoClipsExplanation'
import AddVideos from '../components/queue/AddVideos'
import { Trash } from '../components/icons'
import ProcessingBar from '../components/ProcessingBar'
import SystemStats from '../components/SystemStats'
import { api } from '../lib/api'
import { useEvents } from '../lib/useEvents'
import { useJobWatch } from '../lib/useJobWatch'
import { t } from '../lib/i18n'
import type { Clip, Settings, StudioEvent, Video } from '../lib/types'

const DONATE_URL = 'https://paypal.me/clipsstudio'

// The assistant shares the window with the two cards above it, so its
// height is the user's call rather than ours. Remembered per machine.
const ASSISTANT_H_KEY = 'dashboard.assistantHeight'
const ASSISTANT_H_MIN = 132
const ASSISTANT_H_MAX = 620
const ASSISTANT_H_DEFAULT = 164

// The smallest useful height for the videos card: its own chrome, the table
// head, and two whole channel rows, so dragging the assistant up can never
// slice a channel name in half. Measured at runtime, because the card's
// header wraps — on width, on the language, and on whether the channel
// filter chip is showing — so a fixed number is only ever right on the
// window it was measured on. This is what stands in until the first
// measurement lands; it matches the steady state at a typical width, so the
// panel does not twitch on mount.
const VIDEOS_FLOOR_FALLBACK = 174

// Matches `gap-5` on the pool below: the space between the cards and the
// assistant, which comes out of the pool before either of them.
const GRID_GAP = 20

function clampHeight(px: number, max: number = ASSISTANT_H_MAX): number {
  return Math.max(ASSISTANT_H_MIN, Math.min(max, Math.round(px)))
}

function rememberHeight(px: number): void {
  // Written on release rather than on every pointer move, and never worth
  // failing a resize over: private windows can refuse to store anything.
  try {
    localStorage.setItem(ASSISTANT_H_KEY, String(px))
  } catch {
    /* the panel still resizes, it just forgets */
  }
}

type SortMode = 'newest' | 'channel'

function describeEvent(e: StudioEvent): string {
  if (e.type === 'progress') {
    if (e.stage === 'render') return `Rendering clip ${e.clip}/${e.total}`
    if (e.stage === 'done') return `Finished — ${e.clips} clip(s) created`
    if (e.stage === 'prefetch') return 'Downloading the next queued video in the background'
    return `Stage: ${e.stage}${e.title ? ` — ${e.title}` : ''}`
  }
  if (e.type === 'job') return `Job ${e.job_id}: ${e.status}${e.error ? ` (${e.error})` : ''}`
  if (e.type === 'model_pull') return `Model ${e.tag}: ${e.status}`
  return JSON.stringify(e)
}

export default function Dashboard({
  onOpenInStudio
}: {
  onOpenInStudio: (videoId: string, clipId?: number) => void
}): JSX.Element {
  const [videos, setVideos] = useState<Video[]>([])
  const [settings, setSettings] = useState<Settings | null>(null)
  const [log, setLog] = useState<string[]>([])
  const [sort, setSort] = useState<SortMode>('newest')
  const [channelFilter, setChannelFilter] = useState<{
    creatorId: number | null
    label: string
  } | null>(null)
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [assistantH, setAssistantH] = useState<number>(() => {
    try {
      const saved = Number(localStorage.getItem(ASSISTANT_H_KEY))
      return saved ? clampHeight(saved) : ASSISTANT_H_DEFAULT
    } catch {
      return ASSISTANT_H_DEFAULT
    }
  })
  const [clipsByVideo, setClipsByVideo] = useState<Record<string, Clip[]>>({})
  const lastEventAt = useRef(Date.now())
  const logRef = useRef<HTMLDivElement>(null)
  // Only follow the feed while the user is already at the bottom, so
  // scrolling up to read something is not yanked away by the next event.
  const logPinned = useRef(true)
  const resizeFrom = useRef<{ y: number; h: number } | null>(null)
  const poolRef = useRef<HTMLDivElement>(null)
  const [poolH, setPoolH] = useState(0)
  // The three pieces the drag cap is measured from: the videos card and its
  // scrolling list, plus the activity card, whose position tells us whether
  // the two are side by side or stacked.
  const videosCardRef = useRef<HTMLElement>(null)
  const videosHeaderRef = useRef<HTMLDivElement>(null)
  const videosScrollRef = useRef<HTMLDivElement>(null)
  const activityCardRef = useRef<HTMLElement>(null)
  // The promo cards now share the pool with the grid and the chat, so
  // their height comes out of what the chat may grow into. Measured
  // because they wrap to one column below the md breakpoint, which
  // roughly doubles them.
  const promoRef = useRef<HTMLDivElement>(null)
  const [promoH, setPromoH] = useState(0)
  const [videosFloor, setVideosFloor] = useState(VIDEOS_FLOOR_FALLBACK)
  const [stacked, setStacked] = useState(false)
  // The publishing referral, if one has been configured. Empty means the
  // call to action does not exist rather than pointing nowhere.
  const [publishUrl, setPublishUrl] = useState('')
  // Only watch while something is actually in flight, so an idle Dashboard
  // never polls.
  const busy = videos.some((v) => v.status !== 'done' && v.status !== 'failed')

  const refresh = async (): Promise<void> => {
    try {
      setVideos(await api.videos())
      setSettings(await api.settings())
      // An expanded row keeps the clips it fetched when it was opened, so
      // after a re-render it would go on showing the previous version. Re-read
      // whichever row is open rather than leaving it stale.
      if (expanded) {
        const fresh = await api.clips(expanded)
        setClipsByVideo((prev) => ({ ...prev, [expanded]: fresh }))
      }
    } catch {
      /* backend not up yet */
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  useEffect(() => {
    api
      .woopSocialStatus()
      .then((s) => setPublishUrl(s.affiliate_url || ''))
      .catch(() => {
        /* backend not up, or an older build: no call to action */
      })
  }, [])

  useEffect(() => {
    const el = logRef.current
    if (el && logPinned.current) el.scrollTop = el.scrollHeight
  }, [log])

  useEffect(() => {
    const el = poolRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => setPoolH(entry.contentRect.height))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  /** Size the list to a whole number of rows.
   *
   *  Scrolling a list whose box is not a multiple of the row height always
   *  ends with a channel name sliced through the middle at the bottom edge.
   *  Rather than leaving that to chance, the box is set to exactly as many
   *  whole rows as fit — nine rows and a little empty space beats nine and
   *  a half.
   *
   *  It has to be max-height, not height: the box is a `flex-1` child, and
   *  flex-grow overrides a plain height outright — the first version of this
   *  set 320px and the browser rendered 339. A maximum is respected by flex
   *  layout, so it actually binds.
   *
   *  Cleared before measuring so the layout reports the space really
   *  available; measuring the already-clamped box would shrink it a little
   *  more on every pass.
   */
  function snapListToWholeRows(): void {
    const scroller = videosScrollRef.current
    if (!scroller) return
    scroller.style.maxHeight = ''
    const rows = scroller.querySelectorAll('tr[data-video-row]')
    const head = scroller.querySelector('thead')
    if (rows.length === 0) return
    const available = scroller.getBoundingClientRect().height
    const headH = head ? head.getBoundingClientRect().height : 0
    const rowH = rows[0].getBoundingClientRect().height
    if (rowH <= 0 || available <= 0) return
    const fits = Math.floor((available - headH) / rowH)
    // Only when the list actually overflows. A short list keeps its natural
    // size rather than being padded out to a box it does not fill.
    if (fits >= 1 && rows.length > fits) {
      // Rounded UP: the header is a fractional 24.5px, and flooring left the
      // last row half a pixel short of the edge, which costs a whole row.
      scroller.style.maxHeight = `${Math.ceil(headH + fits * rowH)}px`
    }
  }

  // What the videos card needs to keep two whole channel names on screen.
  // Null means "cannot tell right now" — never zero — so the caller holds on
  // to the last good answer rather than letting the cap jump.
  function measureVideosFloor(): number | null {
    const card = videosCardRef.current
    const scroller = videosScrollRef.current
    if (!card || !scroller) return null
    // Unclamped, for the same reason as above: the chrome is derived by
    // subtracting this, and a snapped box would inflate it every pass.
    scroller.style.maxHeight = ''
    const scrollH = scroller.getBoundingClientRect().height
    // A card squeezed past its own chrome reports a zero-height scroller,
    // which would make the chrome below look bigger than it is and hand back
    // too generous a floor. Wait for the next pass instead.
    if (scrollH <= 0) return null
    const rows = scroller.querySelectorAll('tr[data-video-row]')
    if (rows.length === 0) return null
    const head = scroller.querySelector('thead')
    // The scroller is the card's only growing child, so everything else in
    // the card is a fixed cost that does not move when the card resizes.
    // That is what keeps this from chasing its own tail.
    const chrome = card.getBoundingClientRect().height - scrollH
    // Every collapsed row is the same height, so one of them stands in for
    // two — which also means a list of one video still measures correctly.
    const unit = rows[0].getBoundingClientRect().height
    const headH = head ? head.getBoundingClientRect().height : 0
    // Whole pixels: rounding down here is what clips a row by a hair, and an
    // integer settles, so re-measuring after our own resize returns the same
    // number and React stops re-rendering.
    return Math.ceil(chrome + headH + unit * 2)
  }

  // Side by side the two cards share the grid's height; stacked they each
  // want their own. Read it off the rectangles rather than repeating
  // Tailwind's breakpoint here, so this stays true if the grid changes.
  function measureStacked(): boolean | null {
    const videos = videosCardRef.current?.getBoundingClientRect()
    const activity = activityCardRef.current?.getBoundingClientRect()
    if (!videos || !activity) return null
    return activity.top >= videos.bottom - 1
  }

  function remeasure(): void {
    const floor = measureVideosFloor()
    if (floor != null) setVideosFloor(floor)
    // After the floor, because it needs the box unconstrained to measure.
    snapListToWholeRows()
    const promo = promoRef.current?.getBoundingClientRect().height
    if (promo != null) setPromoH(Math.ceil(promo))
    const isStacked = measureStacked()
    if (isStacked != null) setStacked(isStacked)
  }

  // Watch the header rather than the card. The card is the thing our own cap
  // resizes, so observing it would feed back into itself; the header is
  // fixed-height within the card and still changes on every input that moves
  // the floor — wrapping on width, the filter chip appearing, a wordier
  // language, zoom.
  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => remeasure())
    // Neither of these is resized by the cap this feeds, so watching them
    // cannot loop — unlike the videos card, which is.
    if (videosHeaderRef.current) observer.observe(videosHeaderRef.current)
    if (promoRef.current) observer.observe(promoRef.current)
    return () => observer.disconnect()
  }, [])

  const gridFloor = stacked ? videosFloor * 2 + GRID_GAP : videosFloor

  // The pool now holds three things with two gaps between them: the cards,
  // the promos, and the chat.
  const maxAssistantH = poolH
    ? Math.max(
        ASSISTANT_H_MIN,
        Math.min(ASSISTANT_H_MAX, poolH - 2 * GRID_GAP - promoH - gridFloor)
      )
    : ASSISTANT_H_MAX
  const shownAssistantH = Math.min(assistantH, maxAssistantH)

  function startResize(e: React.PointerEvent<HTMLDivElement>): void {
    e.preventDefault()
    resizeFrom.current = { y: e.clientY, h: shownAssistantH }
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  function onResize(e: React.PointerEvent<HTMLDivElement>): void {
    const from = resizeFrom.current
    if (!from) return
    // The panel grows upwards from its own top edge, so dragging up makes
    // it taller and the cards above it give up the space.
    setAssistantH(clampHeight(from.h - (e.clientY - from.y), maxAssistantH))
  }

  function endResize(e: React.PointerEvent<HTMLDivElement>): void {
    if (!resizeFrom.current) return
    resizeFrom.current = null
    e.currentTarget.releasePointerCapture(e.pointerId)
    // What is on screen, not the raw preference: pressing the grip without
    // moving it never runs onResize, so `assistantH` can still hold a height
    // this window is too small for, and storing that would disagree with
    // what the user is looking at. The stored value on load is deliberately
    // left unclamped though — poolH is 0 on first render so there is nothing
    // to clamp against, and keeping the preference means a big panel comes
    // back when the window is big again.
    rememberHeight(shownAssistantH)
  }

  function onResizeKey(e: React.KeyboardEvent<HTMLDivElement>): void {
    const step = e.key === 'ArrowUp' ? 24 : e.key === 'ArrowDown' ? -24 : 0
    if (!step) return
    e.preventDefault()
    const next = clampHeight(shownAssistantH + step, maxAssistantH)
    setAssistantH(next)
    rememberHeight(next)
  }

  useEvents((e) => {
    lastEventAt.current = Date.now()
    const msg = describeEvent(e)
    const line = `${new Date().toLocaleTimeString()}  ${msg}`
    setLog((prev) => {
      // Skip consecutive duplicates: only log when the message actually
      // changes, so a stage that emits every second doesn't spam the feed.
      const last = prev[prev.length - 1]
      if (last && last.slice(last.indexOf('  ') + 2) === msg) return prev
      // Oldest first, newest at the bottom: a feed you read downwards, and
      // the only order in which following it means anything.
      return [...prev, line].slice(-200)
    })
    if (e.type === 'job' && (e.status === 'done' || e.status === 'failed')) refresh()
  })

  // That single event is not guaranteed to arrive -- the server drops events
  // for a client that falls behind, and a long render emits thousands. Miss it
  // once and this page shows an empty video after a run that worked, which
  // reads as "no clips were created". Ask the server when the stream goes
  // quiet instead of assuming.
  useJobWatch({ active: busy, lastEventAt, onSettled: refresh })

  const remove = async (videoId: string, label: string): Promise<void> => {
    if (!window.confirm(`Delete "${label}" and all its clips? This removes the files from disk too.`))
      return
    try {
      await api.deleteVideo(videoId)
      refresh()
    } catch (e) {
      window.alert(`Could not delete: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const fmtTime = (s: number): string => {
    if (!s) return '—'
    const m = Math.floor(s / 60)
    return m > 0 ? `${m}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`
  }

  const toggleExpand = async (videoId: string): Promise<void> => {
    if (expanded === videoId) {
      setExpanded(null)
      return
    }
    setExpanded(videoId)
    if (!clipsByVideo[videoId]) {
      try {
        const clips = await api.clips(videoId)
        setClipsByVideo((prev) => ({ ...prev, [videoId]: clips }))
      } catch {
        /* leave empty */
      }
    }
  }

  const shown = useMemo(() => {
    let list = [...videos]
    if (channelFilter) {
      // Creator-aware: clicking a channel shows ALL of that creator's
      // channels (e.g. their Twitch and YouTube accounts linked in the
      // Creators tab), falling back to the exact channel string for
      // videos with no creator profile.
      list = list.filter((v) =>
        channelFilter.creatorId != null
          ? v.creator_id === channelFilter.creatorId
          : (v.channel_name || 'Unknown channel') === channelFilter.label
      )
    }
    const q = search.trim().toLowerCase()
    if (q) {
      list = list.filter(
        (v) =>
          (v.title || '').toLowerCase().includes(q) ||
          (v.channel_name || '').toLowerCase().includes(q)
      )
    }
    if (sort === 'channel') {
      list.sort(
        (a, b) =>
          (a.channel_name || 'zzz').localeCompare(b.channel_name || 'zzz') ||
          b.created_at.localeCompare(a.created_at)
      )
    } else {
      list.sort((a, b) => b.created_at.localeCompare(a.created_at))
    }
    return list
  }, [videos, sort, channelFilter, search])

  // The rest of what the header observer cannot see: the first rows
  // arriving, and the first pass once the pool has reported its height.
  // Before paint, so the cap is already right the first time it is drawn.
  useLayoutEffect(remeasure, [shown.length, poolH, publishUrl, shownAssistantH, expanded])

  return (
    <div className="h-full flex flex-col p-6 gap-4">
      {/* Pinned top: title + post bar always visible */}
      <div className="shrink-0 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-2xl font-bold">{t('Dashboard')}</h2>
          {settings && (
            <span className="bg-raised px-3 py-1.5 rounded-lg text-sm">
              model: <span className="text-accent font-medium">{settings.model}</span>
            </span>
          )}
        </div>
        {/* The same list builder as the Queue page, not a second copy: one
            video or ten, each with its own options, started when you say so. */}
        <AddVideos onAdded={refresh} />
        <ProcessingBar />
      </div>

      {/* Middle: videos + activity, each scrolls on its own */}
      {/* Two tall cards side by side, with the assistant on its own row beneath
          so neither of them loses width or height to it. */}
      {/* One pool of space, split between the cards and the assistant. The
          pool's own height does not depend on where the split falls, which is
          what makes the cap below stable. */}
      <div ref={poolRef} className="flex-1 min-h-0 flex flex-col gap-5">
        <div className="flex-1 min-h-0 grid grid-cols-1 xl:grid-cols-2 gap-5">
        <section
          ref={videosCardRef}
          className="card flex flex-col overflow-hidden"
          aria-label="Processed videos"
        >
          <div
            ref={videosHeaderRef}
            className="flex items-center justify-between mb-3 gap-3 flex-wrap shrink-0"
          >
            <h3 className="font-semibold">{t('Processed videos')}</h3>
            <div className="flex items-center gap-2 flex-wrap">
              <input
                type="search"
                className="input !w-44 !py-1 text-sm"
                placeholder={t('Search title or channel…')}
                aria-label="Search processed videos by title or channel"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              {channelFilter && (
                <button className="btn-ghost !px-2.5 !py-1 text-xs" onClick={() => setChannelFilter(null)}>
                  {channelFilter.label} ✕
                </button>
              )}
              <label htmlFor="sort-videos" className="label">
                {t('Sort')}
              </label>
              <select
                id="sort-videos"
                className="input !w-32 !py-1 text-sm"
                value={sort}
                onChange={(e) => setSort(e.target.value as SortMode)}
              >
                <option value="newest">{t('Newest')}</option>
                <option value="channel">{t('Channel A–Z')}</option>
              </select>
            </div>
          </div>

          <div ref={videosScrollRef} className="overflow-y-auto flex-1 min-h-0">
          {shown.length === 0 ? (
            <p className="text-muted text-sm">{t('Nothing yet - paste a link above to make your first clips.')}</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="label text-left">
                  <th className="pb-2 font-normal">{t('Channel')}</th>
                  <th className="pb-2 font-normal">{t('Title')}</th>
                  <th className="pb-2 font-normal">{t('Status')}</th>
                  <th className="pb-2 font-normal text-right">{t('Clips')}</th>
                  <th className="pb-2 font-normal text-right">{t('Time')}</th>
                  <th className="pb-2 font-normal"></th>
                </tr>
              </thead>
              <tbody>
                {shown.map((v) => (
                  <>
                    {/* Marked so the drag cap can measure a row's height
                        without picking up an expanded row's clip list. */}
                    <tr key={v.video_id} data-video-row className="border-t border-raised/50">
                      <td className="py-2 pr-2 max-w-36">
                        <button
                          className="text-accent hover:underline truncate block max-w-full text-left"
                          onClick={() =>
                            setChannelFilter({
                              creatorId: v.creator_id ?? null,
                              label: v.creator_name || v.channel_name || 'Unknown channel'
                            })
                          }
                          aria-label={`Show all videos from ${v.creator_name || v.channel_name || 'unknown channel'}`}
                          title={
                            v.creator_id != null
                              ? 'Show this creator’s videos from all their linked channels'
                              : undefined
                          }
                        >
                          {v.channel_name || '—'}
                        </button>
                      </td>
                      <td className="py-2 pr-3">
                        <button
                          className="text-left hover:text-accent truncate block max-w-64"
                          onClick={() => toggleExpand(v.video_id)}
                          aria-expanded={expanded === v.video_id}
                          aria-label={`Show generated clip titles for ${v.title || v.video_id}`}
                        >
                          {expanded === v.video_id ? '▾ ' : '▸ '}
                          {v.title || v.video_id}
                        </button>
                      </td>
                      <td className="py-2">
                        <span
                          className={
                            v.status === 'done'
                              ? 'text-success'
                              : v.status === 'failed'
                                ? 'text-error'
                                : 'text-warn'
                          }
                        >
                          {v.status}
                        </span>
                      </td>
                      <td className="py-2 text-right tabular-nums">{v.clip_count}</td>
                      <td className="py-2 text-right tabular-nums text-muted">
                        {fmtTime(v.process_seconds)}
                      </td>
                      <td className="py-2 text-right">
                        <button
                          className="text-muted hover:text-error px-1"
                          onClick={() => remove(v.video_id, v.title || v.video_id)}
                          aria-label={`Delete ${v.title || v.video_id}`}
                          title="Delete this video and its clips"
                        >
                          <Trash />
                        </button>
                      </td>
                    </tr>
                    {expanded === v.video_id && (
                      <tr key={`${v.video_id}-clips`}>
                        <td colSpan={6} className="pb-3 pl-6">
                          {clipsByVideo[v.video_id] === undefined ? (
                            <p className="text-muted text-xs">{t('Loading clips…')}</p>
                          ) : clipsByVideo[v.video_id].length === 0 ? (
                            <NoClipsExplanation outcome={v.outcome} compact />
                          ) : (
                            <ul className="space-y-1 text-xs text-muted pl-1">
                              {clipsByVideo[v.video_id].map((c) => (
                                <li key={c.id}>
                                  <button
                                    className="text-left text-accent hover:underline"
                                    onClick={() => onOpenInStudio(v.video_id, c.id)}
                                    title="Open this clip in Clip Editor"
                                  >
                                    {c.title || c.hook || 'Untitled'}
                                  </button>{' '}
                                  <span className="text-muted">
                                    ({Math.round(c.end_s - c.start_s)}s · score {c.score})
                                  </span>
                                </li>
                              ))}
                            </ul>
                          )}
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          )}
          </div>
        </section>

        <section
          ref={activityCardRef}
          className="card flex flex-col overflow-hidden"
          aria-label="Activity log"
        >
          <h3 className="font-semibold mb-3 shrink-0">{t('Activity')}</h3>
          <div
            ref={logRef}
            onScroll={(e) => {
              const el = e.currentTarget
              logPinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
            }}
            className="overflow-y-auto flex-1 min-h-0 font-mono text-xs space-y-1 text-muted"
            role="log"
          >
            {log.length === 0 ? (
              <p>{t('Waiting for events…')}</p>
            ) : (
              log.map((line, i) => <p key={i}>{line}</p>)
            )}
          </div>
          {/* System stats tucked at the bottom of Activity, out of the way */}
          <div className="shrink-0 mt-3 pt-3 border-t border-raised/60">
            <SystemStats compact />
          </div>
        </section>

        </div>


        {/* Sits above the chat box rather than under it. The assistant is the
            last thing on the page because that is where every chat app puts
            its input, and reaching past it to a promo would be odd.

            Two asks of equal weight. Publishing sits first
            because it is what someone looking at a finished clip wants next,
            and because it is the one that pays for the app.

            Both cards are the same three rows — blurb, button, footnote — so
            the buttons land on the same line however the text above them
            wraps.

            Flat tint rather than the gradient this used to be: split across
            two cards, a left-to-right gradient ended one half bright and
            started the next half dark, so the seam down the middle read as
            two different colours. */}
        <div ref={promoRef} className="shrink-0 grid gap-4 md:grid-cols-2 items-stretch">
          {publishUrl && (
            <div className="card bg-accent/20 border border-accent/40 !py-3">
              {/* The footnote sits inside the text column rather than under
                  the whole card, so the button centres against everything
                  beside it instead of floating above the optical middle. */}
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0">
                  {/* One message in every state, and it sells. The card used
                      to go quiet once posting was set up, which wasted the
                      best spot on the page: the referral is how the app earns
                      and it is worth showing whether or not this machine has
                      an account yet. */}
                  <p className="font-bold text-lg text-ink">
                    {t('Post every clip everywhere 🚀')}
                  </p>
                  {/* Names the free ceiling instead of praising the free
                      tier. The referral pays only on a subscription, and the
                      old line sold the plan that earns nothing. The limit is
                      real: every tier reaches the same nine networks, but
                      free connects two accounts, so YouTube plus TikTok plus
                      Instagram already does not fit. The honest sentence and
                      the persuasive one are the same sentence. */}
                  <p className="text-sm text-ink/80 mt-0.5">
                    {t(
                      'One upload reaches YouTube, TikTok, Instagram, Facebook, X, LinkedIn, Threads, Pinterest and Bluesky. The free plan connects two accounts. Connect all nine and post to every platform at once.'
                    )}
                  </p>
                  {/* Readable, not buried: it has to be legible to be a
                      disclosure at all, and it is never shown apart from the
                      link it describes — so both are here in every state. */}
                  <p className="text-xs text-ink/70 mt-1.5">
                    {t(
                      'Affiliate link - Clips Kitty may earn a commission if you sign up through it, at no extra cost to you.'
                    )}
                  </p>
                </div>
                {/* One call to action, not two. A second button competing
                    with it split the card's job; where to put the key is
                    said in the text instead. */}
                <button
                  onClick={() => void window.studio.openExternal(publishUrl)}
                  className="btn-accent shrink-0 text-lg px-8 py-3.5 font-semibold"
                  title={publishUrl}
                >
                  {t('Start posting everywhere ↗')}
                </button>
              </div>
            </div>
          )}

          <div
            className={`card bg-accent/20 border border-accent/40 !py-3${
              publishUrl ? '' : ' md:col-span-2'
            }`}
          >
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="font-bold text-lg text-ink">
                  {t('Clips Kitty is free & open source ❤️')}
                </p>
                <p className="text-sm text-ink/80 mt-0.5">
                  {t('It runs on your PC with no fees. Donations cover development.')}
                </p>
                {/* Mirrors the footnote opposite, so the two cards are the
                    same height and both buttons sit on the same line. */}
                <p className="text-xs text-ink/70 mt-1.5">
                  {t('Any amount, one-off or monthly, through PayPal. No account needed.')}
                </p>
              </div>
              <button
                onClick={() => window.studio.openDonateWindow()}
                className="btn-accent shrink-0 text-lg px-8 py-3.5 font-semibold"
                title={DONATE_URL}
              >
                {t('Donate ❤️')}
              </button>
            </div>
          </div>
        </div>

        <div
          className="shrink-0 flex flex-col"
          style={{ height: shownAssistantH }}
        >
          <div
            role="separator"
            aria-orientation="horizontal"
            aria-label={t('Drag to resize Ask Clips Kitty')}
            tabIndex={0}
            onPointerDown={startResize}
            onPointerMove={onResize}
            onPointerUp={endResize}
            onPointerCancel={endResize}
            onKeyDown={onResizeKey}
            className="h-3 shrink-0 cursor-ns-resize flex items-center justify-center group rounded focus:outline-none focus-visible:ring-1 focus-visible:ring-accent"
          >
            <div className="h-1 w-20 rounded-full bg-raised transition-colors group-hover:bg-accent/70 group-focus:bg-accent" />
          </div>
          <div className="flex-1 min-h-0">
            <Assistant />
          </div>
        </div>
      </div>
    </div>
  )
}