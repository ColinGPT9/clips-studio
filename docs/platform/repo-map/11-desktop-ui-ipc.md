# Desktop UI, IPC and UI conventions (Clips Kitty, `ui/`)

Repo: `/home/user/clips-studio`, branch `claude/open-platform-w4eh9g` (HEAD `1d13723`). All paths below are repo-relative. "(inferred)" marks conclusions not stated verbatim in the code.

## 1. Stack and build

| Item | Value | Source |
|---|---|---|
| Framework | Electron 31 + electron-vite 2 + Vite 5 + React 18 + TypeScript 5.5 + Tailwind CSS v4 (`@tailwindcss/vite`) | `ui/package.json:17-33` |
| Runtime deps | only `electron-updater`, `react`, `react-dom` (no router, no state library, no UI kit, no i18n library) | `ui/package.json:17-21` |
| Bundles | `main`, `preload`, `renderer` (renderer plugins: react + tailwindcss) | `ui/electron.vite.config.ts` |
| Entry | `main: ./out/main/index.js` | `ui/package.json:8` |
| Scripts | `dev` (node scripts/dev.cjs → `npx electron-vite dev`), `dev:web` (plain `vite`, browser-only), `build` (`electron-vite build`), `typecheck` (`tsc --noEmit -p tsconfig.web.json && tsc --noEmit -p tsconfig.node.json`), `package` (`electron-vite build && electron-builder --win`) | `ui/package.json:10-16`, `ui/scripts/dev.cjs` |
| No test script | there is no `test` entry and no test runner in devDependencies | `ui/package.json:10-16` |
| tsconfig split | `tsconfig.web.json` covers `src/renderer/src/**/*` (jsx react-jsx, strict, resolveJsonModule); `tsconfig.node.json` covers `src/main`, `src/preload`, `electron.vite.config.ts` | `ui/tsconfig.web.json`, `ui/tsconfig.node.json` |
| Browser-only dev | `ui/vite.config.mts` serves `src/renderer` on 0.0.0.0:5173 and relaxes CSP for Fast Refresh; used by docker-compose `ui` service (node:20-slim) | `ui/vite.config.mts`, `docker-compose.yml:48-60` |
| Formatting | Prettier: no semicolons, single quotes, printWidth 100, no trailing commas | `ui/.prettierrc.json` |
| CI | job `ui` on ubuntu-latest: node 20, `npm ci`, `npm run typecheck`, `npm run build` | `.github/workflows/ci.yml:100-123` |
| Packaging | NSIS web installer + zip (x64), appId `com.clipsstudio.app`, productName "Clips Kitty"; frozen Python engine shipped as `extraResources` from `../build/dist/backend` → `resources/backend/`; update feed = generic HTTPS on Hugging Face; MSIX block for the Store | `ui/electron-builder.yml:24-57,70-77,109-125,165-168` |

### Can typecheck/build run in this Linux sandbox?

- `ui/node_modules` does **not** exist here (checked: `ls -d ui/node_modules` → absent). `package-lock.json` is present (`ui/package-lock.json`).
- Node 22.22.0 and npm 10.9.4 are available at `/opt/node22/bin`; a global `typescript` exists in `/opt/node22/lib/node_modules` (also `prettier`, `eslint`). But `react`, `@types/react`, `electron`, `electron-vite`, `@tailwindcss/vite` are not installed, so `npm run typecheck` / `npm run build` need `npm ci` first (not run here, per the read-only rule).
- Typecheck and `electron-vite build` are headless (tsc + Vite/esbuild); CI runs them on ubuntu-latest without a display (`.github/workflows/ci.yml:100-123`), so they should work in a Linux sandbox once deps are installed. Only `npm run dev` (Electron window) needs a display (`CONTRIBUTING.md:62`).

## 2. How the renderer talks to the engine

**HTTP + WebSocket to `127.0.0.1:8765`; IPC only for native affordances.**

| Channel | Where | Details |
|---|---|---|
| REST | `ui/src/renderer/src/lib/api.ts:49` `API_BASE = 'http://127.0.0.1:8765'`; `request<T>()` at `:64-74` wraps `fetch`, JSON headers, throws `Error("<status> <path>: <body>")` on non-2xx | Every page/component calls `api.<method>()`; there is one `api` object (`api.ts:76` onward) |
| WebSocket | `ui/src/renderer/src/lib/useEvents.ts:4` `ws://127.0.0.1:8765/ws`; hook `useEvents(onEvent)` auto-reconnects every 2 s | Event union `StudioEvent` in `ui/src/renderer/src/lib/types.ts:642` |
| Media | `api.mediaUrl(clipId)` → `${API_BASE}/media/<id>`; frames via `/clips/<id>/source-frame`, etc. | `api.ts:329,362-371` |
| CSP | `connect-src 'self' http://127.0.0.1:8765 ws://127.0.0.1:8765; media-src http://127.0.0.1:8765; img-src 'self' data: http://127.0.0.1:8765` — any new remote host (e.g. a GitHub registry fetched from the renderer) is blocked by CSP; such fetches must go through the engine or the main process (inferred) | `ui/src/renderer/index.html:6-9` |
| Missed-event fallback | `useJobWatch` polls `/jobs` after 45 s of silence (server event queue may drop events) | `ui/src/renderer/src/lib/useJobWatch.ts:28-29,41-71` |
| Port | `API_PORT = 8765` hard-coded in main; engine spawned with `serve --port 8765` | `ui/src/main/index.ts:19,228,235` |

Main process also calls the engine over HTTP itself (`GET /automation`, to decide whether to keep running in the tray): `ui/src/main/index.ts:105-116`.

## 3. Main process (`ui/src/main/`)

| Concern | Behaviour | Source |
|---|---|---|
| Engine spawn | Packaged: `resources/backend/api.exe serve --port 8765` (stdio ignored, windowsHide). Dev: `python main.py serve --port 8765` with cwd = repo root (`app.getAppPath()/..`), unless `BACKEND_EXTERNAL=1`. Env adds `PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`, and (packaged) `CLIPS_STUDIO_OLLAMA_HOST=http://127.0.0.1:11435` | `ui/src/main/index.ts:203-244` |
| Bundled Ollama | Packaged only: spawns `resources/backend/_internal/ollama/ollama.exe serve` on port 11435 with `OLLAMA_MODELS` under `%LOCALAPPDATA%\Clips Kitty|Clips Studio\data\models` | `ui/src/main/index.ts:25-29,139-170` |
| Shutdown | `killTree()` uses `taskkill /T /F` on Windows; `stopChildren()` on `window-all-closed` and `before-quit` | `ui/src/main/index.ts:181-201,626-640` |
| Window | 1440×900 (min 1080×700), `backgroundColor #0A1628`, `contextIsolation: true`, `nodeIntegration: false`, preload `../preload/index.js`; loads `ELECTRON_RENDERER_URL` in dev else `../renderer/index.html` | `ui/src/main/index.ts:246-259,319-323` |
| Window-open / navigation | all `window.open` targets go to `shell.openExternal` and are denied in-app | `ui/src/main/index.ts:299-302` |
| Single instance | `requestSingleInstanceLock`, second launch focuses the window | `ui/src/main/index.ts:604-608` |
| Tray | opt-in "keep watching in tray" persisted in `userData/tray.json`; only engaged when `/automation` says channels are being watched | `ui/src/main/index.ts:34-129,264-289` |
| Auto-update | `ui/src/main/updater.ts` uses `electron-updater` against a generic HTTPS feed (channels `latest`/`beta`/`alpha` files), prefs in `userData/update-prefs.json`, disabled for Microsoft Store copies (`distribution.ts`) | `ui/src/main/updater.ts:1-60`, `ui/src/main/distribution.ts:24-33` |
| External URL allow-list | `EXTERNAL_ALLOWED` regex list (ollama.com, provider key pages, github.com/ColinGPT9/clips-studio, Google OAuth, YouTube/Twitch/Kick, upload-post, woopsocial, social sites). **A Marketplace page that opens plugin repo links on github.com for other owners would need new entries here** | `ui/src/main/index.ts:487-570` |
| AppUserModelId | `com.clipsstudio.app` (must match electron-builder appId) | `ui/src/main/index.ts:613` |

### IPC handlers (all `ipcMain.handle`)

| Channel | Purpose | Source |
|---|---|---|
| `tray:get`, `tray:set` | keep-in-tray preference | `ui/src/main/index.ts:118-129` |
| `pick-audio-file`, `pick-video-file`, `pick-video-files`, `pick-image-file`, `pick-folder` | native dialogs returning path(s) | `ui/src/main/index.ts:328-362,423-433,591-598` |
| `pick-thumbnail-image` | returns `{name, data(base64)}` capped at 2 MB, never a path | `ui/src/main/index.ts:384-421` |
| `notify` | text-only desktop notification | `ui/src/main/index.ts:368-380` |
| `open-donate-window` | PayPal popup (or system browser on Store) | `ui/src/main/index.ts:439-479` |
| `open-external` | allow-listed `shell.openExternal` | `ui/src/main/index.ts:562-570` |
| `read-clipboard-key` | clipboard text only if it looks like an API key | `ui/src/main/index.ts:581-586` |
| `get-downloads-path` | `app.getPath('downloads')` | `ui/src/main/index.ts:588` |
| `update:check/download/install/skip/prefs` + push `update:state` | registered inside `setupUpdater(win)`: Store copies get stubs (`updater.ts:156-175`), others the real handlers (`updater.ts:235-271`); state pushed with `send("update:state", …)` | `ui/src/main/updater.ts:141-271`, `ui/src/preload/index.ts:46-59` |

## 4. Preload API surface (`window.studio`)

Exposed by `contextBridge.exposeInMainWorld('studio', {...})` in `ui/src/preload/index.ts:5-61`; typed in `ui/src/renderer/src/env.d.ts:29-66`; browser stand-in in `ui/src/renderer/src/lib/browserShim.ts:22-84` (installed from `main.tsx:12`).

```
window.studio = {
  platform: string
  pickAudioFile(): Promise<string|null>
  pickVideoFile(): Promise<string|null>
  pickVideoFiles(): Promise<string[]>
  pickImageFile(): Promise<string|null>
  pickThumbnailImage(): Promise<{name,data}|{error}|null>
  readClipboardKey(): Promise<string>
  getDownloadsPath(): Promise<string>
  pickFolder(): Promise<string|null>
  openDonateWindow(): Promise<void>
  notify(title, body): Promise<boolean>
  openExternal(url): Promise<boolean>
  tray?: { get(), set(on) }                         // optional in the type
  update: { check(), download(), install(), skip(v), prefs(patch?), onState(fn) → unsubscribe }
}
```

Usage tally (grep): `openExternal` is by far the most used (AICard ×10, YouTubePanel, WoopSocialCard, UploadPostCard, Dashboard, …); `update` in UpdateBanner + Settings; pickers in AddVideos, WatermarkControls, TimelineEditor, FeedbackHub, YouTubeThumbnail; `pickFolder`/`getDownloadsPath` in `lib/exportFolder.ts`.

Nothing else crosses the bridge: no fs, no shell, no arbitrary IPC. A new page needing native access must add an `ipcMain.handle` in `ui/src/main/index.ts`, a method in `ui/src/preload/index.ts`, the type in `env.d.ts`, and a stub in `browserShim.ts` (inferred from how `tray` and `update` were added).

## 5. Renderer layout and pages

```
ui/src/renderer/index.html            CSP + #root
ui/src/renderer/src/main.tsx          installBrowserShim(); applyAppearance(); <AppBoundary><App/>
ui/src/renderer/src/App.tsx           sidebar nav + page switch (state, not a router)
ui/src/renderer/src/theme.css         Tailwind v4 @theme tokens + .card/.btn-accent/.btn-ghost/.input/.label
ui/src/renderer/src/pages/*.tsx       one file per page (7)
ui/src/renderer/src/components/*.tsx  flat; sub-folders queue/ and watch/
ui/src/renderer/src/lib/*.ts          api.ts, types.ts, i18n.ts, hooks, pure helpers
ui/src/renderer/src/locales/*.json    18 dictionaries (en is the key itself)
ui/src/renderer/src/assets/mascot.png
ui/src/renderer/src/env.d.ts          window.studio + UpdateState types
```

### Pages (navigation = `NAV` array + `Page` union in `App.tsx`)

| id | Sidebar label | File | Notes |
|---|---|---|---|
| `dashboard` | Dashboard | `ui/src/renderer/src/pages/Dashboard.tsx` | processed-videos table with search/sort/channel filter, live log, assistant, donate card |
| `queue` | Queue | `ui/src/renderer/src/pages/Queue.tsx` | `AddVideos` (generate bar) + sections Processing / Up next / Failed / Completed of `QueueItem` rows |
| `watch` | Watched channels | `ui/src/renderer/src/pages/Watch.tsx` | `components/watch/*` cards; live dot in nav from `/automation` |
| `studio` | Clip Editor | `ui/src/renderer/src/pages/ClipStudio.tsx` | video picker, `ClipCard` grid, `EditorModal` |
| `creators` | Creators | `ui/src/renderer/src/pages/Creators.tsx` | |
| `models` | Models | `ui/src/renderer/src/pages/Models.tsx` | Ollama models: installed list, pull box, recommendation tables |
| `settings` | Settings | `ui/src/renderer/src/pages/Settings.tsx` | stack of `*Card` components (see §7) |

Sources: `ui/src/renderer/src/App.tsx:20-35` (Page type + NAV), `:143-240` (render). ARCHITECTURE.md §11 (`ARCHITECTURE.md:849-858`) lists the same pages except Watch (doc slightly behind the code).

### Navigation mechanics
- `const [page, setPage] = useState<Page>('dashboard')` in `App.tsx:43`; sidebar buttons call `setPage(item.id)` (`App.tsx:166-189`); the main area renders `{page === 'x' && <X/>}` (`App.tsx:218-230`).
- Cross-page jumps are **window CustomEvents**: `open-queue`, `open-settings`, `open-models`, `open-setup-wizard` (`App.tsx:50-75`); e.g. `window.dispatchEvent(new Event('open-settings'))` in `Models.tsx:32`. A new page would add `open-marketplace` the same way (inferred).
- Language changes re-render in place via `app-language-changed` + `key={locale}` (`App.tsx:125-130,144`).
- Shell-level widgets: `UpdateBanner` (top of main), `FeedbackHub` + `ModelSwitcher` pinned in the sidebar, `SetupWizard` overlay on first run (`App.tsx:191-239`).

## 6. i18n

- Tiny custom layer `ui/src/renderer/src/lib/i18n.ts`: `t(s)` looks the **English source string** up in the active dictionary and falls back to the English (`i18n.ts:100-103`). Dictionaries are flat JSON keyed by English (`ui/src/renderer/src/locales/es.json`, ~116 keys; `docs/TRANSLATING.md` "Where the words live").
- Active locale: `localStorage['app-language']` or OS `navigator.language` (`i18n.ts:75-87`); `setAppLanguage()` swaps the dict and dispatches `app-language-changed` (`i18n.ts:92-96`).
- Adding strings: wrap UI text in `t('English text')`; **no file needs editing** for English. Translations are optional, added per locale JSON; untranslated keys render in English (`i18n.ts:1-9`). Adding a locale = new JSON + import + entry in `LOCALES`/`LANGUAGE_NAMES` (`i18n.ts:10-56`, `docs/EXTENDING.md:30-32`).
- Not every page is translated: `Models.tsx` has hard-coded English strings (`Models.tsx:101,114,126`), while `Queue.tsx`, `Settings.tsx`, `AddVideos.tsx` use `t()` throughout.

## 7. Settings data flow

Two stores, split by kind (inferred from the code below):

| Setting | Stored where | Read / write path |
|---|---|---|
| `model`, `channel`, `auto_upload`, `privacy`, `content_language`, `translation_model`, `outro`, `min_score` | engine, `config/settings.yaml` (PATCH rewrites specific lines with a regex per key) | `api.settings()` → `GET /settings`, `api.patchSettings(patch)` → `PATCH /settings` (`ui/src/renderer/src/lib/api.ts:588-590`; type `Settings` in `types.ts:630-640`; server `server/api.py:2939-3017` — GET maps config keys to the flat shape, PATCH regex-edits `settings_path` line by line and returns `{"ok": true, "note": "restart serve to apply pipeline-level changes"}`; only `content_language`, `outro`, `min_score`, `translation_model` are applied to the live `config` at once (`server/api.py:2959-3017`); the regex-rewrite caveat is stated in `server/youtube_service.py:5` and `server/uploadpost_service.py:6-7`) |
| AI provider / transcription backend / keys | engine (`/ai`, `/ai/providers/<p>/key`, `/ai/activate`, `/ai/transcription`, sign-in endpoints) | `api.ts:462-520`; UI in `components/AICard.tsx` (Settings → AI) |
| YouTube / Upload-Post / WoopSocial settings | engine, each with its own `PATCH /<provider>/settings` (stored in `app_state` JSON for Upload-Post/WoopSocial) | `api.ts:596-600,686-690`, `server/uploadpost_service.py:6-7` |
| Remote render | engine `PUT /remote-render` | `api.ts` (remoteRender block), `components/RemoteRenderCard.tsx` |
| App language | renderer `localStorage['app-language']` | `lib/i18n.ts:75-96` |
| Appearance (font/scale/colour) | renderer `localStorage['clips-studio-appearance']` | `lib/appearance.ts:30-45` |
| Export folder | renderer `localStorage['export-folder']` (+ `window.studio.getDownloadsPath`) | `lib/exportFolder.ts` |
| Notifications on/off | renderer localStorage | `lib/queueNotifications.ts:17-29` |
| Generate-bar defaults (captions, 60s+, podcast, vertical live, gaming, sport, longform(+mode/+shorts), caption style, upload-channel) | renderer `localStorage['generate-*']`, `'generate-caption-style'`, `'upload-channel'` | `components/queue/AddVideos.tsx:123-215` |
| Update channel / skipped version | main process `userData/update-prefs.json` via `window.studio.update.prefs` | `ui/src/main/updater.ts:26-49` |
| Keep in tray | main process `userData/tray.json` | `ui/src/main/index.ts:49-59,120-129` |

The Settings page itself (`ui/src/renderer/src/pages/Settings.tsx:667-719`) is a vertical stack in `max-w-xl`: `LanguageCard`, `AppearanceCard`, `NotificationsCard`, `ExportFolderCard`, `StorageCard`, `VideoStorageCard`, `AICard`, `SetupCard`, `UpdateCard`, `WoopSocialCard`, `YouTubeCard`, `UploadPostCard`, an "Advanced settings" card (`MinScoreSetting`, `RemoteRenderCard`), a note that advanced options live in `config/settings.yaml`, and `BrandingCard` last. Each card is a `function XCard(): JSX.Element` returning `<section className="card space-y-3"><h3 className="font-semibold">…` and does its own `useEffect` fetch + immediate `api.patch…` on change (e.g. `Settings.tsx:43-100,577-632`).

### Where Sports / Gaming / Post style are presented
They are **not** in Settings. They are per-video job options on the Generate bar (`components/queue/AddVideos.tsx`) and the per-queued-job editor (`components/queue/QueueItemSettings.tsx`), plus per-watched-channel options (`components/watch/WatchPlatformOptions.tsx`, inferred from name). See §9.

## 8. UI conventions a Marketplace page should follow

| Convention | What the code does | Source |
|---|---|---|
| File naming | `pages/PascalCase.tsx` default-exporting `function Name(): JSX.Element`; components `components/PascalCase.tsx`, grouped in a sub-folder when a page has several (`components/queue/`, `components/watch/`); helpers/hooks `lib/camelCase.ts` | tree listing; `pages/Queue.tsx:22`, `pages/Models.tsx:8` |
| Styling | Tailwind v4 utility classes + 5 shared classes from `theme.css`: `.card`, `.btn-accent`, `.btn-ghost`, `.input`, `.label`; colour tokens `bg-base/surface/raised`, `text-ink/muted/accent`, `bg-success/warn/error`, `accent-strong`. No CSS modules, no component library. `!py-1.5`-style important overrides are common | `ui/src/renderer/src/theme.css:4-58`; `Models.tsx:158-161` |
| Page frame | `<div className="p-6 space-y-5 max-w-3xl">` (Models) / `max-w-xl` (Settings) / `w-full` (Queue); page title `<h2 className="text-2xl font-bold">` or `<h1 className="text-xl font-bold">` with a muted one-line subtitle | `Models.tsx:113-114`, `Settings.tsx:669-670`, `Queue.tsx:175-181` |
| Section | `<section className="card space-y-3"><h3 className="font-semibold">Title</h3>…` or an uppercase muted `h2` (`text-sm font-semibold uppercase tracking-wide text-muted`) with a count in `tabular-nums` and an action slot `ml-auto` | `Models.tsx:125-126`, `Queue.tsx:134-168` |
| List rows / cards | rows: `flex items-center gap-3 border-t border-raised/50 pt-3` with title `font-medium`, meta `text-xs text-muted`, actions as `btn-accent !py-1.5` / `btn-ghost !py-1.5` on the right (Models); tables: `<table className="w-full text-sm">` with `thead tr.label text-left`, `th.pb-2.font-normal`, `tbody tr.border-t.border-raised/50` (Models, Dashboard); media cards: `ClipCard.tsx` (lazy-loaded via IntersectionObserver) | `Models.tsx:127-168,195-214`, `Dashboard.tsx:478-500`, `ClipCard.tsx` |
| Search + sort header | `<input type="search" className="input !w-44 !py-1 text-sm" placeholder={t('Search…')} aria-label=…>` beside `<label className="label">` + `<select className="input !w-32 !py-1 text-sm">`; filtering/sorting is client-side via `useMemo` | `Dashboard.tsx:443-470,375-403` |
| Badges / chips | inline `<span className="ml-2 text-xs bg-accent/15 text-accent px-2 py-0.5 rounded">active</span>`; `ScoreBadge` (`px-2 py-0.5 rounded-md … bg-accent/20 text-accent`); pill filters `px-2.5 py-1 rounded-full text-xs border`; option chips: strings from `describeOptions()` rendered as `<span className="text-[11px] px-1.5 py-0.5 rounded bg-raised text-muted">` in a `flex gap-1.5 flex-wrap` row | `Models.tsx:134`, `components/ScoreBadge.tsx`, `FeedbackHub.tsx:383`, `lib/queue.ts:59-79`, `components/queue/QueueItem.tsx:159-168` |
| Tabs | `role="tablist"` row of buttons with `role="tab"`, `flex gap-0.5 border-b border-raised/60 text-xs` (only in TimelineEditor); the Clip Editor uses a simple `clipType` state `'all'|'shorts'|'longform'` | `TimelineEditor.tsx:1521-1531`, `ClipStudio.tsx:45` |
| Modals | hand-rolled overlay `fixed inset-0 z-50 bg-base/80 backdrop-blur-sm grid place-items-center p-6` with `role="dialog" aria-modal="true"`; no portal library | `PublishAllDialog.tsx:171-173`, `ScheduleView.tsx:84-86`, `FeedbackHub.tsx:200-203` |
| Dropdown with hints | `ExplainedSelect` (custom listbox with per-option tooltip) when options need explanations; native `<select className="input">` otherwise | `components/ExplainedSelect.tsx`, `SportFields.tsx:42-67` |
| Confirm | two-step inline confirm (`confirming` state) rather than `window.confirm` | `Queue.tsx:94-132` |
| Icons | inline SVG set in `components/icons.tsx` (`Scissors`, `Trash`, `Star`, `Folder`, `YouTube`, …); nav uses unicode glyphs | `components/icons.tsx:1-30`, `App.tsx:24-35` |
| State management | local `useState` + `useEffect` fetch per page; "server is the authority": events trigger `refresh()`, never optimistic updates; shared module-level caches for cross-component data (`useAIStatus`, `useSports`) | `Queue.tsx:11-21`, `lib/useAIStatus.ts`, `lib/sports.ts:1-44` |
| Live updates | `useEvents((e) => { if (e.type === '…') refresh() })` + a slow `setInterval` poll as belt-and-braces | `Queue.tsx:55-75`, `Models.tsx:58-69` |
| Error / offline | `offline` state → `<div className="card text-warn">…`; errors in `<div className="card border-error/40 text-error text-sm">` | `Models.tsx:98-108`, `Queue.tsx:236` |
| Loading | `<p className="text-sm text-muted">{t('Loading…')}</p>` | `Queue.tsx:257`, `Models.tsx:110` |
| Error text | `errorText(e)` in `lib/api.ts:946-955` extracts FastAPI `detail` from the thrown `Error` message; used by newer components (`WatchCard.tsx:2`) | `ui/src/renderer/src/lib/api.ts:946-955` |
| Error isolation | wrap optional panels in `FeatureBoundary name="…"`; `AppBoundary` at root | `components/FeatureBoundary.tsx`, `main.tsx:20-22` |
| External links | `window.studio.openExternal(url)` (host must be allow-listed in main) — not `<a target=_blank>` except the GitHub footer link | `App.tsx:198-205`, `ui/src/main/index.ts:487-570` |
| Accessibility | `aria-label` on inputs/selects, `aria-expanded` on toggles, `:focus-visible` outline, reduced-motion | `theme.css:22-38`, `AddVideos.tsx:654-656` |
| Comments | long "why" comments above code are the house style | throughout |

## 9. Modes / pipelines in the UI (the "mode picker")

There is **no single mode dropdown**. A video's processing mode is a set of **per-video toggles** on the Generate bar, with exclusivity rules enforced in the UI:

- Definition: `TOGGLES` array `{key,label,hint,title}` in `ui/src/renderer/src/components/queue/AddVideos.tsx:71-121`: `long_clips` "60s+", `longform` "Longform (16:9)", `vertical_live` "Vertical Live (9:16)", `podcast` "Podcast (multi-cam)", `gaming` "Gaming / Reaction (split-screen)", `sport` "Sports (match)", `watermark` "Watermark (branding)". Captions is rendered separately with a "Caption style"/"Post style" button (`AddVideos.tsx:632-658`).
- `LAYOUT_MODES = ['podcast','vertical_live','gaming','longform']` — only one layout mode at a time (`AddVideos.tsx:66`, exclusivity in `toggle()` `:369-445`).
- Each toggle is a checkbox `<label className="flex items-center gap-2 text-sm …" title={tg.title}><input type="checkbox" className="size-4 accent-[#38BDF8]"/>{t(tg.label)} <span className="text-muted">{t(tg.hint)}</span></label>` (`AddVideos.tsx:661-705`).
- Conditional sub-rows appear under a toggle: `SportFields` (sport, highlights via `ExplainedSelect`, period, teams, reels) when `options.sport` (`AddVideos.tsx:722-731`, `components/SportFields.tsx`); "Vertical Live content" select (`:734-757`); "Longform output" select (`:759+`); gaming opens `GamingLayoutEditor` (`:689-691`).
- Options are sent as `JobOptions` fields (`podcast`, `vertical_live`, `gaming`, `gaming_layout`, `longform: {mode, shorts}`, `sport: SportOption`, …) in `POST /jobs`, `/jobs/batch`, `/videos/local`, `PATCH /jobs/<id>` (`lib/types.ts:359-391`, `lib/api.ts:100-160,196-210`).
- Engine-driven lists: the Sports toggle only shows when `GET /sports` returns entries (`lib/sports.ts:1-44`, `AddVideos.tsx:663-665`); longform modes are a `<select>` (modes registry on the engine in `longform/profiles.py` per `docs/EXTENDING.md:163-170`).
- The same toggles reappear for a queued job in `components/queue/QueueItemSettings.tsx` (uses `SportFields`, `CaptionStyleControls`) and are summarised as chips by `describeOptions()` in `lib/queue.ts:59-79` on `QueueItem` rows.
- `docs/EXTENDING.md:163-170` states the intended pattern for a new genre: *one switch that reveals a picker whose list comes from an engine registry* (as `longform` and `sport` do), not a toggle per variant.

**How a new mode/pipeline would appear today**: add a `ToggleKey` + `TOGGLES` entry (+ exclusivity in `toggle()`/`isOn()`/`seedOptions()`/`remember()`), a `JobOptions` field in `types.ts`, pass-through in `api.ts` `createJob`/`addLocalVideo`, a chip in `lib/queue.ts:describeOptions`, and the mirrored controls in `QueueItemSettings.tsx` (inferred from how `sport` is wired across those files). For plugin-provided modes, the Sports pattern (`GET /sports` → `useSports()` → toggle appears only when the engine offers it) is the closest existing precedent for **engine-discovered, dynamically listed options** (inferred).

## 10. Adding a page (files to touch)

1. `ui/src/renderer/src/pages/Marketplace.tsx` — `export default function Marketplace(): JSX.Element`, frame `p-6 space-y-5 max-w-3xl`, `t()` for strings (pattern: `pages/Models.tsx`).
2. `ui/src/renderer/src/App.tsx` — add `'marketplace'` to the `Page` union (`:20`), an entry in `NAV` (`:24-35`, label + unicode icon), the import, and `{page === 'marketplace' && <Marketplace/>}` (`:218-230`); optionally an `open-marketplace` window-event listener like `:65-75`.
3. `ui/src/renderer/src/lib/api.ts` — new `api.*` methods for the engine endpoints (plugins/registry); `lib/types.ts` for their types.
4. `ui/src/renderer/src/components/marketplace/*.tsx` — cards/rows if the page grows (pattern: `components/queue/`, `components/watch/`).
5. Locales: nothing required for English; optional keys in `locales/*.json`.
6. `ui/src/main/index.ts` `EXTERNAL_ALLOWED` — only if the page opens non-allow-listed hosts via `openExternal`.
7. No router, no registry of routes elsewhere, no tests to update (there is no UI test suite). `ARCHITECTURE.md:855-858` lists pages in prose and could be updated.

CSP note: the renderer cannot `fetch()` GitHub directly (`index.html:8`), so registry data must be fetched by the engine (`127.0.0.1:8765`) and exposed over its API (inferred).

## 11. Extra details

### WebSocket event types
`StudioEvent.type` ∈ `'progress' | 'job' | 'model_pull' | 'queue' | 'publish' | 'automation'` with optional fields (`job_id`, `job_type: 'process'|'render'|'translate'`, `status`, `stage`, `fraction`, `tag`, `completed`, `total`, `remaining`, …) — `ui/src/renderer/src/lib/types.ts:642-686`. `'queue'` is a bare "re-read" ping; `Queue.tsx:55-61` refreshes on `'queue'`/`'job'`, `Models.tsx:58-69` listens to `'model_pull'` (`status`, `completed/total`, `error`). A plugin-install progress stream could follow the `model_pull` shape (inferred).

### Dry typecheck result in this sandbox (no install performed)
`cd ui && /opt/node22/bin/tsc --noEmit -p tsconfig.web.json` → `TS2307: Cannot find module 'react'`, `TS2503: Cannot find namespace 'JSX'` (exit 2); `-p tsconfig.node.json` → `TS2688: Cannot find type definition file for 'node'`. So the global TypeScript cannot substitute for `npm ci`; `ui/node_modules` must be populated before `npm run typecheck` / `npm run build` are meaningful.

### Where job creation happens
Only `components/queue/AddVideos.tsx` calls `api.createJobsBatch` (`:543`) and `api.addLocalVideo` (`:561`); `api.createJob` (`api.ts:100-130`) has no caller in the renderer (grep). The Watch page seeds a channel's per-video options from the same `seedOptions()` (`pages/Watch.tsx:70`) and reuses `QueueItemSettings` inside `WatchCard` (`components/watch/WatchCard.tsx:5`).

### Models page as the closest "catalog" precedent
`pages/Models.tsx` is the nearest thing to a marketplace today: an "Installed" section of rows with Use/Remove buttons and an "active" badge (`:125-169`), a "Download a model" input + button driven by `POST /models/pull` with progress from `model_pull` events (`:171-185`), and recommendation tables from the engine (`:195-250`). It also shows the offline pattern (`:98-108`). `components/AICard.tsx` (1306 lines) is the provider-catalog precedent: `ProviderPanel`, `TierRow`, `ModelPicker`, `KeyField` sub-components inside one `card space-y-5` (`AICard.tsx:86,279,342,1087,1237`).

### docs/API.md on the UI's relationship to the engine
"The desktop window is one client of it … The service has 86 HTTP endpoints and a WebSocket" (`docs/API.md:3-13`); `ARCHITECTURE.md:851-853`: "The renderer never touches Python or the filesystem directly: context isolation on, no node integration, everything through the local API."

## 12. Open questions / risks for the Marketplace work

1. **Registry fetch path**: the renderer CSP (`ui/src/renderer/index.html:8`) only allows `127.0.0.1:8765`, so GitHub registry reads must be proxied by the engine (new endpoints) or by the main process over IPC. The code base's stance ("everything through the local API", `ARCHITECTURE.md:851-853`) points to the engine (inferred).
2. **Opening plugin repo pages**: `EXTERNAL_ALLOWED` only allows `github.com/ColinGPT9/clips-studio` (`ui/src/main/index.ts:509`); other GitHub URLs are refused silently with a console warning (`:562-570`). Needs a widened regex or an engine-side redirect.
3. **i18n coverage**: `Models.tsx` is untranslated while `Settings`/`Queue` are; decide whether the Marketplace page uses `t()` from the start (the locale files are hand-maintained, `docs/TRANSLATING.md`).
4. **No UI tests**: nothing to extend; CI only typechecks and builds (`.github/workflows/ci.yml:100-123`). The Python side has a test that keeps `multilingual/languages.py` and the locale folder in step (`docs/EXTENDING.md:36-37`), which a new locale would hit.
5. **Where plugin-provided modes surface**: `TOGGLES` in `AddVideos.tsx:71-121` is a static array; the Sports toggle (`GET /sports` gating, `AddVideos.tsx:663-665`) is the only engine-discovered toggle. Making modes plugin-driven means generalising that gate (inferred).
6. **Settings persistence is regex-on-YAML** (`server/api.py:2955-3017`); plugin settings should probably not extend `PATCH /settings` but use their own endpoint/`app_state` JSON as Upload-Post/WoopSocial do (`server/uploadpost_service.py:6-7`) (inferred).
7. **ARCHITECTURE.md §11 is slightly stale**: it omits the Watch page (`ARCHITECTURE.md:855-858` vs `App.tsx:24-35`).
