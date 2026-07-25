// Embed the app icon into Clips Studio.exe after packaging.
//
// electron-builder normally does this itself, with a tool called rcedit that
// ships inside its winCodeSign download. That download also contains macOS
// symlinks, and extracting those needs Developer Mode or admin rights — on an
// ordinary Windows account the whole step fails with "Cannot create symbolic
// link", which is why signAndEditExecutable is off in electron-builder.yml.
//
// The side effect was that the packaged .exe kept Electron's default atom
// icon: verified by extracting the icon straight out of a built exe. The
// desktop shortcut, taskbar and Alt-Tab all showed it, which is the first
// thing a creator sees.
//
// rcedit itself extracts from that archive perfectly well — only the symlinks
// fail. So we call it ourselves here, after the app directory is built and
// before the zip and installer payload are compressed from it.

const { execFileSync } = require('node:child_process')
const { existsSync, readdirSync } = require('node:fs')
const { join } = require('node:path')

/** rcedit lives in a hash-named folder under the electron-builder cache. */
function findRcedit() {
  const cache = join(
    process.env.LOCALAPPDATA || '',
    'electron-builder',
    'Cache',
    'winCodeSign'
  )
  if (!existsSync(cache)) return null
  for (const entry of readdirSync(cache)) {
    const candidate = join(cache, entry, 'rcedit-x64.exe')
    if (existsSync(candidate)) return candidate
  }
  return null
}

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'win32') return

  const exe = join(context.appOutDir, `${context.packager.appInfo.productFilename}.exe`)
  const icon = join(__dirname, 'icon.ico')

  if (!existsSync(icon)) {
    console.warn('  • afterPack: no build/icon.ico — app will keep the default icon')
    return
  }
  const rcedit = findRcedit()
  if (!rcedit) {
    // Not fatal: the installer still builds, it just looks generic. Better a
    // warning than a failed release build.
    console.warn('  • afterPack: rcedit not found in the electron-builder cache;')
    console.warn('    the app exe keeps the default Electron icon.')
    return
  }

  execFileSync(rcedit, [exe, '--set-icon', icon], { stdio: 'inherit' })
  console.log(`  • afterPack: embedded ${icon} into ${exe}`)
}
