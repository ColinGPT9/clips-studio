/** The Marketplace screen's logic: how listings, installed plugins and
 *  install plans are grouped, labelled and checked against this PC.
 *
 *  Kept free of React and of the engine's address so tests run it under Node
 *  (tests/test_ui_marketplace.py). Everything a plugin says about trust,
 *  permissions and data comes from the engine (plugins/permissions.py) word
 *  for word: this file decides how it is shown, never what it claims. */

export interface PermissionLine {
  id: string
  label: string
  /** "enforced for the hand-over" or "declared by the developer". */
  enforcement: string
}

export interface ModelLine {
  name?: string
  source?: string
  id?: string
  revision?: string
  files?: string[]
  license?: string
  size_bytes?: number
  gated?: boolean
}

/** What the engine says a plugin will do (plugins/permissions.describe). */
export interface PluginDetails {
  notice: string | null
  tier: string
  tier_text: string
  execution: string | null
  execution_text: string
  permissions: PermissionLine[]
  network: string[]
  data_warnings: string[]
  requirements: string[]
  models: ModelLine[]
  service: { name?: string; url?: string; pricing?: string; required: boolean } | null
  secrets: string[]
  secrets_notice: string | null
  python_packages: string | null
  /** It runs with a Python from this PC (`{python}` in its command). */
  needs_python: boolean
}

/** Something the engine found that stops a plugin running on this PC. */
export interface ProblemHere {
  need: 'app' | 'python' | string
  text: string
}

/** A manifest's `requirements`, as the developer declared them. */
export interface Requirements {
  gpu?: string
  vram_gb?: number
  ram_gb?: number
  disk_gb?: number
  os?: string[]
  software?: string[]
}

export interface SettingSpec {
  type: string
  title?: string
  description?: string
  default?: unknown
  minimum?: number
  maximum?: number
  max_length?: number
  options?: string[]
}

interface PluginInfo {
  id: string
  name: string
  version?: string
  kind?: string
  description?: string
  license?: string
  repository?: string
  category?: string
  tags?: string[]
  games?: string[]
  events?: string[]
  execution?: string
  author?: { name?: string; url?: string }
  links?: { docs?: string; funding?: string[] }
  requirements?: Requirements
}

export interface ListedVersion {
  version: string
  commit: string
  tag?: string
  tested_with?: string
  date?: string
  requires?: { clips_kitty?: string; plugin_api?: number | string }
  permissions?: string[]
  /** Why this version can't run on this Clips Kitty, or null. */
  problem_here?: string | null
}

/** One plugin in a registry index, as GET /marketplace returns it. */
export interface Listing extends PluginInfo {
  publisher: string
  latest: string
  versions: ListedVersion[]
  /** The index it came from: "bundled" or an address from settings. */
  index: string
  path?: string
  aliases?: string[]
  capability?: string
  settings?: Record<string, SettingSpec>
  examples?: { title: string; url: string }[]
  service?: { name?: string; url?: string; pricing?: string; required?: boolean }
  /** The automated checks the index build ran; nobody reads the code. */
  checks?: Record<string, boolean>
  details: PluginDetails
  /** "Unofficial · not made or endorsed by the makers of …" when it names a game. */
  unofficial: string | null
  installed: string | null
  update_available: boolean
  pinned: boolean
  /** What stops the latest version running here, as far as the engine can tell. */
  problems_here: ProblemHere[]
}

export interface MarketplaceIndex {
  url: string
  /** When the engine last fetched it, as an ISO 8601 UTC time; null when never. */
  fetched_at: string | null
  cached: boolean
  plugins?: number
}

export interface MarketplaceResponse {
  plugins: Listing[]
  indexes: MarketplaceIndex[]
  categories: string[]
  kinds: Record<string, 'built' | 'planned'>
}

/** An installed community plugin, as GET /plugins returns it. */
export interface InstalledPlugin extends PluginInfo {
  enabled: boolean
  pinned: boolean
  previous: string | null
  versions: string[]
  installed_at?: string
  source: Record<string, string>
  source_text: string
  builtin: false
  details: PluginDetails
  settings: Record<string, SettingSpec>
  secrets_set: string[]
  /** Why it can't run here (an app update left it behind, files missing). */
  problem: string | null
  /** Anything else the engine found missing on this PC (a Python to run it). */
  problems_here?: ProblemHere[]
  flag: { severity: 'blocked' | 'delisted' | string; reason?: string } | null
}

/** Where one model an installed plugin lists is on this PC (plugins/models.status). */
export interface ModelStatus {
  name: string
  source: 'huggingface' | 'url' | 'ollama' | 'bundled' | string
  id: string
  revision: string | null
  files: string[]
  sha256: string | null
  format: string | null
  license: string | null
  size_bytes?: number | null
  gated: boolean
  /** True or false; null when Clips Kitty can't tell (Ollama not answering). */
  installed: boolean | null
  path: string | null
  files_status: { file: string; installed: boolean; path: string | null; size: number | null; link?: string | null }[]
  store: string
  /** How a shared file was placed: symlink, hardlink or copy. */
  link: string | null
  pickle_files: string[]
  shared_with: string[]
  note: string
  used_by: { plugin: string; name: string }[]
}

/** GET /plugin-models. */
export interface ModelsOverview {
  models: ModelStatus[]
  folder: string
  stored_bytes: number
  /** Null when no installed plugin lists an Ollama model, so Ollama wasn't asked. */
  ollama_reachable: boolean | null
}

/** POST /plugin-models/plan: what Download would fetch. */
export interface ModelPlan {
  name: string
  source: string
  id: string
  revision: string | null
  installed: boolean | null
  license: string | null
  gated: boolean
  files: { file: string; installed: boolean; size: number | null; sha256: string | null; already_here_as: string | null }[]
  download_bytes: number
  size_known: boolean
  pickle_files: string[]
  /** Why it can't be downloaded here (gated, or not a kind Clips Kitty fetches). */
  problem: string | null
}

/** A mode that ships with Clips Kitty (Shorts, Gaming, Sports): Official. */
export interface BuiltinPlugin extends PluginInfo {
  builtin: true
  enabled: true
  details: PluginDetails
}

export interface PluginsResponse {
  plugins: InstalledPlugin[]
  builtin: BuiltinPlugin[]
}

export interface PlanUpdate {
  from: string
  direction: 'update' | 'downgrade' | 'reinstall' | string
  added_permissions: string[]
  removed_permissions: string[]
  added_hosts: string[]
  removed_hosts: string[]
  added_data_warnings: string[]
  execution_changed: boolean
}

/** What installing would do (POST /plugins/plan). */
export interface PluginPlan {
  plan_id: string | null
  ok: boolean
  errors: string[]
  warnings: string[]
  plugin: Partial<PluginInfo>
  source: Record<string, string>
  source_text: string
  details: PluginDetails
  update: PlanUpdate | null
}

export type Tone = 'ok' | 'info' | 'warn' | 'danger'

/** The manifest's categories (sdk/python/clipskitty_sdk/manifest.py
 *  CATEGORIES), in its order. A test keeps the two in step. */
export const CATEGORY_LABELS: Record<string, string> = {
  gaming: 'Gaming',
  sports: 'Sports',
  creators: 'Creators',
  streaming: 'Streaming',
  podcasting: 'Podcasting',
  captions: 'Captions',
  detection: 'Detection',
  analytics: 'Analytics',
  audio: 'Audio',
  utilities: 'Utilities'
}

/** The manifest's kinds, built and planned (KINDS + PLANNED_KINDS). */
export const KIND_LABELS: Record<string, string> = {
  pipeline: 'Pipelines',
  'caption-style': 'Caption styles',
  publisher: 'Publishers',
  source: 'Sources',
  integration: 'Integrations',
  provider: 'Providers',
  component: 'Components'
}

export function categoryLabel(id: string | undefined): string {
  if (!id) return ''
  return CATEGORY_LABELS[id] ?? id
}

export interface Badge {
  label: string
  tone: Tone
  title: string
}

/** Where a plugin runs, as a short badge. The engine's sentence is the title. */
export function executionBadge(execution: string | null | undefined, text = ''): Badge {
  if (execution === 'local') return { label: 'Runs on this PC', tone: 'ok', title: text }
  if (execution === 'remote') return { label: 'Runs online', tone: 'warn', title: text }
  if (execution === 'hybrid') return { label: 'This PC + online', tone: 'warn', title: text }
  return { label: 'Doesn’t say where it runs', tone: 'danger', title: text }
}

/** The trust tier as a badge, in the engine's words. */
export function tierBadge(details: Pick<PluginDetails, 'tier' | 'tier_text'>): Badge {
  const tone: Tone = details.tier === 'official' ? 'ok' : details.tier === 'listed' ? 'info' : 'warn'
  const title =
    details.tier === 'official'
      ? 'Ships with Clips Kitty.'
      : details.tier === 'listed'
        ? 'In a registry index. Automated checks passed; nobody has reviewed the code.'
        : 'Installed from a folder or a link. Clips Kitty has not checked it.'
  return { label: details.tier_text || details.tier, tone, title }
}

/** What Clips Kitty can see of this PC, for the hardware-fit lines. The
 *  engine reads NVIDIA cards only (server/api.py _gpu_stats). */
export interface Hardware {
  gpu: { name: string; vram_total: number } | null
  disk_free_bytes: number | null
  /** process.platform as Electron reports it ("win32", "darwin", "linux"), or null. */
  platform: string | null
}

export interface FitLine {
  text: string
  /** yes: this PC has it. no: this PC doesn't. unknown: Clips Kitty can't tell. */
  fit: 'yes' | 'no' | 'unknown'
}

const GB = 1e9
const OS_NAMES: Record<string, string> = { windows: 'Windows', macos: 'macOS', linux: 'Linux' }
const PLATFORM_OS: Record<string, string> = { win32: 'windows', darwin: 'macos', linux: 'linux' }

function gb(n: number): string {
  return `${Math.round(n * 10) / 10} GB`
}

/** Each declared requirement against this PC. Says "can't tell" rather than
 *  guess: memory size isn't measured, and only NVIDIA cards are seen. */
export function hardwareFit(req: Requirements | undefined, hw: Hardware | null): FitLine[] {
  if (!req) return []
  const out: FitLine[] = []
  const gpu = hw?.gpu ?? null
  if (req.gpu === 'required' || req.gpu === 'recommended') {
    const need = req.gpu === 'required' ? 'Needs a graphics card' : 'A graphics card is recommended'
    if (gpu) out.push({ text: `${need}: this PC has ${gpu.name}`, fit: 'yes' })
    else if (!hw) out.push({ text: need, fit: 'unknown' })
    else
      out.push({ text: `${need}. Clips Kitty found no NVIDIA card here and can’t see other makes`, fit: 'unknown' })
  } else if (req.gpu === 'optional') {
    out.push({ text: 'A graphics card helps but isn’t needed', fit: 'yes' })
  } else if (req.gpu === 'none') {
    out.push({ text: 'Doesn’t use a graphics card', fit: 'yes' })
  }
  if (typeof req.vram_gb === 'number' && req.vram_gb > 0) {
    if (gpu && gpu.vram_total > 0) {
      const have = gpu.vram_total / GB
      out.push({
        text: `${gb(req.vram_gb)} of video memory: this card has ${gb(have)}`,
        fit: have + 0.05 >= req.vram_gb ? 'yes' : 'no'
      })
    } else out.push({ text: `${gb(req.vram_gb)} of video memory`, fit: 'unknown' })
  }
  if (typeof req.ram_gb === 'number' && req.ram_gb > 0) {
    out.push({ text: `${gb(req.ram_gb)} of memory (Clips Kitty doesn’t measure this)`, fit: 'unknown' })
  }
  if (typeof req.disk_gb === 'number' && req.disk_gb > 0) {
    const free = hw?.disk_free_bytes
    if (typeof free === 'number' && free >= 0) {
      out.push({
        text: `${gb(req.disk_gb)} of disk: ${gb(free / GB)} free`,
        fit: free / GB >= req.disk_gb ? 'yes' : 'no'
      })
    } else out.push({ text: `${gb(req.disk_gb)} of disk`, fit: 'unknown' })
  }
  if (req.os && req.os.length > 0) {
    const names = req.os.map((o) => OS_NAMES[o] ?? o).join(', ')
    const here = hw?.platform ? PLATFORM_OS[hw.platform] : undefined
    if (here) {
      const ok = req.os.includes(here)
      out.push({
        text: ok ? `Works on ${names}` : `Works on ${names}; this PC runs ${OS_NAMES[here]}`,
        fit: ok ? 'yes' : 'no'
      })
    } else out.push({ text: `Works on ${names}`, fit: 'unknown' })
  }
  if (req.software && req.software.length > 0) {
    out.push({ text: `Needs ${req.software.join(', ')}`, fit: 'unknown' })
  }
  return out
}

/** Everything a plugin needs, against this PC: its declared requirements
 *  (hardwareFit), a Python to run it with, and anything else the engine says
 *  stops it running here. `problems` null: the engine wasn't asked, so
 *  whether Python is here is unknown. */
export function needLines(
  req: Requirements | undefined,
  hw: Hardware | null,
  details: Pick<PluginDetails, 'needs_python'>,
  problems: ProblemHere[] | null | undefined
): FitLine[] {
  const out = hardwareFit(req, hw)
  const python = problems?.find((p) => p.need === 'python')
  if (details.needs_python)
    out.push(
      python
        ? { text: python.text, fit: 'no' }
        : { text: 'Python 3 installed on this PC', fit: problems ? 'yes' : 'unknown' }
    )
  for (const p of problems ?? []) if (p.need !== 'python') out.push({ text: p.text, fit: 'no' })
  return out
}

/** A one-word summary for a listing card: does it fit this PC? */
export function fitSummary(lines: FitLine[]): { label: string; tone: Tone } | null {
  if (lines.length === 0) return null
  if (lines.some((l) => l.fit === 'no')) return { label: 'May not run on this PC', tone: 'danger' }
  if (lines.some((l) => l.fit === 'unknown')) return { label: 'Check the requirements', tone: 'warn' }
  return { label: 'Fits this PC', tone: 'ok' }
}

/** A link from a plugin's manifest that is safe to offer: https, no
 *  credentials in it. Returns the normalised address, or null. */
export function safeLink(url: unknown): string | null {
  if (typeof url !== 'string' || url.length > 2000) return null
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return null
  }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password || !parsed.hostname) return null
  return parsed.href
}

export interface LinkItem {
  label: string
  url: string
  host: string
}

/** Where a plugin's links are: a listing, a plan's summary or an installed plugin. */
export interface LinkSource {
  repository?: string
  links?: { docs?: string; funding?: string[] }
  author?: { name?: string; url?: string }
  service?: { name?: string; url?: string } | null
  examples?: { title: string; url: string }[]
}

/** Every link a listing offers, labelled, https only, each address once. All
 *  of them come from the developer, which the screen says beside them. */
export function listingLinks(p: LinkSource): LinkItem[] {
  const out: LinkItem[] = []
  const seen = new Set<string>()
  const add = (label: string, url: unknown): void => {
    const href = safeLink(url)
    if (!href || seen.has(href)) return
    seen.add(href)
    out.push({ label, url: href, host: new URL(href).host })
  }
  add('Source code', p.repository)
  add('Documentation', p.links?.docs)
  if (p.author?.url) add(p.author.name ? `Developer: ${p.author.name}` : 'Developer', p.author.url)
  if (p.service?.url) add(p.service.name ? `Service: ${p.service.name}` : 'Service', p.service.url)
  for (const ex of p.examples ?? []) add(ex.title ? `Example: ${ex.title}` : 'Example', ex.url)
  for (const f of p.links?.funding ?? []) add('Support the developer', f)
  return out
}

/** What the person must tick before Install is enabled. Empty for an
 *  Official plugin that keeps everything on this PC. */
export function confirmations(plan: Pick<PluginPlan, 'details'>): string[] {
  const out: string[] = []
  const d = plan.details
  if (d.tier === 'link')
    out.push('I trust where this plugin comes from. Clips Kitty has not checked it, and it runs with my rights.')
  else if (d.tier === 'listed')
    out.push('I understand nobody has reviewed this plugin’s code. It runs on this PC with my rights.')
  if (d.data_warnings.length > 0 || d.execution === 'remote' || d.execution === 'hybrid')
    out.push('I understand this plugin sends data off this PC, as the warnings above say.')
  if (d.service?.required) out.push('I understand it needs an account with a service outside Clips Kitty.')
  return out
}

export interface ToneLine {
  text: string
  tone: Tone
}

/** What changes when a plan replaces an installed version. */
export function updateLines(plan: Pick<PluginPlan, 'update' | 'plugin' | 'details'>): ToneLine[] {
  const u = plan.update
  if (!u) return []
  const to = plan.plugin.version ?? ''
  const out: ToneLine[] = []
  if (u.direction === 'update') out.push({ text: `Updates ${u.from} to ${to}. ${u.from} is kept for roll back.`, tone: 'info' })
  else if (u.direction === 'downgrade')
    out.push({ text: `Goes back from ${u.from} to the older ${to}. ${u.from} is kept for roll back.`, tone: 'warn' })
  else out.push({ text: `Replaces the installed copy of ${u.from}.`, tone: 'info' })
  const labels = new Map(plan.details.permissions.map((p) => [p.id, p.label]))
  for (const p of u.added_permissions) out.push({ text: `New permission: ${labels.get(p) ?? p}`, tone: 'warn' })
  if (u.added_hosts.length) out.push({ text: `Now connects to: ${u.added_hosts.join(', ')}`, tone: 'warn' })
  for (const w of u.added_data_warnings) out.push({ text: `New: ${w}`, tone: 'danger' })
  if (u.execution_changed)
    out.push({ text: `Where it runs has changed: ${plan.details.execution_text || 'not stated'}`, tone: 'warn' })
  if (u.removed_permissions.length)
    out.push({ text: `No longer asks for: ${u.removed_permissions.join(', ')}`, tone: 'ok' })
  if (u.removed_hosts.length) out.push({ text: `No longer connects to: ${u.removed_hosts.join(', ')}`, tone: 'ok' })
  return out
}

/** The installed pipelines a job can use: turned on, able to run here, not blocked. */
export function usablePipelines(plugins: InstalledPlugin[]): InstalledPlugin[] {
  return plugins.filter(
    (p) => (p.kind ?? 'pipeline') === 'pipeline' && p.enabled && !p.problem && p.flag?.severity !== 'blocked'
  )
}

/** The settings a job can set (secrets are set once, in the Marketplace). */
export function jobSettings(settings: Record<string, SettingSpec> | undefined): [string, SettingSpec][] {
  return Object.entries(settings ?? {}).filter(([, s]) => s && s.type !== 'secret')
}

/** A form value turned into what the engine takes for this setting, with
 *  the same refusals as manifest.setting_value_problem, in its words. */
export function settingValue(spec: SettingSpec, raw: string | boolean): { value?: unknown; problem?: string } {
  if (spec.type === 'boolean') return typeof raw === 'boolean' ? { value: raw } : { value: raw === 'true' }
  const text = String(raw).trim()
  if (spec.type === 'integer' || spec.type === 'number') {
    const n = text === '' ? NaN : Number(text)
    if (!Number.isFinite(n)) return { problem: `${JSON.stringify(text)} is not a number` }
    if (spec.type === 'integer' && !Number.isInteger(n)) return { problem: `${text} is not a whole number` }
    if (typeof spec.minimum === 'number' && n < spec.minimum) return { problem: `${text} is below the minimum, ${spec.minimum}` }
    if (typeof spec.maximum === 'number' && n > spec.maximum) return { problem: `${text} is above the maximum, ${spec.maximum}` }
    return { value: n }
  }
  if (spec.type === 'choice') {
    const options = spec.options ?? []
    return options.includes(text) ? { value: text } : { problem: `${JSON.stringify(text)} is not one of: ${options.join(', ')}` }
  }
  if (spec.type === 'string') {
    // measured as sent, spaces included, as the engine measures it
    const value = String(raw)
    if (typeof spec.max_length === 'number' && value.length > spec.max_length)
      return { problem: `is longer than ${spec.max_length} characters` }
    return { value }
  }
  if (spec.type === 'secret') return { problem: 'a secret is set in the plugin’s settings, not in a job' }
  return { problem: `unknown setting type ${JSON.stringify(spec.type)}` }
}

/** Only the settings that differ from the plugin's defaults go in the job;
 *  the engine fills in the rest (host.job_settings). */
export function chosenSettings(
  settings: Record<string, SettingSpec> | undefined,
  values: Record<string, unknown>
): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [name, spec] of jobSettings(settings)) {
    if (!(name in values)) continue
    if (JSON.stringify(values[name]) !== JSON.stringify(spec.default)) out[name] = values[name]
  }
  return out
}

/** The settings of a video's pipeline choice that still fit the installed
 *  version. One renamed, removed or narrowed by an update or a rollback is
 *  dropped (its default applies), rather than refused at Generate. */
export function fittingSettings(
  values: Record<string, unknown> | undefined,
  settings: Record<string, SettingSpec> | undefined
): Record<string, unknown> {
  const specs = new Map(jobSettings(settings))
  const out: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(values ?? {})) {
    const spec = specs.get(name)
    if (!spec || !['boolean', 'number', 'string'].includes(typeof value)) continue
    const got = settingValue(spec, typeof value === 'boolean' ? value : String(value))
    if (!got.problem && JSON.stringify(got.value) === JSON.stringify(value)) out[name] = value
  }
  return out
}

const COMMIT = /^[0-9a-f]{40}$/
const GITHUB_PAGE =
  /^https:\/\/(?:www\.)?github\.com\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+?)(?:\.git)?(?:\/(?:tree|commit)\/([^/?#]+)(?:\/([^?#]*))?)?\/?(?:[?#].*)?$/i

interface GitHubPage {
  repo: string
  ref?: string
  path: string
}

/** A github.com address read as a repository, or a folder at a commit
 *  (undefined: not a github.com address; null: one this can't read). */
function githubPage(text: string): GitHubPage | null | undefined {
  if (!/^https:\/\/(?:www\.)?github\.com\//i.test(text)) return undefined
  const m = GITHUB_PAGE.exec(text)
  if (!m) return null
  let path = ''
  try {
    path = m[4] ? decodeURIComponent(m[4]).replace(/\/+$/, '') : ''
  } catch {
    return null
  }
  return { repo: `https://github.com/${m[1]}/${m[2]}`, ref: m[3], path }
}

/** The Git source for an address, a commit and a folder as the user typed
 *  or pasted them. A GitHub link to a folder at a commit
 *  (`…/tree/<commit>/<folder>`) fills in the commit and the folder, pasted
 *  in either box. The engine checks everything again
 *  (plugins/sources.clean_source); this explains the common mistakes first. */
export function gitSource(
  link: string,
  commit: string,
  folder = ''
): { source?: { kind: 'git'; url: string; commit: string; path?: string }; problem?: string } {
  let repo = link.trim()
  let sha = commit.trim()
  let path = folder.trim().replace(/^\/+|\/+$/g, '')
  const fromLink = githubPage(repo)
  const fromCommit = githubPage(sha)
  if (fromLink === null || fromCommit === null)
    return {
      problem: 'That GitHub address isn’t a repository, or a folder at a commit. Open the repository on GitHub and copy its address.'
    }
  if (fromCommit && !fromCommit.ref)
    return { problem: 'The commit box takes the 40-character commit, or a GitHub link that ends in /tree/<commit>.' }
  for (const page of [fromLink, fromCommit])
    if (page?.ref && !COMMIT.test(page.ref.toLowerCase()))
      return {
        problem: `That link points at ${JSON.stringify(page.ref)}, a branch or tag, which can change. On GitHub, open the commit you want and copy the link from there.`
      }
  if (fromLink && fromCommit && fromLink.repo.toLowerCase() !== fromCommit.repo.toLowerCase())
    return { problem: 'The two links are for different repositories. Clear one of them.' }
  const page = fromLink ?? fromCommit
  if (page) repo = page.repo
  else if (!/^https:\/\/[^\s/?#]+\/[^\s?#]+$/.test(repo)) return { problem: 'Paste the repository’s https:// address.' }
  if (fromCommit?.ref) sha = fromCommit.ref
  if (fromLink?.ref) {
    if (sha && sha.toLowerCase() !== fromLink.ref.toLowerCase())
      return { problem: 'The link and the commit box name different commits. Clear one of them.' }
    sha = fromLink.ref
  }
  sha = sha.toLowerCase()
  if (!path) path = fromLink?.path || fromCommit?.path || ''
  if (!COMMIT.test(sha))
    return {
      problem:
        'Give the full 40-character commit. A branch or tag can change after you looked at it; a commit can’t.'
    }
  if (path.split('/').some((part) => part === '..' || part.includes('\\') || part.includes(':')))
    return { problem: 'The folder is a path inside the repository, like plugins/my-plugin.' }
  return { source: { kind: 'git', url: repo.replace(/\/+$/, ''), commit: sha, ...(path ? { path } : {}) } }
}

/** "Updated 3 hours ago", for an index's last fetch (the engine's ISO 8601 time). */
export function fetchedText(fetchedAt: string | null, nowMs: number): string {
  const at = fetchedAt ? Date.parse(fetchedAt) : NaN
  if (!Number.isFinite(at)) return 'not fetched yet'
  const s = Math.max(0, Math.round((nowMs - at) / 1000))
  if (s < 90) return 'updated just now'
  const m = Math.round(s / 60)
  if (m < 90) return `updated ${m} minutes ago`
  const h = Math.round(m / 60)
  if (h < 36) return `updated ${h} hours ago`
  return `updated ${Math.round(h / 24)} days ago`
}

export function formatBytes(n: number | undefined): string {
  if (typeof n !== 'number' || !Number.isFinite(n) || n < 0) return ''
  if (n >= GB) return `${Math.round((n / GB) * 10) / 10} GB`
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`
  return `${Math.max(1, Math.round(n / 1e3))} KB`
}

/** How the screen names an index: the bundled one, or its address. */
export function indexName(url: string): string {
  if (url === 'bundled') return 'The list that came with Clips Kitty'
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/** An event or game slug as words: "team_wipe" → "Team wipe". */
export function slugLabel(slug: string): string {
  const words = slug.replace(/[_-]+/g, ' ').trim()
  return words ? words[0].toUpperCase() + words.slice(1) : slug
}

/** The automated checks an index build ran on a listing (plugins/registry.py). */
export const CHECK_LABELS: Record<string, string> = {
  manifest_valid: 'The manifest passed Clips Kitty’s checks',
  publisher_is_repository_owner: 'The publisher owns the GitHub repository',
  commit_pinned: 'Each version is pinned to one commit',
  public_at_commit: 'The files were public at that commit'
}

/** The checks an index reports for a listing, as lines to show: only the
 *  ones Clips Kitty's index build runs (an index can carry any key, so an
 *  unknown one, such as a claimed audit, is not shown as a tick), and a
 *  failed one kept, as failed, rather than dropped. */
export function checkLines(checks: Record<string, unknown> | undefined): { text: string; ok: boolean }[] {
  return Object.entries(CHECK_LABELS)
    .filter(([key]) => checks && key in checks)
    .map(([key, text]) => ({ text, ok: checks?.[key] === true }))
}
