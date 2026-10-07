import { useEffect, useState } from 'react'
import { API_BASE } from './api'
import type {
  CatalogResponse,
  Counting,
  InstalledPlugin,
  MarketplaceResponse,
  ModelPlan,
  ModelStatus,
  ModelsOverview,
  OnlineList,
  PluginPlan,
  PluginsResponse
} from './marketplace'
import { usablePipelines } from './marketplace'

/** The plugin manager's routes (plugins/api.py). The ones that fetch,
 *  install or change plugins need the session secret, which only the
 *  desktop app's own pages are given (preload `pluginSession`). */

/** Sent on window after anything changes what is installed, so the Generate
 *  bar's pipeline list and the Marketplace stay in step. */
export const PLUGINS_CHANGED = 'plugins-changed'

/** Sent on window to show the Marketplace (App switches to it). */
export const OPEN_MARKETPLACE = 'open-marketplace'

export type MarketplaceTab = 'browse' | 'installed' | 'add'

let wantedTab: MarketplaceTab | null = null

/** Show the Marketplace, at one of its tabs. */
export function openMarketplace(tab: MarketplaceTab = 'browse'): void {
  wantedTab = tab
  window.dispatchEvent(new Event(OPEN_MARKETPLACE))
}

/** The tab last asked for with openMarketplace, once; null when none was. */
export function takeMarketplaceTab(): MarketplaceTab | null {
  const tab = wantedTab
  wantedTab = null
  return tab
}

let secret: Promise<string> | null = null

function sessionSecret(): Promise<string> {
  secret ??= (window.studio?.pluginSession?.() ?? Promise.resolve('')).catch(() => '')
  return secret
}

/** Whether this window can install and change plugins (the desktop app can;
 *  the interface opened in a plain browser can't). */
export async function canManage(): Promise<boolean> {
  return Boolean(await sessionSecret())
}

/** The engine's own words when it refuses (FastAPI's `detail`). */
async function failure(res: Response): Promise<Error> {
  const text = await res.text().catch(() => '')
  try {
    const detail = JSON.parse(text).detail
    if (typeof detail === 'string') return new Error(detail)
  } catch {
    // not JSON: fall through to the raw text
  }
  return new Error(text.slice(0, 300) || `The engine answered ${res.status}`)
}

async function call<T>(path: string, init: RequestInit = {}, guarded = false): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (guarded) {
    const s = await sessionSecret()
    if (!s) throw new Error('Installing and changing plugins needs the Clips Kitty desktop app.')
    headers['X-Clips-Kitty-Session'] = s
  }
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) throw await failure(res)
  return res.json() as Promise<T>
}

function changed<T>(value: T): T {
  window.dispatchEvent(new Event(PLUGINS_CHANGED))
  return value
}

const one = (id: string): string => id.split('/').map(encodeURIComponent).join('/')

export type PluginSource =
  | { kind: 'folder'; path: string }
  | { kind: 'git'; url: string; commit: string; path?: string }
  | { kind: 'index'; id: string; version?: string }

export const plugins = {
  list: () => call<PluginsResponse>('/plugins'),
  marketplace: (q: { q?: string; category?: string; tag?: string; kind?: string } = {}) => {
    const params = new URLSearchParams()
    for (const [k, v] of Object.entries(q)) if (v) params.set(k, v)
    const qs = params.toString()
    return call<MarketplaceResponse>(`/marketplace${qs ? `?${qs}` : ''}`)
  },
  /** Awesome Clips Kitty's apps, models, workflows, integrations and tools. */
  catalog: (q: { q?: string; kind?: string; section?: string } = {}) => {
    const params = new URLSearchParams()
    for (const [k, v] of Object.entries(q)) if (v) params.set(k, v)
    const qs = params.toString()
    return call<CatalogResponse>(`/marketplace/catalog${qs ? `?${qs}` : ''}`)
  },
  /** Check for new listings. `automatic`: the Marketplace opening, which
   *  checks Clips Kitty's online list only when it is due (once a day). */
  refresh: (automatic = false) =>
    call<{ checked: boolean; indexes: { url: string; ok: boolean; error?: string | null }[] }>(
      '/marketplace/refresh',
      { method: 'POST', body: JSON.stringify({ automatic }) },
      true
    ),
  /** Whether the Marketplace checks Clips Kitty's online list by itself, and switching it. */
  online: () => call<OnlineList>('/marketplace/online'),
  setOnline: (enabled: boolean) =>
    call<OnlineList>('/marketplace/online', { method: 'PUT', body: JSON.stringify({ enabled }) }, true),
  /** Whether installs from the Marketplace are counted (anonymously), and switching it. */
  counting: () => call<Counting>('/marketplace/counting'),
  setCounting: (enabled: boolean) =>
    call<Counting>('/marketplace/counting', { method: 'PUT', body: JSON.stringify({ enabled }) }, true),
  plan: (source: PluginSource) =>
    call<PluginPlan>('/plugins/plan', { method: 'POST', body: JSON.stringify({ source }) }, true),
  install: (planId: string) =>
    call<InstalledPlugin>('/plugins/install', { method: 'POST', body: JSON.stringify({ plan_id: planId }) }, true).then(
      changed
    ),
  setEnabled: (id: string, on: boolean) =>
    call<InstalledPlugin>(`/plugins/${one(id)}/${on ? 'enable' : 'disable'}`, { method: 'POST' }, true).then(changed),
  setPinned: (id: string, on: boolean) =>
    call<InstalledPlugin>(`/plugins/${one(id)}/${on ? 'pin' : 'unpin'}`, { method: 'POST' }, true).then(changed),
  rollback: (id: string) =>
    call<InstalledPlugin>(`/plugins/${one(id)}/rollback`, { method: 'POST' }, true).then(changed),
  remove: (id: string) =>
    call<{ removed: string; files_left: boolean; keys_removed: boolean }>(
      `/plugins/${one(id)}`,
      { method: 'DELETE' },
      true
    ).then(changed),
  setSecrets: (id: string, values: Record<string, string | null>) =>
    call<{ set: string[] }>(`/plugins/${one(id)}/secrets`, { method: 'PUT', body: JSON.stringify({ values }) }, true).then(
      changed
    ),
  /** Every model the installed plugins list, once, with where it is. */
  models: () => call<ModelsOverview>('/plugin-models'),
  /** What downloading one would fetch; downloads nothing. */
  modelPlan: (plugin: string, model: string) =>
    call<ModelPlan>('/plugin-models/plan', { method: 'POST', body: JSON.stringify({ plugin, model }) }, true),
  downloadModel: (plugin: string, model: string, allowPickle: boolean) =>
    call<ModelStatus>(
      '/plugin-models/download',
      { method: 'POST', body: JSON.stringify({ plugin, model, allow_pickle: allowPickle }) },
      true
    ).then(changed)
}

/** The installed pipelines a job can use, kept current as plugins change.
 *  Null until the engine answers; empty when none is installed and on. The
 *  Generate bar's Pipeline switch stays hidden in both cases. */
export function usePipelines(): InstalledPlugin[] | null {
  const [list, setList] = useState<InstalledPlugin[] | null>(null)
  useEffect(() => {
    let live = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = (): void => {
      plugins
        .list()
        .then((r) => {
          if (live) setList(usablePipelines(r.plugins))
        })
        .catch(() => {
          if (live) timer = setTimeout(load, 5000) // the engine is still starting
        })
    }
    load()
    window.addEventListener(PLUGINS_CHANGED, load)
    return () => {
      live = false
      if (timer) clearTimeout(timer)
      window.removeEventListener(PLUGINS_CHANGED, load)
    }
  }, [])
  return list
}
