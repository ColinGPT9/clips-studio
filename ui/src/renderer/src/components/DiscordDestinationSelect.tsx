import { useEffect, useState } from 'react'
import { discord, type Destination } from '../lib/campaigns'
import { t } from '../lib/i18n'

/** Optional delivery of live published links; webhooks are added in Settings. */
export default function DiscordDestinationSelect({
  value,
  onChange,
  disabled = false
}: {
  value: number | null
  onChange: (id: number | null) => void
  disabled?: boolean
}): JSX.Element | null {
  const [destinations, setDestinations] = useState<Destination[]>([])
  useEffect(() => {
    let live = true
    void discord.destinations()
      .then((got) => {
        if (live) setDestinations(got.destinations)
      })
      .catch(() => {})
    return () => {
      live = false
    }
  }, [])

  if (!destinations.length) return null
  return (
    <label className="block text-sm space-y-1">
      <span className="label block">{t('Send links to Discord')}</span>
      <select
        className="input"
        value={value ?? ''}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
      >
        <option value="">{t('None')}</option>
        {destinations.map((destination) => (
          <option key={destination.id} value={destination.id}>
            {destination.label}
          </option>
        ))}
      </select>
    </label>
  )
}
