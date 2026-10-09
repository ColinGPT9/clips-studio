// Which version the Microsoft Store offers, read from its public catalog.
//
// A Store copy is updated by the Store, never by this app (distribution.ts).
// But the Store updates on its own schedule, and that can be paused, switched
// off or stuck behind a download of several gigabytes. A copy that only says
// "the Store keeps this up to date" leaves somebody three versions behind with
// no way to know. So the app asks what the Store has and says when it is newer.
//
// The Store's own catalog, not the Hugging Face feed the standalone build
// reads: a release is on Hugging Face a day or more before certification
// finishes, and "a new version is out" must never send people to a Store that
// does not have it yet.
//
// No Electron in here, so it runs under Node in tests/test_ui_store_update.py.

/** Clips Kitty in the Microsoft Store (the id in the README's Store link). */
export const STORE_PRODUCT_ID = '9NB6XT7DSQZZ'

/** The package's name (appx.identityName in electron-builder.yml). */
export const STORE_IDENTITY = 'ClipsStudio.ClipsStudio'

/** Clips Kitty's page in the Store app, where Update is pressed. */
export const STORE_PAGE = `ms-windows-store://pdp/?ProductId=${STORE_PRODUCT_ID}`

/** One plain request, carrying the product id and nothing about this PC. */
export const STORE_CATALOG = `https://displaycatalog.mp.microsoft.com/v7.0/products/${STORE_PRODUCT_ID}?market=US&languages=en-US`

function parts(version: string): number[] | null {
  if (!/^\d+(\.\d+)*$/.test(version)) return null
  return version.split('.').map(Number)
}

/** True when `a` is a later version than `b`. A part left out counts as 0,
 *  so 2.0 and 2.0.0 are the same version. Anything that is not numbers and
 *  dots (a pre-release name) is never newer: saying nothing beats saying
 *  "update" wrongly. */
export function isNewer(a: string, b: string): boolean {
  const x = parts(a)
  const y = parts(b)
  if (!x || !y) return false
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] ?? 0) - (y[i] ?? 0)
    if (d !== 0) return d > 0
  }
  return false
}

/** The app version the Store offers, as "2.0.0": the highest among the
 *  catalog's packages of this app. A package is 2.0.0.0 (the fourth part is
 *  the Store's own and always 0), and the app calls that 2.0.0. null when the
 *  answer has no such package, whatever shape it came in. */
export function storeVersionOf(catalog: unknown, identity: string = STORE_IDENTITY): string | null {
  const names: string[] = []
  const walk = (node: unknown, depth: number): void => {
    if (depth > 12 || node === null || typeof node !== 'object') return
    if (Array.isArray(node)) {
      for (const item of node) walk(item, depth + 1)
      return
    }
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      if (key === 'PackageFullName' && typeof value === 'string') names.push(value)
      else walk(value, depth + 1)
    }
  }
  walk(catalog, 0)

  let best: string | null = null
  for (const name of names) {
    // ClipsStudio.ClipsStudio_2.0.0.0_x64__315g1r74a6w58
    const [who, version] = name.split('_')
    if (who !== identity || !version || !/^\d+\.\d+\.\d+\.\d+$/.test(version)) continue
    const app = version.split('.').slice(0, 3).join('.')
    if (best === null || isNewer(app, best)) best = app
  }
  return best
}
