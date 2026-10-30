/* Actual Watch form and pipeline picker; sibling cards and network are fixtures. */
const fs = require('fs')
const path = require('path')
const os = require('os')

if (!process.versions.electron) {
  const root = path.resolve(__dirname, '..')
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'clips-watch-detector-'))
  const stubs = {
    'WatchCard.tsx': 'export default function C(){return null}',
    'WatchLive.tsx': 'export default function C(){return null}',
    'ConnectedAccountsCard.tsx': 'export default function C(){return null}',
    'WatchSchedule.tsx': 'export default function C(){return null}',
    'useAIStatus.ts': 'export function useAIStatus(){return null}',
    'useEvents.ts': 'export function useEvents(){}'
  }
  ;(async () => {
    await require('esbuild').build({
      stdin: { contents: `import React from 'react'; import {createRoot} from 'react-dom/client'; import Watch from './src/renderer/src/pages/Watch'; import Settings from './src/renderer/src/components/watch/WatchPublishSettings'; const root=createRoot(document.getElementById('root')); root.render(<Watch/>); window.showClipSettings=()=>root.render(<Settings watch={{id:1,platform:'twitch_clips',backlog:'all',min_minutes:0,min_views:10000,lookback_days:7,publish:{mode:'ask',platforms:[],per_day:3,gap_hours:1,day_start:'',hashtags:[],ai_hashtags:true,footer:''}}} onSaved={()=>{}}/>);`,
        resolveDir: root, loader: 'tsx' },
      bundle: true, outfile: path.join(dir, 'fixture.js'), jsx: 'automatic', platform: 'browser',
      nodePaths: [path.join(root, 'node_modules')],
      plugins: [{ name: 'unrelated-watch-cards', setup(build) {
        build.onLoad({ filter: /\.[tj]sx?$/ }, args => {
          const contents = stubs[path.basename(args.path)]
          if (contents) return { contents, loader: 'tsx' }
        })
      }}]
    })
    fs.writeFileSync(path.join(dir, 'fixture.html'), `<!doctype html><div id="root"></div><script>
      localStorage.clear();localStorage.setItem('generate-gaming','true');
      window.studio={pluginSession:async()=> 'fixture-session'};
      window.available=[{id:'fixture-dev/gta',name:'GTA fixture',version:'1.0.0',kind:'pipeline',category:'gaming',enabled:true,secrets_set:[],details:{execution:'local',execution_text:'Local',network:[],data_warnings:[],permissions:[],secrets:[]}}];
      window.fetch=async(url,init={})=>{
        if (init.method && init.method !== 'GET') (window.writes??=[]).push({url,method:init.method,body:JSON.parse(init.body||'{}')});
        const route=new URL(url).pathname;let body={};
        if(route==='/plugins') body={plugins:window.available};
        else if(route==='/sports') body=[];
        else if(route==='/automation') body={enabled:false,paused:false};
        else if(route==='/automation/watches' && init.method==='POST') {window.added=JSON.parse(init.body);body={id:1};}
        else if(route==='/automation/watches') body=[];
        else if(route==='/discord/destinations') body={destinations:[]};
        return {ok:true,status:200,json:async()=>body};
      };
      </script><script src="fixture.js"></script>`)
    const env = { ...process.env }
    delete env.ELECTRON_RUN_AS_NODE
    const report = process.env.WATCH_DETECTOR_REPORT || path.join(dir, 'result.json')
    const result = require('child_process').spawnSync(require('electron'), [__filename, '--electron', dir, report],
      { env, windowsHide: true, timeout: 45000, encoding: 'utf8' })
    if (fs.existsSync(report)) console.log(fs.readFileSync(report, 'utf8'))
    if (result.error) throw result.error
    if (result.status !== 0) throw new Error(result.stderr || 'Watch detector regression failed')
  })().catch(error => { console.error(error); process.exitCode = 1 })
} else {
  const { app, BrowserWindow } = require('electron')
  const [dir, report] = process.argv.slice(-2)
  app.whenReady().then(async () => {
    const win = new BrowserWindow({ show: false, webPreferences: { offscreen: true, sandbox: true, contextIsolation: true } })
    const errors = []
    win.webContents.on('console-message', (_event, level, message) => { if (level === 3) errors.push(message) })
    const js = script => win.webContents.executeJavaScript(script)
    const delay = () => new Promise(resolve => setTimeout(resolve, 150))
    const snapshot = () => js(`(()=>{const s=document.querySelector('select[aria-label="Gaming detector Watched channel"]');return s?{value:s.value,text:s.selectedOptions[0].text}:null})()`)
    try {
      await win.loadFile(path.join(dir, 'fixture.html'))
      await delay()
      if (await js('Boolean(window.added)')) throw new Error('Watch mounted with a POST')
      await js(`(()=>{const s=document.querySelector('select[aria-label="Gaming detector Watched channel"]');s.value='fixture-dev/gta';s.dispatchEvent(new Event('change',{bubbles:true}))})()`)
      await delay()
      if ((await snapshot())?.value !== 'fixture-dev/gta') throw new Error('Fixture could not select available GTA detector: '+JSON.stringify(await js(`({picker:document.querySelector('select[aria-label="Gaming detector Watched channel"]')?.outerHTML,text:document.body.innerText})`)))
      await js(`window.dispatchEvent(new Event('plugins-changed'))`)
      await delay()
      if ((await snapshot())?.value !== 'fixture-dev/gta') throw new Error('Refresh discarded an available detector')
      await js(`window.available=[];window.dispatchEvent(new Event('plugins-changed'))`)
      await delay()
      const after = await snapshot()
      if (after?.value !== '') throw new Error('Removed GTA detector stayed selected: '+JSON.stringify(after))
      await js(`(()=>{const i=document.querySelector('input[placeholder^="https://www.youtube.com"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'@fixture');i.dispatchEvent(new Event('input',{bubbles:true}))})()`)
      await delay()
      await js(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Add').click()`)
      await delay()
      const added = await js('window.added')
      if (!added || added.options?.pipeline) throw new Error('Watch creation retained a removed pipeline or did not submit: '+JSON.stringify(added))
      await js(`(()=>{const s=document.querySelector('select[aria-label="Platform"]');s.value='twitch_clips';s.dispatchEvent(new Event('change',{bubbles:true}))})()`)
      await delay()
      if (!(await js(`document.body.innerText.includes('Viewer clips are posted whole, with no trimming.')`))) throw new Error('Whole-clip note missing')
      await js(`(()=>{for(const [label,value] of [['Post a clip once it has views','10000'],['Look back days','45'],['Channel','zackrawrr']]){const i=document.querySelector('input[aria-label="'+label+'"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,value);i.dispatchEvent(new Event('input',{bubbles:true}))}document.querySelector('input[aria-label="Also post clips already over the target from the lookback window"]').click()})()`)
      await delay()
      await js(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Add').click()`)
      await delay()
      const clips = await js('window.added')
      if (clips?.platform !== 'twitch_clips' || clips.min_views !== 10000 || clips.lookback_days !== 45 || clips.max_pending !== 5 || clips.publish.per_day !== 5 || !clips.backfill || !clips.options.whole || !clips.options.gaming || clips.options.pipeline) throw new Error('Incorrect clip watch: '+JSON.stringify(clips))
      await js('window.writes=[];window.showClipSettings()')
      await new Promise(resolve => setTimeout(resolve, 800))
      if ((await js('window.writes')).length) throw new Error('Clip settings wrote on mount')
      await js(`(()=>{const i=Array.from(document.querySelectorAll('label')).find(l=>l.textContent.includes('Post a clip once it has views')).querySelector('input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'15000');i.dispatchEvent(new Event('input',{bubbles:true}))})()`)
      await new Promise(resolve => setTimeout(resolve, 850))
      const writes = await js('window.writes')
      if (writes.length !== 1 || writes[0].method !== 'PATCH' || writes[0].body.min_views !== 15000 || 'min_minutes' in writes[0].body) throw new Error('Target did not autosave correctly: '+JSON.stringify(writes))
      fs.writeFileSync(report, JSON.stringify({ passed: true, fixture_only: true, available_preserved: true, removed_default: after, submitted_options: added.options, clip_watch: clips, target_autosave: true, no_write_on_mount: true }, null, 2))
      app.exit(0)
    } catch (error) {
      fs.writeFileSync(report, JSON.stringify({ passed: false, error: String(error), errors }, null, 2))
      app.exit(1)
    }
  })
}
