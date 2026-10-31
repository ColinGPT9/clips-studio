/* Real approval components in a hidden Electron window; HTTP is an offline fixture. */
const fs = require('fs')
const path = require('path')
const os = require('os')
if (!process.versions.electron) {
  ;(async () => {
    const root = path.resolve(__dirname, '..')
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'clip-approvals-'))
    const assetDir=path.join(root,'out','renderer','assets')
    const stylesheet=fs.existsSync(assetDir)?fs.readdirSync(assetDir).find(name=>name.endsWith('.css')):null
    const css=stylesheet?fs.readFileSync(path.join(assetDir,stylesheet),'utf8'):''
    await require('esbuild').build({
      stdin: {contents: `import React from 'react';import {createRoot} from 'react-dom/client';import {ClipUpdatesProvider,ApprovalCards} from './src/renderer/src/components/ApprovalCard';createRoot(document.getElementById('root')).render(<ClipUpdatesProvider><ApprovalCards onOpen={()=>{}}/></ClipUpdatesProvider>);`, resolveDir:root, loader:'tsx'},
      bundle:true,outfile:path.join(dir,'fixture.js'),jsx:'automatic',platform:'browser',nodePaths:[path.join(root,'node_modules')],
      plugins:[{name:'events',setup(build){build.onLoad({filter:/useEvents\.ts$/},()=>({contents:'export function useEvents(fn){window.emit=fn}',loader:'ts'}))}}]
    })
    fs.writeFileSync(path.join(dir,'fixture.html'), `<!doctype html><style>${css}</style><div id="root"></div><script>
      window.writes=[];window.pending=[{item_id:42,title:'Viewer clip',line:'Saved approval wording.',platforms:['youtube'],first_slot:'',thumbnail_url:'',video_id:'tw_clip_fixture',answer:'',answered_where:'',show_dmca:true}];
      window.studio={pluginSession:async()=>'',notify:async()=>true};window.fetch=async(url,init={})=>{const route=new URL(url).pathname;if(init.method&&init.method!=='GET')window.writes.push(route);let body=route.endsWith('/pending')?window.pending:route.endsWith('/status')?{enabled:true,show_dmca:true}: {publish_state:route.endsWith('/skip')?'off':'publishing'};return {ok:true,status:200,json:async()=>body};};
      </script><script src="fixture.js"></script>`)
    const env={...process.env};delete env.ELECTRON_RUN_AS_NODE
    const report=path.join(dir,'result.json')
    const result=require('child_process').spawnSync(require('electron'),[__filename,dir,report],{env,windowsHide:true,timeout:45000,encoding:'utf8'})
    if(fs.existsSync(report))console.log(fs.readFileSync(report,'utf8'))
    if(result.status!==0)throw Error(result.stderr||'Approval fixture failed')
  })().catch(error=>{console.error(error);process.exitCode=1})
} else {
  const {app,BrowserWindow}=require('electron')
  const [dir,report]=process.argv.slice(-2)
  app.whenReady().then(async()=>{
    const win=new BrowserWindow({show:false,webPreferences:{offscreen:true,sandbox:true,contextIsolation:true}})
    const js=code=>win.webContents.executeJavaScript(code)
    const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms))
    const click=label=>js(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='${label}').click()`)
    try {
      await win.loadFile(path.join(dir,'fixture.html'));await delay(400)
      if(await js('window.writes.length'))throw Error('Write on mount')
      if(!await js('document.body.innerText.includes("Saved approval wording.")'))throw Error('Saved line absent')
      await click('Damage Control');await delay(100)
      if(!await js('Boolean(document.querySelector("dialog[open]")) && document.body.innerText.includes("Contact your attorney")'))throw Error('Attorney popup absent')
      if(!await js('document.body.innerText.includes("Ringing")'))throw Error('Missing ringing')
      await delay(2100)
      if(!await js('document.body.innerText.includes("Delete all VODs and clips from the stream.") && document.body.innerText.includes("because you don’t like it")'))throw Error('Missing regret/DMCA satire')
      if(await js('window.writes.length'))throw Error('Joke sent a request')
      if(process.env.TEXT_UPDATES_PREVIEW) fs.writeFileSync(process.env.TEXT_UPDATES_PREVIEW,(await win.webContents.capturePage()).toPNG())
      await click('Close');await delay(100)
      await click('Approve');await delay(200)
      if(!await js('window.writes.includes("/automation/items/42/publish")'))throw Error('Wrong approve route')
      await js(`window.pending[0].answer='approve';window.pending[0].answered_where='discord';window.emit({type:'automation'})`);await delay(250)
      if(!await js('document.body.innerText.includes("Approved on Discord")'))throw Error('Discord answer absent')
      await js(`window.pending=[{...window.pending[0],item_id:43,answer:'',answered_where:''}];window.emit({type:'automation'})`);await delay(200)
      await click('Deny');await delay(200)
      if(!await js('window.writes.includes("/automation/items/43/skip")'))throw Error('Wrong deny route')
      fs.writeFileSync(report,JSON.stringify({ok:true,savedLine:true,jokeNoRequests:true,existingPublish:true,discordSync:true}))
      app.exit(0)
    }catch(error){fs.writeFileSync(report,JSON.stringify({ok:false,error:String(error)}));app.exit(1)}
  })
}
