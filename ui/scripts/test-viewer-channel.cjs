/* Actual ConnectedAccountsCard, private network fixture, hidden Electron. */
const fs = require('fs')
const path = require('path')
const os = require('os')

if (!process.versions.electron) {
  const root = path.resolve(__dirname, '..')
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'clips-viewer-channel-'))
  ;(async () => {
    await require('esbuild').build({
      stdin: { contents: `import React from 'react';import {createRoot} from 'react-dom/client';
        import Card from './src/renderer/src/components/ConnectedAccountsCard';
        const root=createRoot(document.getElementById('root'));let key=0;
        window.remount=()=>root.render(<Card key={key++}/>);window.remount();`, resolveDir: root, loader: 'tsx' },
      bundle: true, outfile: path.join(dir, 'fixture.js'), jsx: 'automatic', platform: 'browser',
      plugins: [{ name: 'unrelated-generate-settings', setup(build) {
        build.onLoad({ filter: /AddVideos\.tsx$/ }, () => ({ contents: 'export function seedOptions(){return {}}', loader: 'tsx' }))
      }}]
    })
    fs.writeFileSync(path.join(dir, 'fixture.html'), `<!doctype html><div id="root"></div><script>
      localStorage.clear();window.studio={pluginSession:async()=> 'fixture-session'};
      window.calls=[];window.monitoring=false;window.channel=null;
      window.fetch=async(url,init={})=>{
        const route=new URL(url).pathname;window.calls.push({route,method:init.method||'GET',body:init.body?JSON.parse(init.body):null});
        let body;
        if(route==='/connected-accounts') body={providers:[{id:'twitch',configured:true,storage_available:true}],
          accounts:[{id:'twitch:123',provider:'twitch',user_id:'123',login:'fixturekitty',display_name:'Fixture Kitty',status:'connected',scopes:['user:read:chat','clips:edit']}]};
        else if(route.endsWith('/streams')) body={monitoring:window.monitoring,broadcaster_id:window.channel,
          broadcaster_login:window.channel?'zackrawrr':null,streams:[]};
        else if(route.endsWith('/events')) body={status:'connected',broadcaster_id:window.channel,cursor:2,events:[{sequence:1,type:'channel.chat.message',broadcaster_id:window.channel},{sequence:2,type:'channel.chat.message',broadcaster_id:'wrong-channel'}]};
        else if(route.endsWith('/twitch')) body={broadcaster:{id:'999',login:'zackrawrr',display_name:'Zack'},stream:null};
        else if(route.endsWith('/monitor')) {window.monitoring=init.method==='POST';
          if(window.monitoring) window.channel=JSON.parse(init.body).broadcaster_id;body={monitoring:window.monitoring};}
        else throw Error('Unexpected fixture request');
        return {ok:true,status:200,json:async()=>body};
      };</script><script src="fixture.js"></script>`)
    const env = { ...process.env }
    delete env.ELECTRON_RUN_AS_NODE
    const report = process.env.VIEWER_CHANNEL_REPORT || path.join(dir, 'result.json')
    const result = require('child_process').spawnSync(require('electron'), [__filename, '--electron', dir, report],
      { env, windowsHide: true, timeout: 45000, encoding: 'utf8' })
    if (fs.existsSync(report)) console.log(fs.readFileSync(report, 'utf8'))
    if (result.error) throw result.error
    if (result.status !== 0) throw new Error(result.stderr || 'Viewer channel regression failed')
  })().catch(error => { console.error(error); process.exitCode = 1 })
} else {
  const { app, BrowserWindow } = require('electron')
  const [dir, report] = process.argv.slice(-2)
  app.whenReady().then(async () => {
    const win = new BrowserWindow({ show: false, webPreferences: { offscreen: true, sandbox: true, contextIsolation: true } })
    const errors = []
    win.webContents.on('console-message', (_event, level, message) => { if (level === 3) errors.push(message) })
    const js = script => win.webContents.executeJavaScript(script)
    const delay = () => new Promise(resolve => setTimeout(resolve, 180))
    const input = 'input[aria-label="Twitch channel to collect from: fixturekitty"]'
    const click = text => js(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent===${JSON.stringify(text)}).click()`)
    const setInput = value => js(`(()=>{const i=document.querySelector('${input}');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,${JSON.stringify(value)});i.dispatchEvent(new Event('input',{bubbles:true}))})()`)
    try {
      await win.loadFile(path.join(dir, 'fixture.html'));await delay()
      if (await js('window.calls.some(c=>c.method!=="GET")')) throw Error('Loading the card started an operation')
      await setInput('https://www.twitch.tv/zackrawrr');await delay()
      await click('Start collecting nominations');await delay()
      const calls = await js('window.calls')
      const lookup = calls.find(c => c.route.endsWith('/twitch'))
      const start = calls.find(c => c.route.endsWith('/monitor') && c.method === 'POST')
      if (lookup?.body.params.broadcaster_login !== 'zackrawrr' || start?.body.broadcaster_id !== '999') throw Error('Selected channel was replaced by the reader account')
      if (!(await js(`document.body.innerText.includes('Listening to Twitch') && document.body.innerText.includes('Chat messages received in this view: 1')`))) throw Error('Live activity did not show only the selected channel chat');
      await new Promise(resolve=>setTimeout(resolve,2800));
      if (!(await js(`document.body.innerText.includes('Chat messages received in this view: 1')`))) throw Error('Replayed events inflated the chat count');
      if (!(await js(`document.querySelector('${input}').disabled`))) throw Error('Active channel remained editable')
      await js('window.remount()');await delay()
      if (await js(`document.querySelector('${input}').value`) !== 'zackrawrr') throw Error('Reload showed the signed-in account instead of the collected channel')
      await click('Stop collecting nominations');await delay()
      await click('Start collecting nominations');await delay()
      if (await js('window.calls.filter(c=>c.route.endsWith("/twitch")).at(-1).body.params.broadcaster_login') !== 'zackrawrr') throw Error('Resume switched back to the signed-in account')
      await click('Stop collecting nominations');await delay()
      if (await js(`document.querySelector('[aria-label="Live Twitch activity"]') !== null`)) throw Error('Live activity remained after stopping');
      const count = await js('window.calls.filter(c=>c.method==="POST").length')
      await setInput('https://evil.test/zackrawrr');await delay()
      await click('Start collecting nominations');await delay()
      if (await js('window.calls.filter(c=>c.method==="POST").length') !== count) throw Error('Invalid channel reached the API')
      if (errors.length) throw Error('Renderer console errors')
      fs.writeFileSync(report, JSON.stringify({ passed: true, fixture_only: true, selected_channel: 'zackrawrr',
        authenticated_reader: 'fixturekitty', lookup, start, active_channel_locked: true,
        remount_and_resume_preserved: true, invalid_url_refused: true, live_activity_scoped: true, replayed_events_not_counted: true, activity_removed_after_stop: true, renderer_errors: errors }, null, 2))
      app.exit(0)
    } catch (error) {
      fs.writeFileSync(report, JSON.stringify({ passed: false, error: error.message, renderer_errors: errors }, null, 2))
      app.exit(1)
    }
  })
}
