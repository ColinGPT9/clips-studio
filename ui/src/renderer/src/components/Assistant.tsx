import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import type { PublishPlanItem } from '../lib/types'
import { seedOptions } from './queue/AddVideos'

interface Turn {
  role: 'user' | 'assistant'
  text: string
  steps?: { tool: string; result: string }[]
}

/** Ask Clips Kitty to do things in plain language.
 *
 *  A local model reads the request and calls the app's own tools: find a
 *  video, list its clips, plan uploads. It is the same tool list the MCP
 *  server gives an outside agent, so there is one set of descriptions to keep
 *  honest rather than two.
 *
 *  It CAN publish, since 2026-09-22. It used to be withheld, which meant
 *  "process this video and publish them all" got the processing and silence
 *  about the rest — worse than either doing it or refusing. It still plans
 *  first, so a proposal with a button under it is the usual path, and a
 *  publish only happens when the request asked for one.
 */
export default function Assistant(): JSX.Element | null {
  const [ready, setReady] = useState<boolean | null>(null)
  const [model, setModel] = useState('')
  const [reason, setReason] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [plan, setPlan] = useState<PublishPlanItem[] | null>(null)
  const [planNote, setPlanNote] = useState('')
  // A social-platform schedule is a different plan from a YouTube one:
  // different confirm, different wording. Null means the YouTube kind.
  const [schedule, setSchedule] = useState<{
    platforms: string[]
    every_hours: number
    hashtags: string[]
  } | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api
      .agentStatus()
      .then((s) => {
        setReady(s.ready)
        setModel(s.model)
        setReason(s.reason)
      })
      .catch(() => setReady(false))
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [turns, busy])

  const send = async (): Promise<void> => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    setPlan(null)
    setPlanNote('')
    setSchedule(null)
    setTurns((prior) => [...prior, { role: 'user', text }])
    setBusy(true)
    try {
      // Only the words go back, not the tool traffic: the model re-reads its
      // own answers, and feeding it every raw tool result again would fill
      // the context with things it already summarised.
      const history = turns.map((turn) => ({ role: turn.role, content: turn.text }))
      // The Generate bar's settings travel with the message. Without
      // them a job asked for in the chat ignored every toggle above it:
      // captions came back burned in however often the box was unticked,
      // because that choice lives in this window and the model never
      // saw it.
      const res = await api.agentChat(text, history, seedOptions())
      setTurns((prior) => [
        ...prior,
        {
          role: 'assistant',
          text: res.reply || t('Done.'),
          steps: res.steps?.map((s) => ({ tool: s.tool, result: s.result }))
        }
      ])
      if (res.plan?.items?.length) {
        setPlan(res.plan.items)
        setPlanNote((res.plan.warnings || []).join(' '))
        const p = res.plan as unknown as {
          provider?: string
          platforms?: string[]
          every_hours?: number
          hashtags?: string[]
        }
        setSchedule(
          p.provider === 'woopsocial'
            ? {
                platforms: p.platforms || ['youtube'],
                every_hours: p.every_hours || 0,
                hashtags: p.hashtags || []
              }
            : null
        )
      }
    } catch (e) {
      setTurns((prior) => [
        ...prior,
        { role: 'assistant', text: String(e).replace(/^Error:\s*/, '') }
      ])
    } finally {
      setBusy(false)
    }
  }

  const confirmPlan = async (): Promise<void> => {
    if (!plan || busy) return
    setBusy(true)
    try {
      // A social-platform schedule goes through the batch route; a YouTube
      // plan through the one it has always used.
      if (schedule) {
        const res = await api.woopSocialBatch({
          clip_ids: plan.map((i) => i.clip_id),
          platforms: schedule.platforms,
          every_hours: schedule.every_hours
        })
        const parts = [`${t('Scheduled')} ${res.started.length}.`]
        for (const row of res.skipped) {
          parts.push(`${t('Skipped clip')} ${row.clip_id}: ${row.reason}`)
        }
        setTurns((prior) => [...prior, { role: 'assistant', text: parts.join(' ') }])
        setPlan(null)
        setSchedule(null)
        return
      }
      const res = await api.executePublishPlan(plan)
      const parts = [`${t('Uploading')} ${res.started.length}.`]
      for (const row of res.skipped) parts.push(`${t('Skipped clip')} ${row.clip_id}: ${row.reason}`)
      setTurns((prior) => [...prior, { role: 'assistant', text: parts.join(' ') }])
      setPlan(null)
    } catch (e) {
      setPlanNote(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  if (ready === false) {
    return (
      <section className="card" aria-label={t('Assistant')}>
        <p className="font-semibold">{t('Ask Clips Kitty')}</p>
        <p className="text-xs text-muted mt-1">{reason || t('No model available.')}</p>
      </section>
    )
  }

  return (
    <section
      className="card flex flex-col h-full min-h-0 overflow-hidden"
      aria-label={t('Assistant')}
    >
      <div className="flex items-baseline justify-between shrink-0">
        <p className="font-semibold">{t('Ask Clips Kitty')}</p>
        {model && <span className="text-[11px] text-muted tabular-nums">{model}</span>}
      </div>
      <div ref={scrollRef} className="mt-3 flex-1 min-h-0 overflow-y-auto space-y-3 pr-1">
        {turns.length === 0 ? (
          /* Examples rather than a paragraph. The paragraph that was here got
             cut in half whenever the panel was dragged short, and a wall of
             prose is the wrong shape for an empty state anyway: nobody has to
             invent the phrasing if they can start from a real one. Clicking
             fills the box instead of sending, so it can be edited first. */
          <div className="flex flex-wrap gap-1.5 items-baseline">
            <span className="text-sm text-muted mr-0.5">{t('Try:')}</span>
            {[
              t('Clip this stream: '),
              t('Publish all my clips, 5 a day'),
              t('My best clips this week')
            ].map((example) => (
              <button
                key={example}
                onClick={() => setInput(example)}
                className="text-xs px-2 py-1 rounded-full border border-raised text-muted hover:border-accent hover:text-ink transition-colors"
              >
                {example.trim()}
              </button>
            ))}
          </div>
        ) : (
          turns.map((turn, i) => (
            <div key={i} className={turn.role === 'user' ? 'text-right' : ''}>
              <p
                className={
                  turn.role === 'user'
                    ? 'inline-block bg-raised rounded-lg px-2.5 py-1 text-sm text-left'
                    : 'text-sm whitespace-pre-wrap'
                }
              >
                {turn.text}
              </p>
              {turn.steps && turn.steps.length > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {turn.steps.map((step, j) => (
                    <li key={j} className="text-xs text-muted font-mono">
                      {step.tool}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))
        )}
      </div>

      {plan && (
        // The plan gives way rather than growing: the card clips what does not
        // fit, and what sits below it is the input.
        <div className="mt-2 border border-accent/40 rounded-lg p-2 space-y-1 min-h-0 overflow-y-auto">
          <p className="text-xs font-medium">
            {schedule
              ? `${plan.length} ${t('clips ready to post to')} ${schedule.platforms.join(', ')}${
                  schedule.every_hours
                    ? `, ${t('one every')} ${schedule.every_hours}h`
                    : ''
                }. ${t('Nothing has been posted yet.')}`
              : `${plan.length} ${t('clips ready to upload. Nothing has been uploaded yet.')}`}
          </p>
          {schedule && schedule.hashtags.length > 0 && (
            <p className="text-[11px] text-muted">
              {t('Adding')} {schedule.hashtags.map((h) => `#${h}`).join(' ')}
            </p>
          )}
          {plan.slice(0, 6).map((item) => (
            <p key={item.clip_id} className="text-[11px] text-muted">
              {item.title.slice(0, 48)} · {item.publish_at || t('as soon as it uploads')}
            </p>
          ))}
          {planNote && <p className="text-[11px] text-warn">{planNote}</p>}
          <div className="flex gap-2 pt-1">
            <button className="btn-accent !py-1 text-xs" disabled={busy} onClick={confirmPlan}>
              {schedule ? t('Schedule these') : t('Upload these')}
            </button>
            <button
              className="btn-ghost !py-1 text-xs"
              disabled={busy}
              onClick={() => { setPlan(null); setSchedule(null) }}
            >
              {t('Cancel')}
            </button>
          </div>
        </div>
      )}

      <div className="flex gap-2 mt-3 shrink-0">
        <input
          className="input flex-1"
          value={input}
          disabled={busy || ready === null}
          placeholder={ready === null ? t('Checking…') : t('Ask for something…')}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void send()
          }}
        />
        <button className="btn-accent !py-1" disabled={busy || !input.trim()} onClick={send}>
          {busy ? t('Working…') : t('Ask')}
        </button>
      </div>
    </section>
  )
}
