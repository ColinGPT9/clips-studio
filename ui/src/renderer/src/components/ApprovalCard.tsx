import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { api, API_BASE, errorText } from '../lib/api'
import { textUpdates } from '../lib/textUpdates'
import type { ClipUpdatesStatus, PendingApproval } from '../lib/textUpdates'
import { useEvents } from '../lib/useEvents'
import { notifyApproval } from '../lib/queueNotifications'

const Context = createContext<{status: ClipUpdatesStatus | null; pending: PendingApproval[]; refresh: () => Promise<void>}>({status:null,pending:[],refresh:async()=>{}})
export const useClipUpdates = () => useContext(Context)

export function ClipUpdatesProvider({children}: {children: ReactNode}): JSX.Element {
  const [status, setStatus] = useState<ClipUpdatesStatus | null>(null)
  const [pending, setPending] = useState<PendingApproval[]>([])
  const seen = useRef(new Set<number>())
  const refresh = useCallback(async (): Promise<void> => {
    try {
      const [next, items] = await Promise.all([textUpdates.status(), textUpdates.pending()])
      setStatus(next)
      setPending(Array.isArray(items) ? items : [])
      for (const item of Array.isArray(items) ? items : []) {
        if (!item.answer && !seen.current.has(item.item_id)) {
          seen.current.add(item.item_id)
          notifyApproval(item.item_id, next.show_dmca)
        }
      }
    } catch { /* Optional feature: a disconnected engine doesn't disturb the app. */ }
  }, [])
  useEffect(() => {
    void refresh()
    const timer = setInterval(() => void refresh(), 30000)
    return () => clearInterval(timer)
  }, [refresh])
  useEvents(event => { if (event.type === 'automation') void refresh() })
  return <Context.Provider value={{status,pending,refresh}}>{children}</Context.Provider>
}

export function ApprovalActions({itemId, showDMCA, onChanged, disabled = false}: {
  itemId: number; showDMCA: boolean; onChanged?: () => void; disabled?: boolean
}): JSX.Element {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [jokeOpen, setJokeOpen] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  const decide = async (approve: boolean): Promise<void> => {
    if (timer.current) clearTimeout(timer.current)
    setJokeOpen(false)
    setBusy(true)
    try {
      const result = await (approve ? api.publishWatchItem(itemId) : api.skipWatchItem(itemId))
      const state = result.publish_state
      setMessage(state === 'done' ? `#${itemId} is already scheduled or posted.` : state === 'off'
        ? `⛔ Denied. Skipped #${itemId}. The clips stay in your library.`
        : state === 'publishing' ? `✅ Approved. Posting #${itemId}…` : `#${itemId} is ${state || result.status}.`)
      onChanged?.()
    } catch (error) { setMessage(errorText(error)); setBusy(false) }
  }
  const joke = (): void => {
    if (timer.current) clearTimeout(timer.current)
    setMessage('⚖️ Putting you in contact with a lawyer… 📞 Ringing…')
    setJokeOpen(true)
    timer.current = setTimeout(() => setMessage(`⚖️ Delete all VODs and clips from the stream. Contact your attorney to DMCA any clip someone posted online because you don’t like it. Nothing was filed or deleted. #${itemId} is still waiting: Approve or Deny.`), 2000)
  }
  return <div className="space-y-2">
    <div className="flex gap-2 flex-wrap">
      <button className="btn-accent !bg-green-700 !text-white" disabled={busy || disabled} onClick={() => void decide(true)}>Approve</button>
      <button className="btn-accent !bg-red-700 !text-white" disabled={busy || disabled} onClick={() => void decide(false)}>Deny</button>
      {showDMCA && <button className="btn-ghost" disabled={busy || disabled} onClick={joke}>Damage Control</button>}
    </div>
    {message && !jokeOpen && <p className="text-xs" role="status">{message}</p>}
    {jokeOpen && createPortal(<AttorneyPopup message={message} onClose={() => {
      if (timer.current) clearTimeout(timer.current)
      setJokeOpen(false); setMessage('')
    }} />, document.body)}
  </div>
}

function AttorneyPopup({message, onClose}: {message: string; onClose: () => void}): JSX.Element {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  return <dialog ref={dialog} aria-label="Contact your attorney" onCancel={onClose}
    className="card m-auto max-w-md p-6 text-ink bg-surface backdrop:bg-black/60 space-y-4">
    <h2 className="font-semibold text-lg">⚖️ Contact your attorney</h2>
    <p role="status">{message}</p>
    <button className="btn-accent" autoFocus onClick={onClose}>Close</button>
  </dialog>
}

function ApprovalCard({item, onOpen}: {item: PendingApproval; onOpen: (videoId: string) => void}): JSX.Element | null {
  const {status, refresh} = useClipUpdates()
  const [closed, setClosed] = useState(false)
  useEffect(() => {
    if (!item.answer) return
    const timer = setTimeout(() => setClosed(true), 3000)
    return () => clearTimeout(timer)
  }, [item.answer])
  if (closed) return null
  const place = item.answered_where === 'discord' ? 'on Discord' : 'in the app'
  return <article className="card border border-accent/50 shadow-xl space-y-3" data-clip-approval tabIndex={-1}>
    <h2 className="font-semibold">🐱 Clips Kitty · #{item.item_id}</h2>
    {item.thumbnail_url && <img className="max-h-28 rounded" src={`${API_BASE}${item.thumbnail_url}`} alt="Clip preview" />}
    <p>{item.line}</p>
    <p className="text-xs text-muted">{item.platforms.join(', ')}{item.first_slot && ` · First slot: ${new Date(item.first_slot).toLocaleString()}`}</p>
    {item.answer ? <p role="status">{item.state_text || (item.answer === 'approve' ? `✅ Approved ${place}…` : `⛔ Denied ${place}. The clips stay in your library.`)}</p>
      : <ApprovalActions itemId={item.item_id} showDMCA={status?.show_dmca ?? item.show_dmca} onChanged={() => void refresh()} />}
    <button className="text-xs text-accent underline" onClick={() => onOpen(item.video_id)}>Open in editor</button>
  </article>
}

export function ApprovalCards({onOpen}: {onOpen: (videoId: string) => void}): JSX.Element | null {
  const {status, pending} = useClipUpdates()
  useEffect(() => window.studio?.onClipApproval?.(() => {
    const card = document.querySelector<HTMLElement>('[data-clip-approval]')
    card?.scrollIntoView({block:'nearest'})
    card?.focus()
  }), [])
  if (!status?.enabled || !pending.length) return null
  return <section aria-label="Clip approvals" className="fixed right-5 bottom-5 z-50 w-96 max-w-[90vw] max-h-[80vh] overflow-y-auto space-y-3">
    {pending.map(item => <ApprovalCard key={item.item_id} item={item} onOpen={onOpen} />)}
  </section>
}

export function AwayToggle(): JSX.Element | null {
  const {status, refresh} = useClipUpdates()
  const [error, setError] = useState('')
  if (!status?.enabled) return null
  return <div className="text-sm">
    <label className="flex gap-2 items-center"><input type="checkbox" checked={status.away_from_desk}
      disabled={!status.away_from_desk && !(status.connected && status.test_ok)}
      onChange={event => void textUpdates.settings({away_from_desk:event.target.checked}).then(refresh).catch(e=>setError(errorText(e)))} />Away from desk</label>
    {error && <p role="alert" className="text-danger">{error}</p>}
  </div>
}
