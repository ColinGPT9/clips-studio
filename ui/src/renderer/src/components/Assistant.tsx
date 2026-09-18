import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import type { PublishPlanItem } from '../lib/types'

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
 *  It cannot publish. A plan comes back as a proposal with a button under it,
 *  and only that button uploads anything: thirty videos cannot be
 *  un-uploaded, so the confirmation is part of the design rather than
 *  something the model is trusted to ask for.
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
    setTurns((prior) => [...prior, { role: 'user', text }])
    setBusy(true)
    try {
      // Only the words go back, not the tool traffic: the model re-reads its
      // own answers, and feeding it every raw tool result again would fill
      // the context with things it already summarised.
      const history = turns.map((turn) => ({ role: turn.role, content: turn.text }))
      const res = await api.agentChat(text, history)
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
    <section className="card flex flex-col" aria-label={t('Assistant')}>
      <div className="flex items-baseline justify-between">
        <p className="font-semibold">{t('Ask Clips Kitty')}</p>
        {model && <span className="text-[11px] text-muted tabular-nums">{model}</span>}
      </div>
      <p className="text-[11px] text-muted mt-0.5">
        {t('e.g. "clip this stream, then plan the best three an hour apart from tomorrow noon"')}
      </p>

      {turns.length > 0 && (
        <div ref={scrollRef} className="mt-2 max-h-72 overflow-y-auto space-y-2 pr-1">
          {turns.map((turn, i) => (
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
                    <li key={j} className="text-[11px] text-muted font-mono">
                      {step.tool}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}

      {plan && (
        <div className="mt-2 border border-accent/40 rounded-lg p-2 space-y-1">
          <p className="text-xs font-medium">
            {plan.length} {t('clips ready to upload. Nothing has been uploaded yet.')}
          </p>
          {plan.slice(0, 6).map((item) => (
            <p key={item.clip_id} className="text-[11px] text-muted">
              {item.title.slice(0, 48)} · {item.publish_at || t('as soon as it uploads')}
            </p>
          ))}
          {planNote && <p className="text-[11px] text-warn">{planNote}</p>}
          <div className="flex gap-2 pt-1">
            <button className="btn-accent !py-1 text-xs" disabled={busy} onClick={confirmPlan}>
              {t('Upload these')}
            </button>
            <button
              className="btn-ghost !py-1 text-xs"
              disabled={busy}
              onClick={() => setPlan(null)}
            >
              {t('Cancel')}
            </button>
          </div>
        </div>
      )}

      <div className="flex gap-2 mt-2">
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
