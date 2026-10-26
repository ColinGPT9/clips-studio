import { useState } from 'react'
import { streamWorkflows, twitchVodId, type RecordedNominationStream } from '../lib/connectedAccounts'
import { t } from '../lib/i18n'

function timestamp(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  return `${Math.floor(total / 3600)}:${String(Math.floor(total / 60) % 60).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

export default function RecordingBindingCard({ accountId, stream, disabled, onSaved }: {
  accountId: string
  stream: RecordedNominationStream
  disabled: boolean
  onSaved: () => Promise<void>
}): JSX.Element {
  const [editing, setEditing] = useState(false)
  const [recording, setRecording] = useState('')
  const [offset, setOffset] = useState('0')
  const [checked, setChecked] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const buckets = stream.timeline ?? []
  const peak = Math.max(1, ...buckets.map((bucket) => bucket.count))
  const first = buckets[0]?.offset_seconds ?? 0
  const span = Math.max(stream.bucket_seconds ?? 30, (buckets.at(-1)?.offset_seconds ?? first) - first + (stream.bucket_seconds ?? 30))
  const vodId = twitchVodId(recording)
  const seconds = Number(offset)
  const validOffset = offset.trim() !== '' && Number.isFinite(seconds) && Math.abs(seconds) <= 31536000

  const save = async (): Promise<void> => {
    if (!vodId || !validOffset || !checked || !stream.stream_start) return
    setSaving(true)
    setError('')
    try {
      await streamWorkflows.bindRecording(accountId, {
        broadcaster_id: stream.broadcaster_id, vod_id: vodId, stream_id: stream.stream_id,
        stream_start: stream.stream_start, offset_seconds: seconds, verified: true
      })
      setEditing(false)
      setChecked(false)
      await onSaved()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : t('The recording could not be bound. Try again.'))
    } finally {
      setSaving(false)
    }
  }

  const checkRecording = (): void => {
    if (!vodId || !validOffset) return
    const at = Math.max(0, Math.floor(first + seconds))
    void window.studio?.openExternal(`https://www.twitch.tv/videos/${vodId}?t=${Math.floor(at / 3600)}h${Math.floor(at / 60) % 60}m${at % 60}s`)
  }

  const retry = async (): Promise<void> => {
    setSaving(true)
    setError('')
    try {
      await streamWorkflows.retryRecording(accountId, stream.stream_id)
      await onSaved()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : t('The recording lookup could not be restarted.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-2 rounded border border-raised p-2">
      <p className="text-xs text-muted">
        {t('Stream')} {stream.stream_id}
        {stream.stream_start ? ` · ${new Date(stream.stream_start).toLocaleString()}` : ''}
        {' · '}{stream.ended_at ? t('Ended') : t('No end recorded')}
        {' · '}{t('Nominations')}: {stream.nomination_count}
      </p>
      {stream.commands && (
        <p className="text-xs text-muted">!clip {stream.commands.clip ?? 0} · !chaos {stream.commands.chaos ?? 0} · !funny {stream.commands.funny ?? 0}</p>
      )}
      {buckets.length > 0 && (
        <div>
          <div className="relative h-10 rounded bg-raised" role="img" aria-label={t('Viewer nominations over time in 30-second intervals')}>
            {buckets.map((bucket) => (
              <span key={bucket.offset_seconds} className="absolute bottom-0 rounded-t bg-accent"
                style={{ left: `${(bucket.offset_seconds - first) / span * 100}%`, width: `${Math.max(0.3, (stream.bucket_seconds ?? 30) / span * 90)}%`, height: `${Math.max(5, bucket.count / peak * 100)}%` }}
                title={`${timestamp(bucket.offset_seconds)} · ${bucket.count} ${t('nominations')}`} />
            ))}
          </div>
          <p className="flex justify-between text-xs text-muted"><span>{timestamp(first)}</span><span>{timestamp(first + span)}</span></p>
          {stream.timeline_truncated && <p className="text-xs text-muted">{t('Showing the latest 200 intervals; totals include all recorded nominations.')}</p>}
        </div>
      )}
      {stream.mapping && (
        <p className="text-xs text-muted" role="status">
          {t('Recording timing verified.')} {stream.mapping.source_id
            ? t('Viewer nominations are available for the matched source.')
            : t('Viewer nominations will attach when Clips Kitty verifies the complete downloaded recording. Re-run an earlier job to use them.')}
        </p>
      )}
      {!stream.mapping && stream.recording?.status === 'pending' && (
        <p className="text-xs text-muted" role="status">{t('Waiting for Twitch to make this recording available. Checking automatically.')}</p>
      )}
      {!stream.mapping && stream.recording?.status === 'ready' && (
        <p className="text-xs text-muted" role="status">{t('Matching recording found. Check its timing before using nominations.')}</p>
      )}
      {!stream.mapping && stream.recording?.status === 'expired' && (
        <div className="space-y-1">
          <p className="text-xs text-muted">{t('No matching recording was found within six hours. Check that Twitch saved your broadcast, retry, or enter its URL below.')}</p>
          <button className="btn-ghost !py-1 text-xs" disabled={disabled || saving} onClick={() => void retry()}>{t('Look for recording again')}</button>
        </div>
      )}
      {stream.ended_at && stream.stream_start && !editing && (
        <button className="btn-ghost !py-1 text-xs" disabled={disabled || saving}
          onClick={() => {
            const found = stream.mapping?.vod_id ?? stream.recording?.vod_id
            setRecording(found ? `https://www.twitch.tv/videos/${found}` : '')
            setOffset(String(stream.mapping?.offset_seconds ?? 0))
            setChecked(false)
            setError('')
            setEditing(true)
          }}>{stream.mapping ? t('Check recording alignment') : t('Bind recording')}</button>
      )}
      {editing && (
        <form className="space-y-2" onSubmit={(event) => { event.preventDefault(); void save() }}>
          <label className="block text-xs">{t('Twitch recording URL')}
            <input className="input mt-1 w-full" value={recording} disabled={saving || disabled}
              placeholder="https://www.twitch.tv/videos/..." onChange={(event) => { setRecording(event.target.value); setChecked(false) }} required />
          </label>
          <label className="block text-xs">{t('Recording timing offset (seconds)')}
            <input className="input mt-1 w-full" type="number" step="any" value={offset} disabled={saving || disabled}
              onChange={(event) => { setOffset(event.target.value); setChecked(false) }} required />
          </label>
          <p className="text-xs text-muted">{t('Use the whole Twitch recording. Start at zero and check a nomination against your stream clock. If the recording begins 12 seconds after the stream, enter -12.')}</p>
          <button type="button" className="btn-ghost !py-1 text-xs" disabled={!vodId || !validOffset || saving || disabled}
            onClick={checkRecording}>{t('Open recording at a nomination')}</button>
          <label className="flex items-start gap-2 text-xs">
            <input type="checkbox" checked={checked} disabled={saving || disabled} onChange={(event) => setChecked(event.target.checked)} />
            {t('I checked the recording against the stream clock and this timing is correct.')}
          </label>
          <div className="flex gap-2">
            <button type="submit" className="btn-accent !py-1 text-xs" disabled={!vodId || !validOffset || !checked || saving || disabled}>
              {saving ? t('Saving…') : t('Save verified alignment')}
            </button>
            <button type="button" className="btn-ghost !py-1 text-xs" disabled={saving} onClick={() => setEditing(false)}>{t('Cancel')}</button>
          </div>
        </form>
      )}
      {error && <p className="text-xs text-warn" role="alert">{error}</p>}
    </div>
  )
}
