import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import {
  CATEGORY_LABELS,
  DIRECTORY_KINDS,
  RELATIONSHIP_LABELS,
  basedOnLines,
  catalogBadges,
  checkLines,
  KIND_LABELS,
  categoryLabel,
  compatibilityText,
  confirmations,
  entryLink,
  groupBySection,
  metricLines,
  executionBadge,
  fetchedText,
  fitSummary,
  formatBytes,
  indexName,
  listingLinks,
  needLines,
  slugLabel,
  tierBadge,
  updateLines,
  gitSource,
  type Badge,
  type BasedOn,
  type BuiltinPlugin,
  type CatalogEntry,
  type CatalogResponse,
  type Counting,
  type Hardware,
  type InstalledPlugin,
  type LinkItem,
  type Listing,
  type MarketplaceResponse,
  type ModelPlan,
  type ModelStatus,
  type ModelsOverview,
  type Metrics,
  type PluginDetails,
  type PluginPlan,
  type PluginsResponse,
  type ProblemHere,
  type Requirements,
  type Tone
} from '../lib/marketplace'
import {
  OPEN_MARKETPLACE,
  PLUGINS_CHANGED,
  canManage,
  plugins,
  takeMarketplaceTab,
  type MarketplaceTab,
  type PluginSource
} from '../lib/plugins'

/** The Marketplace: find community pipelines in the registry indexes, see
 *  what each one does and needs before installing, and manage the ones
 *  installed. Every listing shows its trust tier, where it runs, what it
 *  needs and what it may do, and a pipeline that sends anything off this PC
 *  says so in a warning. Nothing here runs plugin code: installing copies
 *  files, and a pipeline runs only when a video is processed with it. */

type Tab = MarketplaceTab

const TONE: Record<Tone, string> = {
  ok: 'bg-success/15 text-success',
  info: 'bg-accent/15 text-accent',
  warn: 'bg-warn/15 text-warn',
  danger: 'bg-error/15 text-error'
}

const FIT_MARK: Record<string, { mark: string; className: string }> = {
  yes: { mark: '✓', className: 'text-success' },
  no: { mark: '✕', className: 'text-error' },
  unknown: { mark: '?', className: 'text-warn' }
}

function Pill({ badge }: { badge: Badge }): JSX.Element {
  return (
    <span className={`text-xs px-2 py-0.5 rounded whitespace-nowrap ${TONE[badge.tone]}`} title={badge.title}>
      {badge.label}
    </span>
  )
}

/** What this PC has, for the requirement lines. Null until the engine answers. */
function useHardware(): Hardware | null {
  const [hw, setHw] = useState<Hardware | null>(null)
  useEffect(() => {
    let live = true
    api
      .systemStats()
      .then((s) => {
        if (!live) return
        setHw({
          gpu: s.gpu ? { name: s.gpu.name, vram_total: s.gpu.vram_total } : null,
          disk_free_bytes: typeof s.disk_free_bytes === 'number' ? s.disk_free_bytes : null,
          platform: window.studio?.platform && window.studio.platform !== 'browser' ? window.studio.platform : null
        })
      })
      .catch(() => undefined) // shown as "can't tell" until it answers
    return () => {
      live = false
    }
  }, [])
  return hw
}

/** A developer's links. Each opens in the browser only after a dialog that
 *  shows the full address; without the desktop app they are plain text. */
function Links({ links }: { links: LinkItem[] }): JSX.Element | null {
  if (links.length === 0) return null
  const open = window.studio?.openPluginLink
  return (
    <div className="space-y-1">
      <p className="label">{t('Links from the developer')}</p>
      <ul className="text-sm space-y-0.5">
        {links.map((l) => (
          <li key={l.url} className="flex items-center gap-2 min-w-0">
            {open ? (
              <button className="text-accent hover:underline text-left" onClick={() => void open(l.url)} title={l.url}>
                {t(l.label)} ↗
              </button>
            ) : (
              <span>{t(l.label)}</span>
            )}
            <span className="text-xs text-muted truncate select-all">{l.url}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** A link to a project's own page, through the same dialog as a developer's
 *  links; plain text without the desktop app. */
function OutLink({ url, children }: { url: string | null; children: ReactNode }): JSX.Element {
  const open = window.studio?.openPluginLink
  if (!url) return <span>{children}</span>
  return open ? (
    <button className="text-accent hover:underline text-left" onClick={() => void open(url)} title={url}>
      {children} ↗
    </button>
  ) : (
    <span title={url}>{children}</span>
  )
}

/** The projects a plugin builds on, with their licences: the credit their
 *  licences ask for, and what a user should know about where it comes from. */
function BuiltOn({ items }: { items: BasedOn[] | undefined }): JSX.Element | null {
  const lines = basedOnLines(items)
  if (lines.length === 0) return null
  return (
    <div className="text-sm">
      <p className="label mb-1">{t('Built on')}</p>
      <ul className="space-y-0.5">
        {lines.map((b) => (
          <li key={b.name}>
            <OutLink url={b.url}>{b.name}</OutLink> <span className="text-muted">· {t(b.text)}</span>
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted mt-1">
        {t('Each of these keeps its own licence. The developer says how the plugin uses them.')}
      </p>
    </div>
  )
}

/** Each number from its own source, never added together, and where comments live. */
function Numbers({
  metrics,
  discussions,
  at
}: {
  metrics: Metrics | undefined
  discussions?: string
  at?: string | null
}): JSX.Element | null {
  const lines = metricLines(metrics)
  if (lines.length === 0 && !discussions) return null
  return (
    <div className="text-sm">
      <p className="label mb-1">{t('Numbers')}</p>
      <ul className="space-y-0.5">
        {lines.map((l) => (
          <li key={l.text} className={l.tone === 'warn' ? 'text-warn' : ''}>
            {t(l.text)}
          </li>
        ))}
        {discussions && (
          <li>
            <OutLink url={discussions}>{t('Comments and ideas: GitHub Discussions')}</OutLink>
          </li>
        )}
      </ul>
      <p className="text-xs text-muted mt-1">
        {t('Each number comes from its own source and is read for the catalog, not by this app.')}
        {at ? ` ${t('Read on')} ${at}.` : ''}
      </p>
    </div>
  )
}

/** Everything the engine says a plugin will do, in its words. Shown on a
 *  listing's page, before an install and on an installed plugin. */
function DetailsBlock({
  details,
  requirements,
  hardware,
  problems
}: {
  details: PluginDetails
  requirements?: Requirements
  hardware: Hardware | null
  /** What the engine found missing here; null when it wasn't asked. */
  problems: ProblemHere[] | null | undefined
}): JSX.Element {
  const fit = needLines(requirements, hardware, details, problems)
  const exec = executionBadge(details.execution, details.execution_text)
  return (
    <div className="space-y-3 text-sm">
      {details.notice && <p className="text-warn">⚠ {details.notice}</p>}
      <div className="flex flex-wrap gap-2 items-center">
        <Pill badge={tierBadge(details)} />
        <Pill badge={exec} />
      </div>
      {details.execution_text && <p className="text-muted">{details.execution_text}</p>}

      {details.data_warnings.length > 0 && (
        <ul className="space-y-1">
          {details.data_warnings.map((w) => (
            <li key={w} className="text-warn font-medium">
              {w}
            </li>
          ))}
        </ul>
      )}

      <div>
        <p className="label mb-1">{t('What it may do')}</p>
        {details.permissions.length === 0 ? (
          <p className="text-muted">{t('Asks for no permissions.')}</p>
        ) : (
          <ul className="space-y-0.5">
            {details.permissions.map((p) => (
              <li key={p.id}>
                {p.label} <span className="text-xs text-muted">({p.enforcement})</span>
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-muted mt-1">
          {t(
            '“Enforced” means Clips Kitty decides what it hands over. “Declared” means the developer says so and nothing stops the plugin doing more, because it runs with your rights.'
          )}
        </p>
      </div>

      <div>
        <p className="label mb-1">{t('What it needs')}</p>
        {fit.length === 0 ? (
          <p className="text-muted">{t('Nothing stated by the developer.')}</p>
        ) : (
          <ul className="space-y-0.5">
            {fit.map((f) => (
              <li key={f.text}>
                <span className={FIT_MARK[f.fit].className} aria-label={f.fit}>
                  {FIT_MARK[f.fit].mark}
                </span>{' '}
                {t(f.text)}
              </li>
            ))}
          </ul>
        )}
      </div>

      {details.models.length > 0 && (
        <div>
          <p className="label mb-1">{t('Models it uses')}</p>
          <ul className="space-y-0.5">
            {details.models.map((m, i) => (
              <li key={`${m.name ?? m.id}-${i}`}>
                {m.name ?? m.id}
                {m.source && <span className="text-muted"> · {m.source}</span>}
                {m.id && m.name && <span className="text-muted"> · {m.id}</span>}
                {m.revision && <span className="text-muted"> @ {m.revision.slice(0, 10)}</span>}
                {m.license && <span className="text-muted"> · {t('licence')} {m.license}</span>}
                {m.size_bytes ? <span className="text-muted"> · {formatBytes(m.size_bytes)}</span> : null}
                {m.gated && <span className="text-warn"> · {t('needs an account with the model’s host')}</span>}
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted mt-1">
            {t('Once it is installed, its Hugging Face and web models are downloaded from Marketplace › Installed, into one folder every plugin shares. Ollama models are pulled on the Models page.')}
          </p>
        </div>
      )}

      {details.service && (
        <div>
          <p className="label mb-1">{t('Online service')}</p>
          <p>
            {details.service.name}
            {details.service.pricing && <span className="text-muted"> · {details.service.pricing}</span>}
            {details.service.required ? (
              <span className="text-warn"> · {t('required')}</span>
            ) : (
              <span className="text-muted"> · {t('optional')}</span>
            )}
          </p>
          <p className="text-xs text-muted">
            {t('Any payment is between you and the developer. Clips Kitty takes no part in it.')}
          </p>
        </div>
      )}

      {details.secrets_notice && <p className="text-xs text-warn">{details.secrets_notice}</p>}
      {details.python_packages && <p className="text-xs text-warn">{details.python_packages}</p>}
    </div>
  )
}

/** At a glance, on every card: what it needs and what it asks for, each
 *  permission marked enforced or only declared, and the reminder that it is
 *  code running with the user's rights. */
function Summary({ details, needs }: { details: PluginDetails; needs: string[] }): JSX.Element {
  return (
    <>
      <p className="text-xs">
        <span className="text-muted">{t('Needs')}: </span>
        {needs.length ? needs.map((n) => t(n)).join(' · ') : t('nothing stated by the developer')}
      </p>
      {/* A plugin runs with the user's rights, so none of this is a limit on its own. */}
      <p className="text-xs">
        <span className="text-muted">{t('Asks for')}: </span>
        {details.permissions.length
          ? details.permissions.map((p, i) => (
              <span key={p.id}>
                {i > 0 && ' · '}
                {p.label} <span className="text-muted">({t(p.enforcement)})</span>
              </span>
            ))
          : t('no permissions')}
      </p>
      {details.notice && <p className="text-xs text-warn">⚠ {t(details.notice)}</p>}
    </>
  )
}

/** One listing in the results: what it is and, at a glance, its tier, where
 *  it runs, what it needs and what it may do. */
function ListingCard({
  listing,
  hardware,
  onOpen,
  onTag
}: {
  listing: Listing
  hardware: Hardware | null
  onOpen: () => void
  onTag: (tag: string) => void
}): JSX.Element {
  const d = listing.details
  const needs = needLines(listing.requirements, hardware, d, listing.problems_here)
  const fit = fitSummary(needs)
  const tags = listing.tags ?? []
  return (
    <div className="card space-y-2 flex flex-col">
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <button className="font-semibold text-left hover:text-accent" onClick={onOpen}>
            {listing.name}
          </button>
          <p className="text-xs text-muted truncate">
            {listing.publisher} · {listing.latest}
          </p>
        </div>
        {listing.installed &&
          (listing.update_available ? (
            <Pill badge={{ label: `${t('Update')} ${listing.installed} → ${listing.latest}`, tone: 'info', title: '' }} />
          ) : (
            <Pill badge={{ label: `${t('Installed')} ${listing.installed}`, tone: 'ok', title: '' }} />
          ))}
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Pill badge={tierBadge(d)} />
        {catalogBadges(listing.badges, true).map((b) => (
          <Pill key={b.label} badge={b} />
        ))}
        <Pill badge={executionBadge(d.execution, d.execution_text)} />
        {fit && <Pill badge={{ ...fit, title: '' }} />}
        {listing.license && (
          <Pill badge={{ label: `${t('Licence')} ${listing.license}`, tone: 'info', title: t('The licence the developer chose') }} />
        )}
      </div>
      {listing.unofficial && <p className="text-xs text-muted">{listing.unofficial}</p>}
      <p className="text-sm">{listing.description}</p>
      {metricLines(listing.metrics).length > 0 && (
        <p className="text-xs text-muted">{metricLines(listing.metrics).slice(0, 2).map((l) => t(l.text)).join(' · ')}</p>
      )}
      {listing.events && listing.events.length > 0 && (
        <p className="text-xs">
          <span className="text-muted">{t('Finds')}: </span>
          {listing.events.slice(0, 6).map(slugLabel).join(' · ')}
          {listing.events.length > 6 ? ' …' : ''}
        </p>
      )}
      {d.data_warnings.map((w) => (
        <p key={w} className="text-xs text-warn font-medium">
          {w}
        </p>
      ))}
      <Summary details={d} needs={needs.map((n) => n.text)} />
      <div className="flex flex-wrap gap-1.5 mt-auto pt-1">
        {listing.category && (
          <span className="text-xs bg-raised px-2 py-0.5 rounded">{categoryLabel(listing.category)}</span>
        )}
        {tags.slice(0, 5).map((tag) => (
          <button
            key={tag}
            className="text-xs text-muted hover:text-accent px-1"
            onClick={() => onTag(tag)}
            title={t('Show listings with this tag')}
          >
            #{tag}
          </button>
        ))}
        {tags.length > 5 && <span className="text-xs text-muted px-1">+{tags.length - 5}</span>}
      </div>
      <button className="btn-ghost !py-1.5 self-start" onClick={onOpen}>
        {t('Details')}
      </button>
    </div>
  )
}

/** A listing's own page: everything, every version, and Install. */
function ListingPage({
  listing,
  hardware,
  manage,
  onBack,
  onInstall,
  onTag
}: {
  listing: Listing
  hardware: Hardware | null
  manage: boolean
  onBack: () => void
  onInstall: (source: PluginSource) => void
  onTag: (tag: string) => void
}): JSX.Element {
  const [version, setVersion] = useState(listing.latest)
  const chosen = listing.versions.find((v) => v.version === version) ?? listing.versions[0]
  const latest = listing.versions.find((v) => v.version === listing.latest)
  const samePermissions = (a?: string[], b?: string[]): boolean =>
    JSON.stringify([...(a ?? [])].sort()) === JSON.stringify([...(b ?? [])].sort())
  const settings = Object.entries(listing.settings ?? {})
  const checks = checkLines(listing.checks)
  const action =
    listing.installed === version
      ? t('Install again')
      : listing.installed
        ? `${t('Install')} ${version} (${t('installed')}: ${listing.installed})`
        : `${t('Install')} ${version}`
  return (
    <div className="space-y-4">
      <button className="btn-ghost !py-1.5" onClick={onBack}>
        ← {t('Back to results')}
      </button>
      <section className="card space-y-4">
        <div>
          <h3 className="text-xl font-bold">{listing.name}</h3>
          <p className="text-sm text-muted">
            {listing.id} · {t('by')} {listing.author?.name ?? listing.publisher}
            {listing.license ? ` · ${t('licence')} ${listing.license}` : ''}
            {listing.category ? ` · ${categoryLabel(listing.category)}` : ''}
          </p>
          {listing.unofficial && <p className="text-sm text-muted mt-1">{listing.unofficial}</p>}
          {(listing.badges ?? []).length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {catalogBadges(listing.badges).map((b) => (
                <Pill key={b.label} badge={b} />
              ))}
            </div>
          )}
          {listing.featured && (
            <p className="text-xs text-muted mt-1">
              ★ {t('Featured')}: {listing.featured.reason}
            </p>
          )}
        </div>
        <p>{listing.description}</p>
        {listing.events && listing.events.length > 0 && (
          <div>
            <p className="label mb-1">{t('What it finds')}</p>
            <ul className="text-sm grid grid-cols-2 gap-x-4">
              {listing.events.map((e) => (
                <li key={e}>✓ {slugLabel(e)}</li>
              ))}
            </ul>
          </div>
        )}
        {listing.games && listing.games.length > 0 && (
          <p className="text-sm">
            <span className="text-muted">{t('For')}: </span>
            {listing.games.map(slugLabel).join(', ')}
          </p>
        )}
        {listing.tags && listing.tags.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {listing.tags.map((tag) => (
              <button
                key={tag}
                className="text-xs bg-raised text-muted hover:text-accent px-2 py-0.5 rounded"
                onClick={() => onTag(tag)}
                title={t('Show listings with this tag')}
              >
                #{tag}
              </button>
            ))}
          </div>
        )}

        <DetailsBlock
          details={listing.details}
          requirements={listing.requirements}
          hardware={hardware}
          problems={listing.problems_here}
        />

        {settings.length > 0 && (
          <div className="text-sm">
            <p className="label mb-1">{t('Settings you can change')}</p>
            <ul className="space-y-0.5">
              {settings.map(([name, s]) => (
                <li key={name}>
                  {s.title ?? name}
                  {s.type === 'secret' && <span className="text-muted"> · {t('a key you enter once')}</span>}
                  {s.description && <span className="text-muted"> · {s.description}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}

        <BuiltOn items={listing.based_on} />
        <Numbers metrics={listing.metrics} discussions={listing.discussions_url} />
        <Links links={listingLinks(listing)} />

        <div className="text-sm">
          <p className="label mb-1">{t('Where it comes from')}</p>
          <p>
            {listing.repository}
            {listing.path && listing.path !== '.' ? ` · ${listing.path}` : ''}
          </p>
          <p className="text-muted">
            {t('Listed in')}: {indexName(listing.index)}
          </p>
          {checks.length > 0 && (
            <ul className="text-xs text-muted mt-1">
              <li>
                {listing.index === 'bundled'
                  ? t('Checks run when the list was built:')
                  : `${t('Checks this list reports')} (${indexName(listing.index)}):`}
              </li>
              {checks.map((c) => (
                <li key={c.text} className={c.ok ? '' : 'text-error'}>
                  {c.ok ? '✓' : '✕'} {t(c.text)}
                  {!c.ok && ` (${t('not passed')})`}
                </li>
              ))}
              <li>{t('These checks are automatic. Nobody has read this plugin’s code.')}</li>
            </ul>
          )}
        </div>

        <div className="border-t border-raised/50 pt-3 space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <label className="text-sm flex items-center gap-2">
              <span className="label">{t('Version')}</span>
              <select className="input !w-auto" value={version} onChange={(e) => setVersion(e.target.value)}>
                {listing.versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    {v.version}
                    {v.date ? ` · ${v.date}` : ''}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="btn-accent"
              disabled={!manage || Boolean(chosen?.problem_here)}
              title={manage ? '' : t('Installing plugins needs the Clips Kitty desktop app.')}
              onClick={() => onInstall({ kind: 'index', id: listing.id, version })}
            >
              {action}
            </button>
          </div>
          {chosen?.problem_here && (
            <p className="text-sm text-error">
              ✕ {t('This version can’t run here')}: {chosen.problem_here}
            </p>
          )}
          {chosen && compatibilityText(chosen.compatibility) && (
            <p className={`text-sm ${compatibilityText(chosen.compatibility)?.tone === 'ok' ? 'text-success' : 'text-warn'}`}>
              {t(compatibilityText(chosen.compatibility)?.text ?? '')}
            </p>
          )}
          {chosen && latest && chosen.version !== latest.version && !samePermissions(chosen.permissions, latest.permissions) && (
            <p className="text-sm text-warn">
              ⚠{' '}
              {t(
                'This version asks for different permissions from the one described above. Install lists exactly what it asks for before anything is installed.'
              )}
            </p>
          )}
          {chosen && (
            <p className="text-xs text-muted">
              {t('Commit')} {chosen.commit.slice(0, 12)}
              {chosen.tag ? ` · ${t('tag')} ${chosen.tag}` : ''}
              {chosen.requires?.clips_kitty ? ` · ${t('needs Clips Kitty')} ${chosen.requires.clips_kitty}` : ''}
              {chosen.tested_with ? ` · ${t('tested with')} ${chosen.tested_with}` : ''}
            </p>
          )}
          <p className="text-xs text-muted">
            {t('Install shows you exactly what will be installed first. Nothing from the plugin runs until you process a video with it.')}
          </p>
        </div>
      </section>
    </div>
  )
}

/** One app, model, workflow, integration or tool. They aren't installed
 *  from here: each links to its home, and one that runs inside Clips Kitty
 *  through a listed pipeline links to that. */
function EntryCard({
  entry,
  onOpenListing
}: {
  entry: CatalogEntry
  onOpenListing: (id: string) => void
}): JSX.Element {
  const link = entryLink(entry)
  const numbers = metricLines(entry.metrics)
  return (
    <div className="card space-y-2 flex flex-col">
      <div>
        <OutLink url={link}>
          <span className="font-semibold">{entry.name}</span>
        </OutLink>
        <p className="text-xs text-muted">
          {t(RELATIONSHIP_LABELS[entry.relationship] ?? entry.relationship)}
          {entry.uses ? ` · ${t(entry.uses === 'both' ? 'uses the API and the SDK' : entry.uses === 'sdk' ? 'uses the SDK' : 'uses the local API')}` : ''}
          {entry.runs ? ` · ${t(entry.runs === 'local' ? 'runs locally' : entry.runs === 'cloud' ? 'runs online' : 'local or online')}` : ''}
        </p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {catalogBadges(entry.badges).map((b) => (
          <Pill key={b.label} badge={b} />
        ))}
        <Pill badge={{ label: `${t('Licence')} ${entry.license}`, tone: 'info', title: t('The licence its authors chose') }} />
        {!entry.checked && (
          <Pill badge={{ label: t('Not yet checked'), tone: 'warn', title: t('Added by its authors; a maintainer hasn’t checked it against the criteria yet.') }} />
        )}
      </div>
      {entry.unofficial && <p className="text-xs text-muted">{entry.unofficial}</p>}
      <p className="text-sm">{entry.description}</p>
      {entry.license_note && <p className="text-xs text-muted">{t('Licence note')}: {entry.license_note}</p>}
      {entry.warning && <p className="text-xs text-warn font-medium">⚠ {entry.warning}</p>}
      {numbers.length > 0 && (
        <ul className="text-xs text-muted space-y-0.5">
          {numbers.map((l) => (
            <li key={l.text} className={l.tone === 'warn' ? 'text-warn' : ''}>
              {t(l.text)}
            </li>
          ))}
        </ul>
      )}
      {entry.featured && <p className="text-xs text-muted">★ {entry.featured.reason}</p>}
      <div className="flex flex-wrap gap-2 mt-auto pt-1 items-center">
        {entry.adapter &&
          (entry.adapter_listed ? (
            <button className="btn-ghost !py-1.5" onClick={() => onOpenListing(entry.adapter ?? '')}>
              {t('Use it in Clips Kitty')} →
            </button>
          ) : (
            <span className="text-xs text-muted">
              {t('Runs in Clips Kitty through')} {entry.adapter}
            </span>
          ))}
        {entry.discussions_url && (
          <span className="text-xs">
            <OutLink url={entry.discussions_url}>{t('Discussions')}</OutLink>
          </span>
        )}
        {(entry.platforms ?? []).length > 0 && (
          <span className="text-xs text-muted">{(entry.platforms ?? []).map(slugLabel).join(', ')}</span>
        )}
      </div>
    </div>
  )
}

/** Awesome Clips Kitty's other kinds, by section: apps, models, workflows,
 *  integrations and tools around Clips Kitty. */
function CatalogBrowse({
  kind,
  q,
  onOpenListing
}: {
  kind: string
  q: string
  onOpenListing: (id: string) => void
}): JSX.Element {
  const [data, setData] = useState<CatalogResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    const timer = setTimeout(
      () => {
        plugins
          .catalog({ q: q.trim(), kind })
          .then((r) => {
            if (!live) return
            setData(r)
            setError(null)
          })
          .catch((e: Error) => live && setError(e.message))
      },
      q ? 250 : 0
    )
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [kind, q])
  if (error) return <div className="card text-error text-sm">{error}</div>
  if (!data) return <p className="text-muted">{t('Loading…')}</p>
  const groups = groupBySection(data.entries, data.sections[kind]?.sections)
  return (
    <div className="space-y-4">
      {groups.length === 0 && (
        <div className="card text-sm">
          {q.trim()
            ? t('Nothing in Awesome Clips Kitty matches that.')
            : t('Nothing of this kind is in Awesome Clips Kitty yet.')}
        </div>
      )}
      {groups.map((g) => (
        <section key={g.id || 'other'} className="space-y-2">
          <div>
            <h3 className="font-semibold">{t(g.title)}</h3>
            {g.description && <p className="text-xs text-muted">{t(g.description)}</p>}
          </div>
          <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(18rem,1fr))]">
            {g.entries.map((e) => (
              <EntryCard key={e.id} entry={e} onOpenListing={onOpenListing} />
            ))}
          </div>
        </section>
      ))}
      <p className="text-xs text-muted border-t border-raised/50 pt-3">
        {t('From Awesome Clips Kitty, a curated list. Built with Clips Kitty: a separate app or tool that uses Clips Kitty. Related: relevant, not connected to Clips Kitty yet. Each project keeps its own licence.')}
        {data.metrics_at ? ` ${t('Numbers read on')} ${data.metrics_at}.` : ''}
      </p>
    </div>
  )
}

/** Whether installs are counted, and the switch. */
function CountingNote({ manage }: { manage: boolean }): JSX.Element | null {
  const [state, setState] = useState<Counting | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  useEffect(() => {
    plugins.counting().then(setState).catch(() => setState(null))
  }, [])
  if (!state) return null
  const toggle = (on: boolean): void => {
    setProblem(null)
    plugins
      .setCounting(on)
      .then(setState)
      .catch((e: Error) => setProblem(e.message))
  }
  return (
    <div className="space-y-1">
      <label className="flex items-start gap-2 cursor-pointer">
        <input
          type="checkbox"
          className="size-4 mt-0.5 accent-[#38BDF8]"
          checked={state.enabled}
          disabled={!manage || state.locked_off}
          onChange={(e) => toggle(e.target.checked)}
        />
        <span>
          {t('Count my installs')}. {t(state.text)}
          {state.locked_off && ` ${t('Switched off in settings.yaml (plugins.count_installs).')}`}
          {!state.active && ` ${t('Nothing is counted yet: the catalog has no counter address.')}`}
        </span>
      </label>
      {problem && <p className="text-error">{problem}</p>}
    </div>
  )
}

/** Search and browse the registry indexes. */
function Browse({
  hardware,
  manage,
  onInstall
}: {
  hardware: Hardware | null
  manage: boolean
  onInstall: (source: PluginSource) => void
}): JSX.Element {
  const [q, setQ] = useState('')
  const [category, setCategory] = useState('')
  const [tag, setTag] = useState('')
  const kind = 'pipeline'
  /** "pipeline", or one of the directory's other kinds (DIRECTORY_KINDS). */
  const [view, setView] = useState('pipeline')
  /** A listing opened from an app's card, kept open until the full list has loaded. */
  const opening = useRef<string | null>(null)
  const [data, setData] = useState<MarketplaceResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshNote, setRefreshNote] = useState<string | null>(null)
  const [stamp, setStamp] = useState(0)

  useEffect(() => {
    let live = true
    const timer = setTimeout(
      () => {
        plugins
          .marketplace({ q: q.trim(), category, tag, kind })
          .then((r) => {
            if (!live) return
            if (!q.trim() && !category && !tag) opening.current = null
            setData(r)
            setError(null)
          })
          .catch((e: Error) => live && setError(e.message))
      },
      q ? 250 : 0
    )
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [q, category, tag, stamp])

  useEffect(() => {
    const again = (): void => setStamp((n) => n + 1)
    window.addEventListener(PLUGINS_CHANGED, again)
    return () => window.removeEventListener(PLUGINS_CHANGED, again)
  }, [])

  const refresh = (): void => {
    setRefreshing(true)
    setRefreshNote(null)
    plugins
      .refresh()
      .then((r) => {
        const failed = r.indexes.filter((i) => !i.ok)
        setRefreshNote(
          failed.length
            ? failed.map((i) => `${indexName(i.url)}: ${i.error ?? t('could not be fetched')}`).join(' · ')
            : t('Up to date.')
        )
        setStamp((n) => n + 1)
      })
      .catch((e: Error) => setRefreshNote(e.message))
      .finally(() => setRefreshing(false))
  }

  // A listing the current results no longer have is closed, so it can't
  // reopen by itself when a later search brings it back.
  useEffect(() => {
    if (open && data && !data.plugins.some((p) => p.id === open) && opening.current !== open) setOpen(null)
  }, [data, open])

  const listing = open ? data?.plugins.find((p) => p.id === open) : null
  if (listing)
    return (
      <ListingPage
        listing={listing}
        hardware={hardware}
        manage={manage}
        onBack={() => {
          opening.current = null
          setOpen(null)
        }}
        onInstall={onInstall}
        onTag={(tg) => {
          setTag(tg)
          setOpen(null)
        }}
      />
    )

  const remote = (data?.indexes ?? []).filter((i) => i.url !== 'bundled')
  const filtered = Boolean(q.trim() || category || tag)
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2 items-center">
        <input
          className="input flex-1 min-w-[16rem]"
          placeholder={t('Search: a game, a sport, “podcast”, “captions”…')}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label={t('Search the Marketplace')}
        />
      </div>

      <div className="flex flex-wrap gap-1.5 items-center" role="tablist" aria-label={t('Type')}>
        {[['pipeline', KIND_LABELS.pipeline], ...Object.entries(DIRECTORY_KINDS)].map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={view === id}
            className={`text-sm px-3 py-1 rounded-lg ${
              view === id ? 'bg-accent/15 text-accent font-medium' : 'text-muted hover:text-ink'
            }`}
            onClick={() => setView(id)}
          >
            {t(label)}
          </button>
        ))}
        {Object.entries(KIND_LABELS)
          .filter(([id]) => id !== kind && (data?.kinds ?? {})[id] !== 'built' && !(id in DIRECTORY_KINDS))
          .map(([id, label]) => (
            <span
              key={id}
              className="text-sm px-3 py-1 rounded-lg text-muted opacity-60"
              title={t('Planned: Clips Kitty can’t install this kind of plugin yet.')}
            >
              {t(label)}
              <span className="text-[10px] ml-1 uppercase">{t('planned')}</span>
            </span>
          ))}
      </div>

      {view !== 'pipeline' && (
        <CatalogBrowse
          kind={view}
          q={q}
          onOpenListing={(id) => {
            opening.current = id
            setView('pipeline')
            setQ('')
            setCategory('')
            setTag('')
            setOpen(id)
          }}
        />
      )}

      {view === 'pipeline' && (
      <>
      <div className="flex flex-wrap gap-1.5">
        <button
          className={`text-xs px-2.5 py-1 rounded-full ${!category ? 'bg-accent/15 text-accent' : 'bg-raised text-muted hover:text-ink'}`}
          onClick={() => setCategory('')}
        >
          {t('All')}
        </button>
        {(data?.categories ?? Object.keys(CATEGORY_LABELS)).map((c) => (
          <button
            key={c}
            className={`text-xs px-2.5 py-1 rounded-full ${category === c ? 'bg-accent/15 text-accent' : 'bg-raised text-muted hover:text-ink'}`}
            onClick={() => setCategory(category === c ? '' : c)}
          >
            {t(categoryLabel(c))}
          </button>
        ))}
        {tag && (
          <button className="text-xs px-2.5 py-1 rounded-full bg-accent/15 text-accent" onClick={() => setTag('')}>
            #{tag} ✕
          </button>
        )}
      </div>

      {error && <div className="card text-error text-sm">{error}</div>}
      {!data && !error && <p className="text-muted">{t('Loading…')}</p>}

      {data && data.plugins.length === 0 && (
        <div className="card text-sm space-y-1">
          {filtered ? (
            <p>{t('Nothing listed matches that. Try fewer words, or another category.')}</p>
          ) : (
            <>
              <p>{t('No community plugins are listed yet.')}</p>
              <p className="text-muted">
                {remote.some((i) => !i.cached)
                  ? t('Some of the lists set up in your settings haven’t been fetched yet: press Check for new listings below.')
                  : remote.length > 0
                    ? t('Neither the list that came with Clips Kitty nor the lists set up in your settings has a pipeline yet.')
                    : t('The list that came with this version of Clips Kitty is empty, and no other list is set up.')}{' '}
                {t('You can still install a plugin from a folder or a GitHub link.')}
              </p>
            </>
          )}
        </div>
      )}

      {data && data.plugins.length > 0 && (
        <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(18rem,1fr))]">
          {data.plugins.map((p) => (
            <ListingCard
              key={p.id}
              listing={p}
              hardware={hardware}
              onOpen={() => setOpen(p.id)}
              onTag={(tg) => setTag(tg)}
            />
          ))}
        </div>
      )}

      {data && (
        <div className="text-xs text-muted space-y-1 border-t border-raised/50 pt-3">
          {data.indexes.map((i) => (
            <p key={i.url}>
              {indexName(i.url)}: {i.plugins ?? 0} {i.plugins === 1 ? t('plugin') : t('plugins')}
              {i.url !== 'bundled' && ` · ${t(fetchedText(i.fetched_at, Date.now()))}`}
            </p>
          ))}
          {remote.length > 0 && (
            <button className="btn-ghost !py-1 !px-2 text-xs" onClick={refresh} disabled={refreshing}>
              {refreshing ? t('Checking…') : t('Check for new listings')}
            </button>
          )}
          {refreshNote && <p>{refreshNote}</p>}
          <p>
            {t(
              'Community means the catalog’s automatic checks passed. It does not mean anyone reviewed the code. ✓ Compatible means a version passed automated technical checks, not a security review. Clips Kitty sends nothing about what you browse.'
            )}
          </p>
          <CountingNote manage={manage} />
        </div>
      )}
      </>
      )}
    </div>
  )
}

const SOURCE_LABELS: Record<string, string> = {
  huggingface: 'Hugging Face',
  url: 'A file from the web',
  ollama: 'Ollama',
  bundled: 'Ships with Clips Kitty'
}

/** One model an installed plugin lists: whether it is on this PC, its
 *  licence and size, which other plugins share the same copy, and Download
 *  for the ones Clips Kitty fetches (Hugging Face and web files), after a
 *  look at what would be fetched. */
function ModelRow({
  plugin,
  name,
  status,
  manage
}: {
  plugin: InstalledPlugin
  name: string
  status: ModelStatus | undefined
  manage: boolean
}): JSX.Element {
  const [plan, setPlan] = useState<ModelPlan | null>(null)
  const [pickleOk, setPickleOk] = useState(false)
  const [busy, setBusy] = useState<'plan' | 'download' | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const listed = plugin.details.models.find((m) => m.name === name)
  const source = status?.source ?? listed?.source ?? ''
  const fetchable = source === 'huggingface' || source === 'url'
  const others = (status?.used_by ?? []).filter((u) => u.plugin !== plugin.id)
  const look = (): void => {
    setBusy('plan')
    setNote(null)
    plugins
      .modelPlan(plugin.id, name)
      .then(setPlan)
      .catch((e: Error) => setNote(e.message))
      .finally(() => setBusy(null))
  }
  const download = (): void => {
    setBusy('download')
    setNote(null)
    plugins
      .downloadModel(plugin.id, name, pickleOk)
      .then(() => setPlan(null))
      .catch((e: Error) => setNote(e.message))
      .finally(() => setBusy(null))
  }
  const state =
    status?.installed === true
      ? { text: t('On this PC'), className: 'text-success', mark: '✓' }
      : status?.installed === false
        ? { text: t('Not on this PC'), className: 'text-warn', mark: '✕' }
        : { text: t('Can’t tell'), className: 'text-muted', mark: '?' }
  const licence = status?.license ?? listed?.license
  return (
    <li className="space-y-1">
      <p>
        <span className="font-medium">{name}</span>
        <span className="text-muted">
          {' '}
          · {t(SOURCE_LABELS[source] ?? source)} · {status?.id ?? listed?.id}
          {(status?.revision ?? listed?.revision) && ` @ ${(status?.revision ?? listed?.revision ?? '').slice(0, 10)}`}
        </span>
      </p>
      <p className="text-xs">
        <span className={state.className}>
          {state.mark} {state.text}
        </span>
        {status?.link === 'copy' && (
          <span className="text-muted"> · {t('stored as a copy (Windows refused a link), so it takes the space twice')}</span>
        )}
        {licence ? (
          <span className="text-muted">
            {' '}
            · {t('licence')} {licence}
          </span>
        ) : (
          <span className="text-warn"> · {t('no licence stated')}</span>
        )}
        {status?.size_bytes ? <span className="text-muted"> · {formatBytes(status.size_bytes)}</span> : null}
        {others.length > 0 && (
          <span className="text-muted">
            {' '}
            · {t('the same copy is used by')} {others.map((u) => u.plugin).join(', ')}
          </span>
        )}
      </p>
      {status?.note && <p className="text-xs text-muted">{t(status.note)}</p>}
      {(status?.pickle_files ?? []).length > 0 && (
        <p className="text-xs text-warn">
          ⚠ {(status?.pickle_files ?? []).join(', ')}: {t('a pickle format, which can run code when it is loaded.')}
        </p>
      )}
      {fetchable && status?.installed === false && !plan && (
        <button className="btn-ghost !py-1 text-xs" disabled={!manage || busy !== null} onClick={look}>
          {busy === 'plan' ? t('Checking…') : t('Download…')}
        </button>
      )}
      {plan && (
        <div className="border border-raised/60 rounded p-2 space-y-1.5 text-xs">
          <ul className="space-y-0.5">
            {plan.files.map((f) => (
              <li key={f.file}>
                {f.file}
                <span className="text-muted">
                  {' '}
                  ·{' '}
                  {f.installed
                    ? t('already here')
                    : f.already_here_as
                      ? t('already here for another model: linked, not downloaded')
                      : f.size !== null && f.size !== undefined
                        ? formatBytes(f.size)
                        : t('size unknown')}
                </span>
              </li>
            ))}
          </ul>
          <p>
            {t('To download')}: {formatBytes(plan.download_bytes) || '0 KB'}
            {!plan.size_known && ` ${t('and some files of unknown size')}`}
            {plan.license ? ` · ${t('licence')} ${plan.license}` : ` · ${t('no licence stated')}`}
          </p>
          {plan.problem && <p className="text-error">{t(plan.problem)}</p>}
          {plan.pickle_files.length > 0 && !plan.problem && (
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                className="size-4 mt-0.5 accent-[#38BDF8]"
                checked={pickleOk}
                onChange={(e) => setPickleOk(e.target.checked)}
              />
              {t('I trust this model: its pickle-format files can run code when the plugin loads them.')}
            </label>
          )}
          <div className="flex gap-2">
            {!plan.problem && (
              <button
                className="btn-accent !py-1 text-xs"
                disabled={!manage || busy !== null || (plan.pickle_files.length > 0 && !pickleOk)}
                onClick={download}
              >
                {busy === 'download' ? t('Downloading… keep Clips Kitty open') : t('Download')}
              </button>
            )}
            <button className="btn-ghost !py-1 text-xs" disabled={busy === 'download'} onClick={() => setPlan(null)}>
              {t('Cancel')}
            </button>
          </div>
        </div>
      )}
      {note && <p className="text-xs text-error">{note}</p>}
    </li>
  )
}

/** The models section of an installed plugin's card. */
function PluginModels({
  plugin,
  overview,
  manage
}: {
  plugin: InstalledPlugin
  overview: ModelsOverview | null
  manage: boolean
}): JSX.Element | null {
  const names = plugin.details.models.map((m) => m.name ?? m.id ?? '').filter(Boolean)
  if (names.length === 0) return null
  const statusOf = (name: string): ModelStatus | undefined =>
    overview?.models.find((m) => m.used_by.some((u) => u.plugin === plugin.id && u.name === name))
  return (
    <div className="space-y-1 text-sm">
      <p className="label">{t('Models')}</p>
      <ul className="space-y-2">
        {names.map((n) => (
          <ModelRow key={n} plugin={plugin} name={n} status={statusOf(n)} manage={manage} />
        ))}
      </ul>
      <p className="text-xs text-muted">
        {t(
          'Downloaded models go in one folder shared by every plugin, so a file two plugins use is stored once. Clips Kitty checks each file’s size and checksum; it never loads a plugin’s model itself.'
        )}
      </p>
    </div>
  )
}

/** Keys (secret settings) for one installed plugin. Values are never read back. */
function Keys({ plugin }: { plugin: InstalledPlugin }): JSX.Element | null {
  const [values, setValues] = useState<Record<string, string>>({})
  const [note, setNote] = useState<string | null>(null)
  if (plugin.details.secrets.length === 0) return null
  const save = (name: string, value: string | null): void => {
    setNote(null)
    plugins
      .setSecrets(plugin.id, { [name]: value })
      .then(() => {
        setValues((v) => ({ ...v, [name]: '' }))
        setNote(value ? t('Saved.') : t('Removed.'))
      })
      .catch((e: Error) => setNote(e.message))
  }
  return (
    <div className="space-y-2">
      <p className="label">{t('Keys')}</p>
      {plugin.details.secrets.map((name) => {
        const spec = plugin.settings[name]
        const set = plugin.secrets_set.includes(name)
        return (
          <div key={name} className="flex flex-wrap items-center gap-2 text-sm">
            <span className="w-40 shrink-0" title={spec?.description}>
              {spec?.title ?? name}
            </span>
            <input
              type="password"
              className="input !w-64"
              placeholder={set ? t('saved (enter a new one to replace it)') : t('not set')}
              value={values[name] ?? ''}
              onChange={(e) => setValues((v) => ({ ...v, [name]: e.target.value }))}
              autoComplete="off"
            />
            <button className="btn-ghost !py-1.5" disabled={!values[name]} onClick={() => save(name, values[name])}>
              {t('Save')}
            </button>
            {set && (
              <button className="btn-ghost !py-1.5" onClick={() => save(name, null)}>
                {t('Remove')}
              </button>
            )}
          </div>
        )
      })}
      {plugin.details.secrets_notice && <p className="text-xs text-warn">{plugin.details.secrets_notice}</p>}
      {note && <p className="text-xs text-muted">{note}</p>}
    </div>
  )
}

function InstalledCard({
  plugin,
  update,
  hardware,
  manage,
  onInstall,
  onRemoved,
  models
}: {
  plugin: InstalledPlugin
  update: Listing | undefined
  hardware: Hardware | null
  manage: boolean
  /** Every installed plugin's models and where they are (GET /plugin-models). */
  models: ModelsOverview | null
  onInstall: (source: PluginSource) => void
  /** Told before the list reloads without this card, so its message outlives it. */
  onRemoved: (message: string) => void
}): JSX.Element {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const run = (what: () => Promise<unknown>, done?: string): void => {
    setBusy(true)
    setNote(null)
    what()
      .then(() => done && setNote(done))
      .catch((e: Error) => setNote(e.message))
      .finally(() => setBusy(false))
  }
  const blocked = plugin.flag?.severity === 'blocked'
  const missingKeys = plugin.details.secrets
    .filter((name) => !plugin.secrets_set.includes(name))
    .map((name) => plugin.settings[name]?.title ?? name)
  return (
    <div className="card space-y-3">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <p className="font-semibold">
            {plugin.name} <span className="text-muted font-normal">{plugin.version}</span>
          </p>
          <p className="text-xs text-muted truncate">
            {plugin.id} · {plugin.source_text}
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm cursor-pointer shrink-0">
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            checked={plugin.enabled}
            disabled={busy || !manage || (blocked && !plugin.enabled)}
            onChange={(e) => run(() => plugins.setEnabled(plugin.id, e.target.checked))}
          />
          {plugin.enabled ? t('On') : t('Off')}
        </label>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Pill badge={tierBadge(plugin.details)} />
        <Pill badge={executionBadge(plugin.details.execution, plugin.details.execution_text)} />
        {plugin.pinned && <Pill badge={{ label: t('Pinned'), tone: 'info', title: t('Updates are not offered.') }} />}
      </div>
      {plugin.flag && (
        <p className={`text-sm font-medium ${blocked ? 'text-error' : 'text-warn'}`}>
          {blocked
            ? `${t('Blocked')}: ${plugin.flag.reason ?? t('on the registry’s block list')}. ${t('It can’t run. Remove it.')}`
            : `${t('No longer listed')}: ${plugin.flag.reason ?? t('removed from the registry')}. ${t('It still runs.')}`}
        </p>
      )}
      {plugin.problem && <p className="text-sm text-error">{plugin.problem}</p>}
      {(plugin.problems_here ?? []).map((p) => (
        <p key={p.text} className="text-sm text-error">
          {t(p.text)}
        </p>
      ))}
      {plugin.details.data_warnings.map((w) => (
        <p key={w} className="text-sm text-warn font-medium">
          {w}
        </p>
      ))}
      <Summary
        details={plugin.details}
        needs={needLines(plugin.requirements, hardware, plugin.details, plugin.problems_here ?? []).map((n) => n.text)}
      />
      {missingKeys.length > 0 && (
        <p className="text-sm text-warn">
          {t('Needs a key before it can run')}: {missingKeys.join(', ')}
        </p>
      )}
      <Keys plugin={plugin} />
      <PluginModels plugin={plugin} overview={models} manage={manage} />
      <div className="flex flex-wrap gap-2">
        {update && !plugin.pinned && (
          <button
            className="btn-accent !py-1.5"
            disabled={busy || !manage}
            onClick={() => onInstall({ kind: 'index', id: plugin.id, version: update.latest })}
          >
            {t('Update to')} {update.latest}
          </button>
        )}
        {plugin.previous && (
          <button
            className="btn-ghost !py-1.5"
            disabled={busy || !manage}
            onClick={() => run(() => plugins.rollback(plugin.id), `${t('Rolled back to')} ${plugin.previous}.`)}
          >
            {t('Roll back to')} {plugin.previous}
          </button>
        )}
        <button
          className="btn-ghost !py-1.5"
          disabled={busy || !manage}
          title={t('Pinned plugins are not offered updates. Installing a version yourself still works.')}
          onClick={() => run(() => plugins.setPinned(plugin.id, !plugin.pinned))}
        >
          {plugin.pinned ? t('Unpin') : t('Pin this version')}
        </button>
        <button className="btn-ghost !py-1.5" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
          {t('Details')} {expanded ? '▾' : '▸'}
        </button>
        {confirmRemove ? (
          <>
            <button
              className="btn-ghost !py-1.5 text-error"
              disabled={busy || !manage}
              onClick={() =>
                run(() =>
                  plugins.remove(plugin.id).then((r) =>
                    onRemoved(
                      r.files_left
                        ? `${t('Removed')} ${plugin.name}. ${t('Some of its files were in use and are still in the plugins folder; delete them once Clips Kitty is closed.')}`
                        : `${t('Removed')} ${plugin.name}.`
                    )
                  )
                )
              }
            >
              {t('Remove it and its keys')}
            </button>
            <button className="btn-ghost !py-1.5" onClick={() => setConfirmRemove(false)}>
              {t('Cancel')}
            </button>
          </>
        ) : (
          <button className="btn-ghost !py-1.5" disabled={busy || !manage} onClick={() => setConfirmRemove(true)}>
            {t('Remove')}
          </button>
        )}
      </div>
      {note && <p className="text-xs text-muted">{note}</p>}
      {expanded && (
        <div className="border-t border-raised/50 pt-3 space-y-3">
          {plugin.description && <p className="text-sm">{plugin.description}</p>}
          <DetailsBlock
            details={plugin.details}
            requirements={plugin.requirements}
            hardware={hardware}
            problems={plugin.problems_here ?? []}
          />
          <BuiltOn items={plugin.based_on} />
          <Links links={listingLinks(plugin)} />
          <p className="text-xs text-muted">
            {t('Versions kept')}: {plugin.versions.join(', ')}
            {plugin.installed_at ? ` · ${t('installed')} ${plugin.installed_at}` : ''}
          </p>
        </div>
      )}
    </div>
  )
}

/** Which of the user's AI settings send data off this PC ("AI: OpenAI,
 *  transcription: …"), or '' when everything runs here. Clips Kitty's own
 *  modes use them, so "Runs on this PC" is only true of those modes without. */
function useCloudAI(): string {
  const [cloud, setCloud] = useState('')
  useEffect(() => {
    let live = true
    api
      .ai()
      .then((s) => {
        const label = (id: string): string => s.providers.find((p) => p.id === id)?.label ?? id
        const parts: string[] = []
        if (!s.active.local) parts.push(`${t('AI')}: ${label(s.active.provider)}`)
        if (s.transcription.backend !== 'local') parts.push(`${t('transcription')}: ${label(s.transcription.backend)}`)
        if (live) setCloud(parts.join(', '))
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [])
  return cloud
}

function BuiltinCard({ plugin, cloudAI }: { plugin: BuiltinPlugin; cloudAI: string }): JSX.Element {
  return (
    <div className="flex items-start gap-3 border-t border-raised/50 pt-3">
      <div className="flex-1 min-w-0">
        <p className="font-medium">{plugin.name}</p>
        <p className="text-xs text-muted">{plugin.description}</p>
        <Summary details={plugin.details} needs={plugin.details.requirements} />
        {cloudAI && (
          <p className="text-xs text-warn font-medium">
            ⚠ {t('Sends your video’s transcript or audio to the services in your AI settings')} ({cloudAI})
          </p>
        )}
      </div>
      <Pill badge={tierBadge(plugin.details)} />
      {cloudAI ? (
        <Pill
          badge={{
            label: t('This PC + your cloud AI'),
            tone: 'warn',
            title: t('Runs on this PC, and uses the cloud services chosen in Settings → AI.')
          }}
        />
      ) : (
        <Pill badge={executionBadge(plugin.details.execution, plugin.details.execution_text)} />
      )}
    </div>
  )
}

function Installed({
  hardware,
  manage,
  onInstall
}: {
  hardware: Hardware | null
  manage: boolean
  onInstall: (source: PluginSource) => void
}): JSX.Element {
  const [data, setData] = useState<PluginsResponse | null>(null)
  const [updates, setUpdates] = useState<Record<string, Listing>>({})
  const [error, setError] = useState<string | null>(null)
  const [removed, setRemoved] = useState<string | null>(null)
  const [models, setModels] = useState<ModelsOverview | null>(null)
  const cloudAI = useCloudAI()
  const load = useCallback(() => {
    plugins
      .list()
      .then((r) => {
        setData(r)
        setError(null)
      })
      .catch((e: Error) => setError(e.message))
    plugins
      .marketplace()
      .then((m) => setUpdates(Object.fromEntries(m.plugins.filter((p) => p.update_available).map((p) => [p.id, p]))))
      .catch(() => setUpdates({}))
    plugins
      .models()
      .then(setModels)
      .catch(() => setModels(null))
  }, [])
  useEffect(() => {
    load()
    window.addEventListener(PLUGINS_CHANGED, load)
    return () => window.removeEventListener(PLUGINS_CHANGED, load)
  }, [load])
  if (error) return <div className="card text-error text-sm">{error}</div>
  if (!data) return <p className="text-muted">{t('Loading…')}</p>
  return (
    <div className="space-y-4">
      {removed && <div className="card text-sm">{removed}</div>}
      {data.plugins.length === 0 ? (
        <div className="card text-sm">
          {t('No community plugins installed. Find one under Browse, or add one from a folder or a link.')}
        </div>
      ) : (
        data.plugins.map((p) => (
          <InstalledCard
            key={p.id}
            plugin={p}
            update={updates[p.id]}
            hardware={hardware}
            manage={manage}
            onInstall={onInstall}
            onRemoved={setRemoved}
            models={models}
          />
        ))
      )}
      <section className="card space-y-1">
        <h3 className="font-semibold">{t('Built into Clips Kitty')}</h3>
        <p className="text-xs text-muted">{t('Always available from the Generate bar’s switches.')}</p>
        {data.builtin.map((b) => (
          <BuiltinCard key={b.id} plugin={b} cloudAI={cloudAI} />
        ))}
      </section>
    </div>
  )
}

/** Install from a folder on this PC or a Git repository at one commit. */
function AddFromLink({
  manage,
  onInstall
}: {
  manage: boolean
  onInstall: (source: PluginSource) => void
}): JSX.Element {
  const [folder, setFolder] = useState('')
  const [link, setLink] = useState('')
  const [commit, setCommit] = useState('')
  const [sub, setSub] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const pick = window.studio?.pickPluginFolder
  const fromGit = (): void => {
    const r = gitSource(link, commit, sub)
    setProblem(r.problem ?? null)
    if (r.source) onInstall(r.source)
  }
  return (
    <div className="space-y-4 max-w-3xl">
      {!manage && (
        <div className="card text-sm text-warn">{t('Installing plugins needs the Clips Kitty desktop app.')}</div>
      )}
      <p className="text-sm text-warn">
        ⚠{' '}
        {t(
          'A plugin from a folder or a link is not listed anywhere, and Clips Kitty has not checked it. Install only code you trust: it runs on this PC with your rights.'
        )}
      </p>
      <section className="card space-y-3">
        <h3 className="font-semibold">{t('From GitHub or another Git host')}</h3>
        <label className="block space-y-1">
          <span className="label">{t('Repository')}</span>
          <input
            className="input"
            placeholder="https://github.com/example-dev/example-plugin"
            value={link}
            onChange={(e) => setLink(e.target.value)}
          />
        </label>
        <label className="block space-y-1">
          <span className="label">{t('Commit (all 40 characters)')}</span>
          <input
            className="input font-mono"
            placeholder={t('Or paste a GitHub link that ends in /tree/<commit>')}
            value={commit}
            onChange={(e) => setCommit(e.target.value)}
          />
        </label>
        <label className="block space-y-1">
          <span className="label">{t('Folder in the repository (if it isn’t at the top)')}</span>
          <input className="input" placeholder="plugins/my-plugin" value={sub} onChange={(e) => setSub(e.target.value)} />
        </label>
        {problem && <p className="text-sm text-error">{problem}</p>}
        <button className="btn-accent" disabled={!manage || (!link.trim() && !commit.trim())} onClick={fromGit}>
          {t('Look at it before installing')}
        </button>
        <p className="text-xs text-muted">
          {t(
            'A commit, not a branch: what you look at is exactly what gets installed. Clips Kitty reads the files without running anything from them.'
          )}
        </p>
      </section>
      <section className="card space-y-3">
        <h3 className="font-semibold">{t('From a folder on this PC')}</h3>
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder={t('The folder with clipskitty.yaml in it')}
            value={folder}
            onChange={(e) => setFolder(e.target.value)}
          />
          {pick && (
            <button
              className="btn-ghost shrink-0"
              onClick={() => void pick().then((p) => p && setFolder(p))}
            >
              {t('Choose…')}
            </button>
          )}
        </div>
        <button
          className="btn-accent"
          disabled={!manage || !folder.trim()}
          onClick={() => onInstall({ kind: 'folder', path: folder.trim() })}
        >
          {t('Look at it before installing')}
        </button>
        <p className="text-xs text-muted">
          {t('For plugins you are writing. Installing the same version again replaces it.')}
        </p>
      </section>
    </div>
  )
}

/** What installing would do, from the engine's plan, and the Install button. */
function InstallDialog({
  plan,
  planning,
  error,
  hardware,
  onClose
}: {
  plan: PluginPlan | null
  planning: boolean
  error: string | null
  hardware: Hardware | null
  onClose: () => void
}): JSX.Element {
  const [ticked, setTicked] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<InstalledPlugin | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [counting, setCounting] = useState<Counting | null>(null)
  useEffect(() => {
    plugins.counting().then(setCounting).catch(() => setCounting(null))
  }, [])
  const needs = plan ? confirmations(plan) : []
  const counted =
    Boolean(counting?.enabled && counting.active && plan?.source.listed_in && !plan.update) &&
    (plan?.details.tier === 'listed' || plan?.details.tier === 'listed-official')
  const ready = Boolean(plan?.ok && plan.plan_id) && needs.every((c) => ticked[c]) && !busy
  const install = (): void => {
    if (!plan?.plan_id) return
    setBusy(true)
    setFailed(null)
    plugins
      .install(plan.plan_id)
      .then(setDone)
      .catch((e: Error) => setFailed(e.message))
      .finally(() => setBusy(false))
  }
  const name = plan?.plugin.name ?? plan?.plugin.id ?? t('this plugin')
  return (
    <div
      className="fixed inset-0 z-50 bg-base/80 backdrop-blur-sm grid place-items-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label={t('Install a plugin')}
      onClick={busy ? undefined : onClose}
    >
      <div className="card w-full max-w-2xl space-y-4 max-h-[85vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        {planning && <p className="text-muted">{t('Getting the files and checking them…')}</p>}
        {error && (
          <>
            <h3 className="font-semibold text-lg">{t('Couldn’t look at this plugin')}</h3>
            <p className="text-sm text-error">{error}</p>
          </>
        )}
        {plan && !done && (
          <>
            <div>
              <h3 className="font-semibold text-lg">
                {plan.update?.direction === 'update' ? t('Update') : t('Install')} {name} {plan.plugin.version ?? ''}
              </h3>
              <p className="text-xs text-muted">
                {plan.plugin.id} · {t('from')} {plan.source_text}
                {plan.plugin.license ? ` · ${t('licence')} ${plan.plugin.license}` : ''}
              </p>
            </div>
            {plan.plugin.description && <p className="text-sm">{plan.plugin.description}</p>}
            {plan.errors.length > 0 && (
              <div className="space-y-1">
                <p className="text-sm font-medium text-error">{t('It can’t be installed:')}</p>
                <ul className="text-sm text-error list-disc pl-5">
                  {plan.errors.map((e) => (
                    <li key={e}>{e}</li>
                  ))}
                </ul>
              </div>
            )}
            {plan.warnings.length > 0 && (
              <ul className="text-sm text-warn list-disc pl-5">
                {plan.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
            {updateLines(plan).map((l) => (
              <p key={l.text} className={`text-sm ${l.tone === 'danger' ? 'text-error font-medium' : l.tone === 'warn' ? 'text-warn' : 'text-muted'}`}>
                {l.text}
              </p>
            ))}
            <DetailsBlock
              details={plan.details}
              requirements={plan.plugin.requirements}
              hardware={hardware}
              problems={null}
            />
            <BuiltOn items={plan.plugin.based_on} />
            <Links links={listingLinks(plan.plugin)} />
            {plan.ok && counted && (
              <p className="text-xs text-muted">
                {t('Installing adds one to this plugin’s public install count, kept by GitHub. Nothing about you or your videos is sent. You can switch counting off at the bottom of Browse.')}
              </p>
            )}
            {plan.ok && needs.length > 0 && (
              <div className="space-y-2 border-t border-raised/50 pt-3">
                {needs.map((c) => (
                  <label key={c} className="flex items-start gap-2 text-sm cursor-pointer">
                    <input
                      type="checkbox"
                      className="size-4 mt-0.5 accent-[#38BDF8]"
                      checked={Boolean(ticked[c])}
                      onChange={(e) => setTicked((v) => ({ ...v, [c]: e.target.checked }))}
                    />
                    {t(c)}
                  </label>
                ))}
              </div>
            )}
            {failed && <p className="text-sm text-error">{failed}</p>}
          </>
        )}
        {done && (
          <div className="space-y-2 text-sm">
            <p className="text-success">
              ✓ {done.name} {done.version} {t('is installed.')}
            </p>
            <p>
              {t('To use it, add a video in the Generate bar, tick Pipeline and choose it. It runs only for the videos you choose it for.')}
            </p>
            {done.details.secrets.some((s) => !done.secrets_set.includes(s)) && (
              <p className="text-warn">{t('It needs a key. Add it under Installed.')}</p>
            )}
          </div>
        )}
        <div className="flex justify-end gap-2">
          {plan && plan.ok && !done && (
            <button className="btn-accent" disabled={!ready} onClick={install}>
              {busy ? t('Installing…') : t('Install')}
            </button>
          )}
          <button className="btn-ghost" disabled={busy} onClick={onClose}>
            {done ? t('Done') : t('Cancel')}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Marketplace(): JSX.Element {
  const [tab, setTab] = useState<Tab>(() => takeMarketplaceTab() ?? 'browse')
  const hardware = useHardware()
  const [manage, setManage] = useState(false)
  const [dialog, setDialog] = useState(false)
  const [plan, setPlan] = useState<PluginPlan | null>(null)
  const [planning, setPlanning] = useState(false)
  const [planError, setPlanError] = useState<string | null>(null)

  // Asked for again while the page is open (a "Marketplace" link elsewhere).
  useEffect(() => {
    const show = (): void => {
      const wanted = takeMarketplaceTab()
      if (wanted) setTab(wanted)
    }
    window.addEventListener(OPEN_MARKETPLACE, show)
    return () => window.removeEventListener(OPEN_MARKETPLACE, show)
  }, [])

  useEffect(() => {
    void canManage().then(setManage)
  }, [])

  // Only the latest request's answer is shown: one for a plugin the dialog
  // was closed on, or that another Install replaced, arrives too late to count.
  const planRequest = useRef(0)
  const startInstall = (source: PluginSource): void => {
    const request = ++planRequest.current
    setDialog(true)
    setPlan(null)
    setPlanError(null)
    setPlanning(true)
    plugins
      .plan(source)
      .then((p) => request === planRequest.current && setPlan(p))
      .catch((e: Error) => request === planRequest.current && setPlanError(e.message))
      .finally(() => request === planRequest.current && setPlanning(false))
  }
  const closeDialog = (): void => {
    planRequest.current++
    setDialog(false)
    setPlanning(false)
  }

  const TABS: { id: Tab; label: string }[] = [
    { id: 'browse', label: 'Browse' },
    { id: 'installed', label: 'Installed' },
    { id: 'add', label: 'Add from a folder or link' }
  ]

  return (
    <div className="p-6 space-y-5 max-w-6xl">
      <div>
        <h2 className="text-2xl font-bold">{t('Marketplace')}</h2>
        <p className="text-sm text-muted mt-1">
          {t(
            'Pipelines made by other developers: each one finds a video’s moments its own way, and Clips Kitty cuts, frames and captions them as usual. Free to list and free to install; Clips Kitty handles no payments.'
          )}
        </p>
      </div>
      <div className="flex gap-1 border-b border-raised/60" role="tablist">
        {TABS.map((tb) => (
          <button
            key={tb.id}
            role="tab"
            aria-selected={tab === tb.id}
            className={`px-4 py-2 text-sm -mb-px border-b-2 ${
              tab === tb.id ? 'border-accent text-accent font-medium' : 'border-transparent text-muted hover:text-ink'
            }`}
            onClick={() => setTab(tb.id)}
          >
            {t(tb.label)}
          </button>
        ))}
      </div>
      {tab === 'browse' && <Browse hardware={hardware} manage={manage} onInstall={startInstall} />}
      {tab === 'installed' && <Installed hardware={hardware} manage={manage} onInstall={startInstall} />}
      {tab === 'add' && <AddFromLink manage={manage} onInstall={startInstall} />}
      {dialog && (
        <InstallDialog
          key={plan?.plan_id ?? (planning ? 'planning' : 'closed')}
          plan={plan}
          planning={planning}
          error={planError}
          hardware={hardware}
          onClose={closeDialog}
        />
      )}
    </div>
  )
}
