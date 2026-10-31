import { useCallback, useEffect, useState } from 'react'
import { api, errorText } from '../lib/api'
import { textUpdates } from '../lib/textUpdates'
import type { ClipUpdatesStatus } from '../lib/textUpdates'
import { useEvents } from '../lib/useEvents'

export default function ClipUpdatesCard(): JSX.Element {
  const [status, setStatus] = useState<ClipUpdatesStatus | null>(null)
  const [token, setToken] = useState('')
  const [models, setModels] = useState<string[]>([])
  const [channels, setChannels] = useState<{id:string;name:string;guild:string}[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const refresh = useCallback(async (): Promise<void> => { setStatus(await textUpdates.status()) }, [])
  useEffect(() => {
    void refresh().catch(() => {})
    void api.models().then(info => setModels(info.installed.map(model => model.name))).catch(() => {})
  }, [refresh])
  useEvents(event => { if (event.type === 'automation') void refresh().catch(() => {}) })
  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    setBusy(true); setError('')
    try { await fn(); await refresh() } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const patch = (value: Partial<ClipUpdatesStatus>): void => { void act(() => textUpdates.settings(value)) }
  return <section className="card space-y-4" aria-label="Clip updates">
    <h2 className="font-semibold">Clip updates</h2>
    <p className="text-sm text-muted">Approve clips in the app, or use your private Discord channel when you are away. Off by default.</p>
    <label className="flex gap-2"><input type="checkbox" checked={status?.enabled ?? false} disabled={busy || !status}
      onChange={event => patch({enabled:event.target.checked})} />Enable Clip updates</label>
    {status?.enabled && <>
      {!status.owner_connected && <p className="text-warn">Sign in under Settings → Discord first.</p>}
      <p role="status"><span aria-hidden="true" className={status.connected ? 'text-green-500' : 'text-muted'}>●</span> {status.connected ? 'Bot connected' : 'Bot disconnected'}</p>
      <label className="flex gap-2"><input type="checkbox" checked={status.away_from_desk} disabled={busy || (!status.away_from_desk && !(status.connected && status.test_ok))}
        onChange={event => patch({away_from_desk:event.target.checked})} />Away from desk — ask before every post</label>
      <label className="flex gap-2"><input type="checkbox" checked={status.show_dmca} disabled={busy}
        onChange={event => patch({show_dmca:event.target.checked})} />Show the Damage Control joke button</label>
      <label className="block text-sm">Written by: Gemma (local)
        <select className="input w-full mt-1" aria-label="Clip updates local model" value={status.model} disabled={busy}
          onChange={event => patch({model:event.target.value})}>
          <option value="">Use the app’s local model, or an installed Gemma</option>
          {models.map(model => <option key={model} value={model}>{model}</option>)}
        </select>
      </label>
      <p className="text-xs text-muted">{status.model_status}. The plain messages and buttons work without a model.</p>
      <label className="block text-sm">Discord bot token
        <input className="input w-full mt-1" type="password" autoComplete="off" value={token}
          placeholder={status.has_token ? 'Stored securely — enter a replacement' : 'Paste your bot token'}
          onChange={event => setToken(event.target.value)} />
      </label>
      <div className="flex gap-2 flex-wrap">
        <button className="btn-ghost" disabled={busy || !token.trim()} onClick={() => void act(async () => {await textUpdates.bot(token.trim());setToken('')})}>Save bot token</button>
        <button className="btn-ghost" onClick={() => void window.studio?.openExternal(status.invite_url)}>Invite bot</button>
        <button className="btn-ghost" disabled={busy || !status.has_token} onClick={() => void act(() => textUpdates.disconnect())}>Remove bot token</button>
      </div>
      <p className="text-xs text-muted">In the Developer Portal, enable Message Content Intent for typed replies. Buttons also work without it. Leave Interactions Endpoint URL empty.</p>
      <label className="flex gap-2"><input type="checkbox" checked={status.dm} disabled={busy} onChange={event => patch({dm:event.target.checked})} />Send to my DMs</label>
      {!status.dm && <div className="flex gap-2">
        <select className="input min-w-0 flex-1" aria-label="Clip updates channel" value={status.channel_id} disabled={busy}
          onChange={event => patch({channel_id:event.target.value})}>
          <option value="">Choose a private channel</option>
          {channels.map(channel => <option key={channel.id} value={channel.id}>{channel.guild} · {channel.name}</option>)}
        </select>
        <button className="btn-ghost" disabled={busy || !status.connected} onClick={() => void act(async () => setChannels(await textUpdates.channels()))}>Load channels</button>
      </div>}
      <button className="btn-accent" disabled={busy || !status.connected} onClick={() => void act(() => textUpdates.test())}>Send test</button>
      {status.test_ok && <p className="text-xs">Test delivered. Away from desk can now be enabled.</p>}
      <fieldset className="space-y-1 text-sm"><legend>Discord updates</legend>
        {['ready','scheduled','live','run_failed','publish_failed','rejected'].map(event => <label className="flex gap-2" key={event}>
          <input type="checkbox" disabled={busy} checked={status.events.includes(event)} onChange={e => patch({events:e.target.checked ? [...status.events,event] : status.events.filter(value => value !== event)})} />
          {{ready:'Clips ready',scheduled:'Scheduled',live:'Posted',run_failed:'Processing failed',publish_failed:'Publishing failed',rejected:'Post rejected'}[event]}
        </label>)}
      </fieldset>
      {status.last_error && <p role="alert" className="text-warn">{status.last_error}</p>}
    </>}
    {error && <p className="text-danger" role="alert">{error}</p>}
  </section>
}
