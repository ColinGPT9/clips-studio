import { t } from '../lib/i18n'
import { COMING_SOON, HIGHLIGHT_HINTS, REELS, SPORT_ICONS, fitSport, rememberSport, sportName } from '../lib/sports'
import type { SportChoice, SportOption } from '../lib/types'
import ExplainedSelect from './ExplainedSelect'

/** The Sports toggle's choices, under a video or a watched channel: which
 *  sport, which moments to keep, the period when the sport offers it
 *  (basketball's quarters), who to favour and the story reels to make.
 *  Each explains itself in a tooltip. The match's own events are typed in Ask
 *  Clips Kitty. The whole match is always clipped, and a club recording is
 *  told apart from a TV broadcast by itself. Rendered into the caller's row,
 *  the way Longform's output choice is. */
export default function SportFields({
  value,
  sports,
  onChange,
  remember = false,
  name
}: {
  value: SportOption
  sports: SportChoice[]
  onChange: (next: SportOption) => void
  /** Keep the choice for the next video (the Generate list does). */
  remember?: boolean
  /** Tells the controls apart when there are several rows ("video 2"). */
  name?: string
}): JSX.Element {
  const sport = sports.find((s) => s.id === value.name)
  const suffix = name ? ` ${name}` : ''
  const set = (change: Partial<SportOption>): void => {
    const next = fitSport({ ...value, ...change }, sports)
    if (!next) return
    if (remember) rememberSport(next)
    onChange(next)
  }
  const highlights = (sport?.highlights ?? [])
    .filter((h) => h.id !== 'custom')
    .map((h) => ({ id: h.id, label: t(h.label), hint: HIGHLIGHT_HINTS[h.id] ? t(HIGHLIGHT_HINTS[h.id]) : '' }))
  return (
    <>
      <span className="label shrink-0">{t('Sport')}</span>
      <select
        className="input !w-48"
        value={value.name}
        onChange={(e) => set({ name: e.target.value, highlights: undefined, period: undefined })}
        aria-label={`${t('Sport')}${suffix}`}
      >
        {sports.map((s) => (
          <option key={s.id} value={s.id}>
            {SPORT_ICONS[s.id] ? `${SPORT_ICONS[s.id]} ` : ''}
            {t(sportName(s))}
          </option>
        ))}
        {COMING_SOON.map((s) => (
          <option key={s.label} value="" disabled>
            {`${s.icon} ${t(s.label)} (${t('coming soon')})`}
          </option>
        ))}
      </select>
      <span className="label shrink-0">{t('Highlights')}</span>
      <ExplainedSelect
        className="w-56"
        value={value.highlights ?? ''}
        options={highlights}
        onChange={(id) => set({ highlights: id })}
        label={`${t('Highlights')}${suffix}`}
      />
      {sport?.period_menu && (
        <>
          <span className="label shrink-0">{t(sport.period_menu)}</span>
          <select
            className="input !w-40"
            value={value.period ?? 'full'}
            onChange={(e) => set({ period: e.target.value })}
            aria-label={`${t(sport.period_menu)}${suffix}`}
            title={t('Only the moments of this part of the game. A moment whose part couldn’t be read off the score bug is kept, and the clip page says so.')}
          >
            {sport.periods.map((p) => (
              <option key={p.id} value={p.id}>
                {t(p.label)}
              </option>
            ))}
          </select>
        </>
      )}
      <input
        className="input !w-56 max-w-full"
        placeholder={t('Teams or players (optional)')}
        aria-label={`${t('Teams or players')}${suffix}`}
        maxLength={200}
        value={value.teams ?? ''}
        onChange={(e) => set({ teams: e.target.value })}
        title={t('Clips where the commentary names them get extra points. Nothing is left out for it, and nobody is guessed. With Player reels, a name the commentary says in two moments or more gets a reel of its own.')}
      />
      <span className="label shrink-0">{t('Also make')}</span>
      {REELS.map((reel) => (
        <label key={reel.id} className="flex items-center gap-1.5 text-sm cursor-pointer shrink-0" title={t(reel.title)}>
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            aria-label={`${t(reel.label)}${suffix}`}
            checked={(value.reels ?? []).includes(reel.id)}
            onChange={(e) => {
              const others = (value.reels ?? []).filter((id) => id !== reel.id)
              set({ reels: e.target.checked ? [...others, reel.id] : others })
            }}
          />
          {t(reel.label)}
        </label>
      ))}
    </>
  )
}
