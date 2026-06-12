// VS Code terminals inherit ELECTRON_RUN_AS_NODE=1 from the extension host,
// which makes the Electron binary behave like plain Node and crash the app
// at `app.whenReady` (app is undefined). Strip it before launching dev mode.
delete process.env.ELECTRON_RUN_AS_NODE

const { spawnSync } = require('node:child_process')
const result = spawnSync('npx', ['electron-vite', 'dev'], {
  stdio: 'inherit',
  shell: true,
  env: process.env
})
process.exit(result.status ?? 1)
