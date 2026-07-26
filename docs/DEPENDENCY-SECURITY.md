# Known dependency advisories

A snapshot of what `npm audit` reports and what each finding actually means
for this app, so the number in GitHub's security tab is not read as either
"everything is fine" or "everything is on fire". Both readings are wrong.

Last assessed: **2026-07-26**, against electron 31.7.7 / electron-builder
24.13.3.

## The short version

| Package | Ships to users? | Reachable here? | Status |
|---|---|---|---|
| `electron` | **Yes** — it is the app | Partly | Open. Upgrade planned |
| `tar` (via electron-builder) | No — build machine only | No | Fixed by an override |
| `app-builder-lib`, `builder-util-runtime` | No — build machine only | No | Needs electron-builder 26 |
| `brace-expansion` | No — build tooling | No | Transitive |
| `esbuild`, `vite` | No — dev server only | No | Dev-time only |

Only the first row can reach anyone who installs Clips Studio. Everything
else lives in tooling that produces the installer and is never inside it.

## electron 31.7.7 — the one that matters

Seventeen published advisories, all fixed in **>= 39.8.5**. Use-after-frees,
service-worker spoofing of `executeJavaScript` IPC replies, wrong origin
passed to the iframe permission handler, `nodeIntegrationInWorker` not
scoped correctly in shared renderers.

**Why it is not an emergency for this app, and why it still gets fixed.**

Nearly all of these need untrusted web content running in a renderer. This
app has almost none:

- The main window loads **local files only**, under a CSP of
  `default-src 'self'` with a `connect-src` limited to the local engine.
- The one window that loads **remote** content is the PayPal donate popup in
  `ui/src/main/index.ts`, and it is about as locked down as Electron allows:
  `sandbox: true`, `nodeIntegration: false`, `contextIsolation: true`, no
  preload, `window.open` denied, and `will-navigate` refuses anything that is
  not `paypal.com` / `paypal.me`.

So the sandboxed remote-content window is the realistic surface, and it has
no bridge into the app. That is mitigation, not a fix — a renderer sandbox
escape is exactly what several of these advisories describe.

**The upgrade is a real piece of work, not a version bump.** Electron 31 to
39+ also forces electron-builder 24 to 26, because 24 cannot package modern
Electron. That is the whole packaging chain: the `nsis-web` target (chosen
because 32-bit `makensis` dies around 2 GB), `signAndEditExecutable: false`,
and the `afterPack` rcedit hook that exists *because* signing is off. None of
that can be verified by CI — it needs a full `scripts/build_installer.py`
run and an install on a clean machine.

Do it deliberately, with time to test, and process a real video afterwards.
Not as a drive-by merge the night before a release. Upgrading also closes the
`app-builder-lib` and `builder-util-runtime` advisories in the same move.

## tar — fixed by an override

`tar@6.2.1` arrived via `electron-builder` -> `app-builder-lib` and carried
twelve advisories including the only **critical** one. The 6.x line was never
patched; the fix exists only in 7.x.

`ui/package.json` therefore pins it with:

```json
"overrides": { "tar": "^7.5.22" }
```

Checked before committing: `require('tar')` still resolves and still exposes
`extract`/`create`/`list`, which is what `app-builder-lib/out/targets/archive.js`
calls. Build-machine scope either way — nothing here is inside the shipped app.

**Delete this override when electron-builder reaches 26**, which ships a
patched tar of its own. Leaving a stale override pinned across a major
upgrade is its own hazard.

## Why `npm audit fix --force` is the wrong button

It proposes electron-builder 26 as a breaking change, which is the packaging
upgrade above — a decision with a test plan attached, not something to accept
from a prompt. Run `npm audit` to read, never `--force` to fix.

## Re-checking this

```
cd ui && npm audit
```

If a new advisory appears, the question to answer first is always the one in
the table: **does it ship, or is it build tooling?** That single distinction
decides whether it is urgent.
