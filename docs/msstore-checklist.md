# Microsoft Store 0.1.2 — first submission checklist

Work top to bottom. Everything above the line is done; everything below needs
you, mostly because it needs a Partner Center account only you can create.

Full instructions for each step are in [MSSTORE.md](MSSTORE.md).

## Done

- [x] **Existing installer untouched** — `win.target` is unchanged, so
      `python scripts/build_installer.py` produces exactly what it did before
- [x] **Config no longer written into the install directory** — would have made
      model switching fail inside an MSIX package (`core/paths.py`)
- [x] **Packaged app verified to launch** — window, backend and bundled Ollama
      all come up
- [x] **appx target added** (`ui/electron-builder.yml`), with `runFullTrust` as
      the only capability
- [x] **Build script** — `python scripts/build_msix.py`
- [x] **Version mapping** — 0.1.2 → package `1.1.2.0`, verified monotonic past
      the 1.0.0 boundary
- [x] **Store tiles generated** — `python scripts/build_appx_assets.py`
- [x] **Updates are distribution-aware** — a Store copy never runs the NSIS
      updater
- [x] **Donations open in the system browser** on Store builds (policy 10.8.2)
- [x] **Privacy policy published** — required by policy 10.5.1, and Partner
      Center will not accept a submission without the URL
- [x] **Store listing copy drafted** — [store-listing.md](store-listing.md)
- [x] **Certification notes drafted** — in MSSTORE.md, covering the first-run
      model download that would otherwise confuse a tester

---

## Needs you

### Account and identity

- [ ] Developer account created at **<https://storedeveloper.microsoft.com>**
      (free; that URL specifically, or you get the old paid flow)
- [ ] Identity verified (government ID + selfie)
- [ ] Product name reserved: **Apps and games → New product → MSIX or PWA app**
- [ ] Three values copied from **Product management → View app identity
      details** into the `appx:` block of `ui/electron-builder.yml`:
      `identityName`, `publisher`, `publisherDisplayName`

`scripts/build_msix.py` refuses to run until those are filled in.

### Build and test

- [ ] Windows Developer Mode on (*Settings → System → For developers*) —
      currently **off** on this machine
- [ ] `python scripts/build_msix.py` completes
- [ ] `AppxManifest.xml` inside the package shows version `1.1.2.0`,
      `ProcessorArchitecture="x64"`, `runFullTrust` and nothing else
- [ ] Package signed with a self-signed cert and installed locally
- [ ] Installed package: launches, imports a video, processes it, exports
- [ ] Model switching works **inside the package** — this is the one that would
      have failed before the config fix, so it is the one worth checking
- [ ] Restart, uninstall, reinstall
- [ ] Nothing was written into the package directory
- [ ] Windows App Certification Kit passes

### Submission

- [ ] Pricing: **Free**
- [ ] Markets: all
- [ ] Discoverability: available and discoverable in the Store
- [ ] Category: Multimedia design → Video editing
- [ ] Privacy policy URL:
      `https://colingpt9.github.io/clips-studio/privacy.html`
- [ ] System requirements entered — **16 GB RAM minimum**, x64, Windows 10 1809+
- [ ] Product declaration: **live generative AI** ticked (policy 11.16)
- [ ] Product declaration: **third-party purchase API** ticked, for the PayPal
      donate link (policy 10.8.2)
- [ ] Age rating questionnaire completed
- [ ] Package uploaded (`release/*.appx`)
- [ ] Listing: description, feature bullets, search terms
- [ ] **At least one screenshot** — four or more recommended
- [ ] 1:1 box art uploaded (`site/assets/mascot.png` works as-is)
- [ ] Certification notes pasted in
- [ ] **Publishing set to manual, not automatic** — so you read the
      certification report before it goes live
- [ ] Submitted

### After it passes

- [ ] Certification report read
- [ ] Published
- [ ] Store link added to the README and the website
- [ ] `CHANGELOG.md` notes that 0.1.2 is on the Store

## Not blocking, but worth doing

- [ ] **winget** — [`packaging/winget/`](../packaging/winget/) is ready to
      submit and needs no account, certificate or certification. Much cheaper
      than the Store and reaches a different audience.
- [ ] Screenshots and a demo video ([#35](../../issues/35)) — the listing needs
      screenshots anyway, so this closes two things at once.
