import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { Clip } from '../lib/types'

interface Message {
  role: 'user' | 'assistant'
  text: string
}

/** Chat-driven clip editing: describe the problem in plain language and the
 *  local AI translates it into crop, length, and caption changes, then
 *  re-renders the clip from the original video. */
export default function EditChat({
  clip,
  onQueued
}: {
  clip: Clip
  onQueued: (msg: string) => void
}): JSX.Element {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setMessages([])
    setInput('')
  }, [clip.id])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages])

  const send = async (): Promise<void> => {
    const text = input.trim()
    if (!text || busy) return
    setMessages((m) => [...m, { role: 'user', text }])
    setInput('')
    setBusy(true)
    try {
      const res = await api.aiEdit(clip.id, text)
      setMessages((m) => [...m, { role: 'assistant', text: res.reply }])
      if (res.job_id !== null) onQueued('Edit queued — the clip is re-rendering.')
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: 'assistant', text: `Something went wrong: ${e instanceof Error ? e.message : e}` }
      ])
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="border border-raised/60 rounded-lg p-3 space-y-2">
      <p className="font-medium">
        Ask the AI to fix this clip
        <span className="block text-[10px] text-muted font-normal mt-0.5">
          e.g. “make it 5 seconds longer” · “center the crop” · “yellow captions” · “the caption
          says gost, it should say ghost”
        </span>
      </p>

      {messages.length > 0 && (
        <div ref={scrollRef} className="max-h-48 overflow-y-auto space-y-2 pr-1" role="log">
          {messages.map((m, i) => (
            <p
              key={i}
              className={`text-sm rounded-lg px-3 py-2 ${
                m.role === 'user' ? 'bg-accent/15 text-ink ml-6' : 'bg-raised text-ink mr-6'
              }`}
            >
              {m.text}
            </p>
          ))}
          {busy && (
            <p className="text-xs text-muted mr-6 px-3">
              Thinking… (the local AI can take up to a minute)
            </p>
          )}
        </div>
      )}

      <div className="flex gap-2">
        <input
          className="input flex-1"
          placeholder="Describe what to change…"
          aria-label="Describe what to change about this clip"
          value={input}
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()}
        />
        <button className="btn-accent shrink-0" onClick={send} disabled={busy || !input.trim()}>
          Send
        </button>
      </div>
    </div>
  )
}
