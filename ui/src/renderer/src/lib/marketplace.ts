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
  /** "Clips Kitty hands this over" or "the developer says so". */
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
  /** What it can be chosen for, in pill words: "Finds moments", "Understands moments", "Rates moments",
   *  or "Understands what it finds" for a finder that describes its own moments. */
  steps: string[]
  /** How long it may take to rate or understand a video's moments, for a plugin that can; else null. */
  time_limit: string | null
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
  /** Projects it builds on, as its manifest credits them. */
  based_on?: BasedOn[]
  /** What it takes in and gives back (video, transcript, moments; ranges,
   *  context, ratings), which say what a job can choose it for (lib/steps.ts offers). */
  inputs?: string[]
  outputs?: string[]
}

export interface ListedVersion {
  version: string
  commit: string
  tag?: string
  /** The games and their versions it was tested with, as the listing says. */
  tested_with?: string | { game?: string; version?: string }[]
  date?: string
  requires?: { clips_kitty?: string; plugin_api?: number | string }
  permissions?: string[]
  /** Why this version can't run on this Clips Kitty, or null. */
  problem_here?: string | null
  /** The automated compatibility check of this version at this commit, when one ran. */
  compatibility?: CompatibilityRecord
}

/** One plugin in a registry index, as GET /marketplace returns it. */
export interface Listing extends PluginInfo {
  publisher: string
  latest: string
  versions: ListedVersion[]
  /** The index it came from: "bundled", Clips Kitty's online list (ONLINE_LIST) or an address from settings. */
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
  /** "official" or "community", then "compatible" and "featured" when they apply. */
  badges?: string[]
  featured?: { reason: string; date: string }
  /** Its section in Awesome Clips Kitty, such as "gaming/valorant". */
  section?: string
  metrics?: Metrics
  discussions_url?: string
}

/** A project a plugin builds on (the manifest's `based_on`). */
export interface BasedOn {
  name: string
  url: string
  license: string
  /** runs: starts it as a separate program; includes-code: contains its code; port: rewrites it. */
  how: 'runs' | 'includes-code' | 'port' | string
}

/** The numbers the catalog carries, each from its own source (plugins/catalog.entry_metrics). */
export interface Metrics {
  github?: { stars?: number; pushed_at?: string; archived?: boolean; has_discussions?: boolean; discussions?: number }
  /** Clips Kitty installs, from the install counter (listings only). */
  installs?: number
  /** Per Hugging Face model: downloads in the last 30 days, and likes. */
  models?: Record<string, { downloads?: number; likes?: number; last_modified?: string }>
  /** "archived", or "no commits since <date>". */
  stale?: string
}

/** One automated compatibility check of one version (scripts/check_compatibility.py). */
export interface CompatibilityRecord {
  version: string
  commit: string
  app_version: string
  plugin_api: number | null
  checked_at: string
  checks: Record<string, boolean>
  passed: boolean
  moments?: number
  note?: string
}

export interface CatalogSection {
  id: string
  title: string
  description?: string
}

/** An app, model, workflow, integration or tool in Awesome Clips Kitty (GET /marketplace/catalog). */
export interface CatalogEntry {
  id: string
  kind: 'app' | 'model' | 'workflow' | 'integration' | 'tool' | string
  name: string
  description: string
  section: string
  relationship: 'built-with' | 'related' | string
  license: string
  license_note?: string
  /** download: the page people download it from; homepage: its own website (plugins/catalog.py checks both). */
  source: { github?: string; path?: string; huggingface?: string; url?: string; homepage?: string; download?: string }
  models?: { huggingface: string }[]
  platforms?: string[]
  runs?: 'local' | 'cloud' | 'both' | string
  /** installer: people download it and run its installer; technical: it needs the command line or Python. */
  setup?: 'installer' | 'technical' | string
  tags?: string[]
  games?: string[]
  sports?: string[]
  uses?: 'api' | 'sdk' | 'both' | string
  /** The listed pipeline or plugin that runs it inside Clips Kitty. */
  adapter?: string
  adapter_listed?: boolean
  warning?: string
  added?: string
  checked?: string
  badges: string[]
  featured?: { reason: string; date: string }
  metrics: Metrics
  discussions_url?: string
  unofficial: string | null
  index: string
}

export interface CatalogResponse {
  entries: CatalogEntry[]
  sections: Record<string, { sections: CatalogSection[]; wanted?: { section: string; idea: string }[] }>
  kinds: { id: string; title: string }[]
  relationships: Record<string, string>
  badges: Record<string, { label: string; meaning: string }>
  /** When the numbers were read, YYYY-MM-DD; null when never. */
  metrics_at: string | null
}

/** GET /marketplace/counting. */
export interface Counting {
  enabled: boolean
  /** settings.yaml switches it off for this Windows account. */
  locked_off: boolean
  /** An index names a counter address, so something would be sent. */
  active: boolean
  text: string
}

export interface MarketplaceIndex {
  url: string
  /** The copy bundled with the app, Clips Kitty's online list, or one from settings. */
  kind?: 'bundled' | 'online' | 'other'
  /** When the engine last fetched it, as an ISO 8601 UTC time; null when never. */
  fetched_at: string | null
  cached: boolean
  plugins?: number
}

/** GET /marketplace/online: Clips Kitty's online list (plugins/registry.py online_status). */
export interface OnlineList {
  url: string
  /** Whether the Marketplace checks it by itself, once a day, when it opens. */
  automatic: boolean
  /** When the cached copy was fetched; null when never. */
  fetched_at: string | null
  /** Whether that copy is in use: one older than the list that came with this
   *  version of Clips Kitty is set aside (it would add nothing). */
  in_use?: boolean
  /** When a check was last tried, and why it failed (null when it worked). */
  tried_at: string | null
  error: string | null
}

export interface MarketplaceResponse {
  plugins: Listing[]
  indexes: MarketplaceIndex[]
  online?: OnlineList
  categories: string[]
  kinds: Record<string, 'built' | 'planned'>
  sections?: CatalogResponse['sections']
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
  /** Only in the answer to Install: whether this install was counted. */
  counted?: boolean
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
  /** What it will now also do, in pill words ("Rates moments"). */
  added_steps: string[]
}

/** What installing would do (POST /plugins/plan). */
export interface PluginPlan {
  plan_id: string | null
  ok: boolean
  errors: string[]
  /** In plain words, for the person installing. */
  warnings: string[]
  /** For Technical details: the manifest's own warnings, files that couldn't be fetched. */
  technical?: string[]
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
  const official = details.tier === 'official' || details.tier === 'listed-official'
  const tone: Tone = official ? 'ok' : details.tier === 'listed' ? 'info' : 'warn'
  const title =
    details.tier === 'official'
      ? 'Ships with Clips Kitty.'
      : details.tier === 'listed-official'
        ? 'Made by the Clips Kitty project and listed in Awesome Clips Kitty.'
        : details.tier === 'listed'
          ? 'Made by someone outside the Clips Kitty project and installed from a list. Nobody at Clips Kitty has read its code.'
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
 *  (hardwareFit) and anything the engine says stops it running here. The
 *  installed app runs Python plugins on its own Python, so a missing Python
 *  only ever shows up as a problem the engine reports (a source checkout
 *  without one). */
export function needLines(
  req: Requirements | undefined,
  hw: Hardware | null,
  details: Pick<PluginDetails, 'needs_python'>,
  problems: ProblemHere[] | null | undefined
): FitLine[] {
  const out = hardwareFit(req, hw)
  const python = problems?.find((p) => p.need === 'python')
  if (details.needs_python && python) out.push({ text: python.text, fit: 'no' })
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
    out.push('I trust where this pipeline comes from. Clips Kitty has not checked it, and it can do anything I can do on this PC.')
  else if (d.tier === 'listed')
    out.push('I understand nobody at Clips Kitty has read this pipeline’s code, and it can do anything I can do on this PC.')
  if (d.data_warnings.length > 0 || d.execution === 'remote' || d.execution === 'hybrid')
    out.push('I understand this pipeline sends data off this PC, as the warnings above say.')
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
  if (u.direction === 'update') out.push({ text: `Updates ${u.from} to ${to}. ${u.from} is kept, so you can go back to it.`, tone: 'info' })
  else if (u.direction === 'downgrade')
    out.push({ text: `Goes back from ${u.from} to the older ${to}. ${u.from} is kept, so you can go back to it.`, tone: 'warn' })
  else out.push({ text: `Replaces the installed copy of ${u.from}.`, tone: 'info' })
  const labels = new Map(plan.details.permissions.map((p) => [p.id, p.label]))
  for (const p of u.added_permissions) out.push({ text: `New permission: ${labels.get(p) ?? p}`, tone: 'warn' })
  if (u.added_hosts.length) out.push({ text: `Now connects to: ${u.added_hosts.join(', ')}`, tone: 'warn' })
  for (const w of u.added_data_warnings) out.push({ text: `New: ${w}`, tone: 'danger' })
  if (u.execution_changed)
    out.push({ text: `Where it runs has changed: ${plan.details.execution_text || 'not stated'}`, tone: 'warn' })
  // A rater's scores decide which clips are made, so a new step is said before it installs.
  for (const s of u.added_steps ?? []) out.push({ text: `Now also: ${s}`, tone: 'warn' })
  if (u.removed_permissions.length)
    out.push({ text: `No longer asks for: ${u.removed_permissions.join(', ')}`, tone: 'ok' })
  if (u.removed_hosts.length) out.push({ text: `No longer connects to: ${u.removed_hosts.join(', ')}`, tone: 'ok' })
  return out
}

/** The installed pipelines a job can use: turned on, able to run here, not
 *  blocked, and able to find moments. One that only rates or understands
 *  moments others found is chosen under Rate & understand instead
 *  (lib/steps.ts); one that doesn't say what it gives back is a finder. */
export function usablePipelines(plugins: InstalledPlugin[]): InstalledPlugin[] {
  return plugins.filter(
    (p) =>
      (p.kind ?? 'pipeline') === 'pipeline' &&
      p.enabled &&
      !p.problem &&
      p.flag?.severity !== 'blocked' &&
      (p.outputs ?? ['ranges']).includes('ranges')
  )
}

/** What each step pill means (plugins/permissions.py STEP_WORDS), as its title. */
const STEP_TITLES: Record<string, string> = {
  'Finds moments':
    'It picks a video’s moments itself, in place of Clips Kitty’s own scoring. Turn on Pipeline when you add a video to use it.',
  'Understands moments':
    'It says what happens in each moment found by Clips Kitty or a pipeline, and Clips Kitty uses that when writing titles. Turn on Rate & understand when you add a video.',
  'Understands what it finds': 'It says what happens in the moments it finds, for the titles.',
  'Rates moments':
    'It scores each moment found by Clips Kitty or a pipeline. Turn on Rate & understand when you add a video.'
}

/** A step pill (details.steps, in the engine's words) as a badge in the info tone. */
export function stepBadge(words: string): Badge {
  return { label: words, tone: 'info', title: STEP_TITLES[words] ?? '' }
}

/** The details panel's lines about a plugin that rates or understands
 *  moments: what its answers change, and its time limit (the engine's words). */
export function stepLines(details: Pick<PluginDetails, 'steps' | 'time_limit'>): string[] {
  const out: string[] = []
  const steps = details.steps ?? []
  if (steps.includes('Rates moments'))
    out.push('Its scores decide which clips are made and their order, and which are posted when a channel posts only the best few.')
  if (steps.includes('Understands moments') || steps.includes('Understands what it finds'))
    out.push('What it says about a moment goes into the request that writes your titles.')
  if (details.time_limit) out.push(details.time_limit)
  return out
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
  if (spec.type === 'secret') return { problem: 'a secret is set in the pipeline’s settings, not in a job' }
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

/** How long ago an ISO 8601 time was, in words ("3 hours ago"), or null when it isn't one. */
export function agoText(stamp: string | null, nowMs: number): string | null {
  const at = stamp ? Date.parse(stamp) : NaN
  if (!Number.isFinite(at)) return null
  const s = Math.max(0, Math.round((nowMs - at) / 1000))
  if (s < 90) return 'just now'
  const m = Math.round(s / 60)
  if (m < 90) return `${m} minutes ago`
  const h = Math.round(m / 60)
  if (h < 36) return `${h} hours ago`
  return `${Math.round(h / 24)} days ago`
}

export function fetchedText(fetchedAt: string | null, nowMs: number): string {
  const ago = agoText(fetchedAt, nowMs)
  return ago ? `updated ${ago}` : 'not fetched yet'
}

/** What the Marketplace says about Clips Kitty's online list, in one or two
 *  sentences. `manage` is whether this window can check it (the desktop app
 *  can; the interface in a plain browser can't, so it isn't sent to a button). */
export function onlineText(online: OnlineList | undefined, nowMs: number, manage = true): string {
  const fetched = agoText(online?.fetched_at ?? null, nowMs)
  const tried = agoText(online?.tried_at ?? null, nowMs)
  const used = fetched !== null && online?.in_use !== false
  const shows = used ? `This shows its copy from ${fetched}.` : 'This shows the list that came with Clips Kitty.'
  if (online?.error) {
    const when = tried ? `The check for new pipelines ${tried} didn’t work.` : 'The last check for new pipelines didn’t work.'
    return `${when} ${online.error} ${shows}`
  }
  if (used) return `Clips Kitty’s online list, updated ${fetched}.`
  if (fetched) return 'This shows the list that came with Clips Kitty, which is newer than its online copy.'
  return `${shows} ${manage ? 'Check for new pipelines to get the online list.' : 'The Clips Kitty desktop app gets the online list.'}`
}

export function formatBytes(n: number | undefined): string {
  if (typeof n !== 'number' || !Number.isFinite(n) || n < 0) return ''
  if (n >= GB) return `${Math.round((n / GB) * 10) / 10} GB`
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`
  return `${Math.max(1, Math.round(n / 1e3))} KB`
}

/** Clips Kitty's own list online: the catalog's index on the project's main
 *  branch (plugins/registry.py ONLINE_URL; a test keeps the two the same). */
export const ONLINE_LIST =
  'https://raw.githubusercontent.com/ColinGPT9/clips-studio/main/awesome-clips-kitty/index.json'

/** Whether a listing came from one of Clips Kitty's own lists (bundled or online). */
export function isOurList(url: string | undefined): boolean {
  return url === 'bundled' || url === ONLINE_LIST
}

/** How the screen names an index: one of Clips Kitty's own, or someone else's by its address. */
export function indexName(url: string): string {
  if (url === 'bundled') return 'The list that came with Clips Kitty'
  if (url === ONLINE_LIST) return 'Clips Kitty’s online list'
  try {
    return `A list from ${new URL(url).host} (not Clips Kitty’s)`
  } catch {
    return url
  }
}

/** Which list a listing is in, and how fresh Clips Kitty's online copy is. */
export function listedInText(url: string, online: OnlineList | undefined, nowMs: number): string {
  if (url === 'bundled') return 'In the list that came with Clips Kitty.'
  if (url === ONLINE_LIST) {
    const ago = agoText(online?.fetched_at ?? null, nowMs)
    return ago ? `In Clips Kitty’s online list (updated ${ago}).` : 'In Clips Kitty’s online list.'
  }
  try {
    return `In a list from ${new URL(url).host} (not Clips Kitty’s).`
  } catch {
    return `In a list from ${url} (not Clips Kitty’s).`
  }
}

/** Where an install comes from, in a few words. The engine's exact line
 *  (address, folder and commit) goes under Technical details. */
export function sourceLine(source: Record<string, string> | undefined): string {
  const listed = source?.listed_in
  if (listed) {
    if (isOurList(listed)) return 'From Clips Kitty’s list'
    try {
      return `From a list on ${new URL(listed).host} (not Clips Kitty’s)`
    } catch {
      return 'From a list that isn’t Clips Kitty’s'
    }
  }
  if (source?.kind === 'folder') return 'From a folder on this PC'
  if (source?.kind === 'git') return 'From a link'
  return ''
}

/** An event or game slug as words: "team_wipe" → "Team wipe". */
export function slugLabel(slug: string): string {
  const words = slug.replace(/[_-]+/g, ' ').trim()
  return words ? words[0].toUpperCase() + words.slice(1) : slug
}

/** The automated checks an index build ran on a listing (plugins/registry.py). */
export const CHECK_LABELS: Record<string, string> = {
  manifest_valid: 'Its setup file passed Clips Kitty’s checks',
  publisher_is_repository_owner: 'The developer in its id owns the code’s page on GitHub',
  official_repository: 'The code’s page on GitHub is the Clips Kitty project’s own',
  commit_pinned: 'Each version is fixed and can’t change after it was listed',
  commit_on_branch: 'Each version comes from the developer’s own code, not from someone else’s copy',
  public_at_commit: 'Anyone could read each version’s code'
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


// ---- Awesome Clips Kitty: labels, numbers and credits ------------------------------------------

/** The directory's kinds the Marketplace browses besides pipelines
 *  (plugins/catalog.py DIRECTORY_KINDS). Models are "AI model links" here,
 *  so the tab isn't taken for the app's own Models page. */
export const DIRECTORY_KINDS: Record<string, string> = {
  app: 'Apps',
  model: 'AI model links',
  workflow: 'Workflows',
  integration: 'Integrations',
  tool: 'Tools'
}

/** How a project relates to Clips Kitty, in words (plugins/catalog.py RELATIONSHIPS). */
export const RELATIONSHIP_LABELS: Record<string, string> = {
  'built-for': 'Built for Clips Kitty',
  'built-with': 'Built with Clips Kitty',
  related: 'Related'
}

/** The catalog's labels as badges. "official" and "community" are the
 *  plugin's tier for a listing (tierBadge), so `extraOnly` leaves them out. */
export function catalogBadges(badges: string[] | undefined, extraOnly = false): Badge[] {
  const out: Badge[] = []
  for (const b of badges ?? []) {
    if (b === 'official' && !extraOnly)
      out.push({ label: '✓ Official', tone: 'ok', title: 'Made and maintained by the Clips Kitty project.' })
    else if (b === 'community' && !extraOnly)
      out.push({ label: 'Community', tone: 'info', title: 'Made by someone outside the Clips Kitty project. Nobody at Clips Kitty has read its code.' })
    else if (b === 'compatible')
      out.push({
        label: '✓ Compatible',
        tone: 'ok',
        title:
          'This version passed Clips Kitty’s automated compatibility checks. A technical label, not a security review.'
      })
    else if (b === 'featured') out.push({ label: '★ Featured', tone: 'info', title: 'Picked by a Clips Kitty maintainer.' })
  }
  return out
}

/** 1234 → "1.2k", 1200000 → "1.2M". */
export function shortCount(n: number | undefined): string {
  if (typeof n !== 'number' || !Number.isFinite(n) || n < 0) return ''
  const one = (x: number, unit: string): string => `${(Math.round(x * 10) / 10).toString()}${unit}`
  if (n >= 1e6) return one(n / 1e6, 'M')
  if (n >= 1e3) return one(n / 1e3, 'k')
  return String(Math.round(n))
}

/** Each number as its own line, never added to another. */
export function metricLines(metrics: Metrics | undefined): { text: string; tone: Tone }[] {
  const out: { text: string; tone: Tone }[] = []
  if (!metrics) return out
  if (typeof metrics.installs === 'number')
    out.push({
      text: `${metrics.installs.toLocaleString('en-US')} Clips Kitty ${metrics.installs === 1 ? 'install' : 'installs'}`,
      tone: 'info'
    })
  const gh = metrics.github
  if (gh && typeof gh.stars === 'number') out.push({ text: `★ ${shortCount(gh.stars)} on GitHub`, tone: 'info' })
  if (gh && typeof gh.discussions === 'number' && gh.discussions > 0)
    out.push({ text: `${gh.discussions} ${gh.discussions === 1 ? 'discussion' : 'discussions'} on GitHub`, tone: 'info' })
  for (const [id, m] of Object.entries(metrics.models ?? {})) {
    if (!m || typeof m !== 'object') continue
    const bits = []
    if (typeof m.downloads === 'number') bits.push(`${shortCount(m.downloads)} downloads a month`)
    if (typeof m.likes === 'number') bits.push(`${shortCount(m.likes)} likes`)
    if (bits.length) out.push({ text: `${id} on Hugging Face: ${bits.join(', ')}`, tone: 'info' })
  }
  if (metrics.stale) out.push({ text: `⚠ ${metrics.stale === 'archived' ? 'Archived by its authors' : metrics.stale}`, tone: 'warn' })
  return out
}

/** The one link a directory entry's card offers, as a button: its download
 *  page, its website, its GitHub repository (or folder, or a page of it), its
 *  Hugging Face page, then any other page (plugins/catalog.py _link keeps the
 *  same order). `label` is translated on screen; `host` follows it when there is one. */
export function entryLink(
  entry: Pick<CatalogEntry, 'source'>
): { url: string; label: string; host: string | null } | null {
  const s = entry.source ?? {}
  const download = safeLink(s.download)
  if (download)
    return { url: download, label: 'Download from', host: new URL(download).hostname.replace(/^www\./, '') }
  const homepage = safeLink(s.homepage)
  if (homepage) return { url: homepage, label: 'Website', host: null }
  if (s.github) {
    let url = safeLink(s.github)
    const page = safeLink(s.url)
    if (url && s.path) url = safeLink(`${s.github.replace(/\/+$/, '')}/tree/HEAD/${s.path.replace(/^\/+|\/+$/g, '')}`)
    else if (url && page && (page.startsWith(`${s.github}#`) || page.startsWith(`${s.github}/`))) url = page
    return url ? { url, label: 'Code page on GitHub', host: null } : null
  }
  if (s.huggingface && /^[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(s.huggingface))
    return { url: `https://huggingface.co/${s.huggingface}`, label: 'Model page on Hugging Face', host: null }
  const url = safeLink(s.url)
  return url ? { url, label: 'Website', host: null } : null
}

const BASED_ON_HOW: Record<string, string> = {
  runs: 'runs it as a separate program',
  'includes-code': 'includes its code',
  port: 'is a rewrite of it'
}

/** The projects a plugin credits, for "Built on" (https links only). */
export function basedOnLines(items: BasedOn[] | undefined): { name: string; url: string | null; text: string }[] {
  return (items ?? [])
    .filter((b) => b && typeof b.name === 'string')
    .map((b) => ({
      name: b.name,
      url: safeLink(b.url),
      text: `${b.license} · ${BASED_ON_HOW[b.how] ? `this pipeline ${BASED_ON_HOW[b.how]}` : b.how}`
    }))
}

const RANGE_PART = /^\s*(>=|<=|==|!=|>|<|~=)?\s*(\d+(?:\.\d+){0,2})\s*$/

/** A version range from a manifest in words, the way
 *  manifest.version_satisfies reads it: ">=2.0" → "2.0 and newer",
 *  ">=2.0, <3" → "2.0 up to, not including, 3". Null when there is none or
 *  it isn't a range. */
export function rangeText(range: string | undefined | null): string | null {
  if (typeof range !== 'string' || !range.trim()) return null
  let from = ''
  let to = ''
  const also: string[] = []
  for (const part of range.split(',')) {
    const m = RANGE_PART.exec(part)
    if (!m) return null
    const [, op = '==', v] = m
    if (op === '>=') from = v
    else if (op === '>') from = `newer than ${v}`
    else if (op === '<') to = `up to, not including, ${v}`
    else if (op === '<=') to = `up to ${v}`
    else if (op === '!=') also.push(`except ${v}`)
    else if (op === '==') also.push(`${v} only`)
    else {
      // ~=2.1 is >=2.1 and <3; ~=2.1.0 is >=2.1.0 and <2.2
      const upper = v.split('.').map(Number)
      upper.splice(Math.max(1, upper.length - 1))
      upper[upper.length - 1] += 1
      from = v
      to = `up to, not including, ${upper.join('.')}`
    }
  }
  const span = from && to ? `${from} ${to}` : from ? (from.startsWith('newer') ? from : `${from} and newer`) : to
  return [span, ...also].filter(Boolean).join(', ')
}

/** One sentence for a version's compatibility record, or null when it has none. */
export function compatibilityText(record: CompatibilityRecord | undefined | null): { text: string; tone: Tone } | null {
  if (!record) return null
  const when = String(record.checked_at || '').slice(0, 10)
  if (record.passed)
    return {
      text: `✓ Compatible: version ${record.version} passed the automated checks on Clips Kitty ${record.app_version}${when ? ` (${when})` : ''}. A technical check, not a security review.`,
      tone: 'ok'
    }
  return {
    text: `Version ${record.version} didn’t pass the automated checks on Clips Kitty ${record.app_version}${record.note ? `: ${record.note}` : ''}.`,
    tone: 'warn'
  }
}

/** Entries grouped by their kind's sections, in the catalog's order. An
 *  entry in a section the list doesn't know goes under "Other". In each
 *  section, the ones that need technical setup come after the others. */
export function groupBySection(
  entries: CatalogEntry[],
  sections: CatalogSection[] | undefined
): { id: string; title: string; description?: string; entries: CatalogEntry[] }[] {
  const out: { id: string; title: string; description?: string; entries: CatalogEntry[] }[] = []
  const known = new Set<string>()
  const easyFirst = (list: CatalogEntry[]): CatalogEntry[] => [
    ...list.filter((e) => e.setup !== 'technical'),
    ...list.filter((e) => e.setup === 'technical')
  ]
  for (const s of sections ?? []) {
    known.add(s.id)
    const own = entries.filter((e) => e.section === s.id)
    if (own.length) out.push({ id: s.id, title: s.title, description: s.description, entries: easyFirst(own) })
  }
  const rest = entries.filter((e) => !known.has(e.section))
  if (rest.length) out.push({ id: '', title: 'Other', entries: easyFirst(rest) })
  return out
}
