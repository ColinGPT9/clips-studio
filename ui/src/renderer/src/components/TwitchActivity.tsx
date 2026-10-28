import { useEffect, useState } from 'react'
import { connectedAccounts } from '../lib/connectedAccounts'
import { t } from '../lib/i18n'

/** Observe the existing collector. Reading activity never starts a listener. */
export default function TwitchActivity({ accountId, broadcasterId }: {
  accountId: string
  broadcasterId: string
}): JSX.Element {
  const [state, setState] = useState({ status: 'connecting', messages: 0, truncated: false })
  useEffect(() => {
    let stopped = false
    let cursor = 0
    let messages = 0
    let truncated = false
    let timer: ReturnType<typeof setTimeout> | undefined
    setState({ status: 'connecting', messages: 0, truncated: false })
    const poll = async (): Promise<void> => {
      try {
        const batch = await connectedAccounts.events(accountId, cursor)
        if (stopped) return
        if (batch.broadcaster_id === broadcasterId) {
          messages += batch.events.filter((event) => event.sequence > cursor &&
            event.broadcaster_id === broadcasterId && event.type === 'channel.chat.message').length
          cursor = Math.max(cursor, batch.cursor)
          truncated ||= Boolean(batch.truncated)
          setState({ status: batch.status, messages, truncated })
        } else setState({ status: 'connecting', messages: 0, truncated: false })
      } catch {
        if (stopped) return
        // A restarted backend has a new cursor space. Begin a fresh activity count.
        cursor = 0
        messages = 0
        truncated = false
        setState({ status: 'retrying', messages: 0, truncated: false })
      }
      if (!stopped) timer = setTimeout(() => { void poll() }, 2500)
    }
    void poll()
    return () => { stopped = true; if (timer) clearTimeout(timer) }
  }, [accountId, broadcasterId])
  const label = state.status === 'connected' ? t('Listening to Twitch')
    : state.status === 'retrying' || state.status === 'reconnecting' ? t('Reconnecting to Twitch')
      : state.status === 'stopped' ? t('Listener stopped')
        : state.status === 'reconnect_required' ? t('Reconnect your Twitch account')
          : t('Connecting to Twitch')
  return (
    <div className="rounded bg-raised p-2 space-y-1" aria-label={t('Live Twitch activity')}>
      <p className="text-xs font-medium" role="status">{label}</p>
      <p className="text-xs">{t('Chat messages received in this view')}: {state.messages}</p>
      {state.truncated && <p className="text-xs text-muted">{t('Some earlier messages were outside the recent event buffer.')}</p>}
    </div>
  )
}
