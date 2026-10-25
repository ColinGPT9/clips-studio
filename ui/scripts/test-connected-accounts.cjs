// Run with: node scripts/test-connected-accounts.cjs
// Exercises the real renderer client without Electron or a running backend.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const source = fs.readFileSync(path.join(__dirname, '../src/renderer/src/lib/connectedAccounts.ts'), 'utf8')
// The app's own target (tsconfig.web.json), so classes behave as they do in the app.
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
}).outputText

function client(session = 'desktop-session') {
  const calls = []
  const events = []
  let response = { ok: true, status: 200, json: async () => ({ ok: true }) }
  const module = { exports: {} }
  const context = {
    exports: module.exports,
    require: (name) => {
      assert.equal(name, './api')
      return { API_BASE: 'http://127.0.0.1:8765' }
    },
    window: {
      studio: { pluginSession: async () => session },
      dispatchEvent: (event) => events.push(event.type)
    },
    fetch: async (url, init) => {
      calls.push({ url, init })
      if (response instanceof Error) throw response
      return response
    },
    URL,
    Event: class Event { constructor(type) { this.type = type } }
  }
  vm.runInNewContext(compiled, context)
  return { ...module.exports, calls, events, respond: (value) => { response = value } }
}

async function main() {
  const absent = client('')
  await assert.rejects(absent.connectedAccounts.status(), /requires the Clips Kitty desktop app/)
  await assert.rejects(absent.streamWorkflows.startMonitor('twitch:123'), /requires the Clips Kitty desktop app/)
  await assert.rejects(absent.streamWorkflows.stopMonitor('twitch:123'), /requires the Clips Kitty desktop app/)
  await assert.rejects(absent.streamWorkflows.streams('twitch:123'), /requires the Clips Kitty desktop app/)
  assert.equal(absent.calls.length, 0, 'a browser without desktop authentication must make no account request')

  const c = client()
  await c.connectedAccounts.status()
  await c.connectedAccounts.configureTwitch('public-client')
  await c.connectedAccounts.connectTwitch(['clips:edit'])
  await c.connectedAccounts.poll('flow/one')
  await c.connectedAccounts.cancel('flow/one')
  await c.connectedAccounts.test('twitch:123')
  await c.connectedAccounts.disconnect('twitch:123')
  await c.connectedAccounts.watch('twitch:123')
  await c.streamWorkflows.streams('twitch:123')
  await c.streamWorkflows.startMonitor('twitch:123')
  await c.streamWorkflows.stopMonitor('twitch:123')
  assert.equal(c.calls.length, 11)
  for (const { url, init } of c.calls) {
    assert.ok(url.startsWith('http://127.0.0.1:8765/connected-accounts') ||
      url.startsWith('http://127.0.0.1:8765/stream-workflows'))
    assert.equal(init.headers['X-Clips-Kitty-Session'], 'desktop-session')
    assert.equal(init.redirect, 'error', 'a redirect must never forward the desktop session')
    assert.equal(init.cache, 'no-store')
  }
  assert.deepEqual(JSON.parse(c.calls[1].init.body), { client_id: 'public-client' })
  assert.deepEqual(JSON.parse(c.calls[2].init.body), { scopes: ['clips:edit'] })
  assert.ok(c.calls[3].url.endsWith('/flows/flow%2Fone/poll'))
  assert.ok(c.calls[5].url.endsWith('/twitch%3A123/test'))
  assert.equal(c.calls[6].init.method, 'DELETE')
  assert.equal(c.calls[8].url, 'http://127.0.0.1:8765/stream-workflows/twitch%3A123/streams')
  assert.equal(c.calls[8].init.method, undefined, 'loading counts must only read status')
  assert.equal(c.calls[9].url, 'http://127.0.0.1:8765/stream-workflows/twitch%3A123/monitor')
  assert.equal(c.calls[9].init.method, 'POST')
  assert.equal(c.calls[10].init.method, 'DELETE')
  // configureTwitch, test, disconnect and watch each tell the cards to re-read.
  assert.deepEqual(c.events, Array(4).fill('connected-accounts-changed'))

  // A failed test or watch can change the account (Reconnect required), so
  // the cards re-read the list even then.
  const failing = client()
  failing.respond({ ok: false, status: 401, json: async () => ({ detail: { code: 'invalid_token', message: 'Reconnect Twitch.' } }) })
  await assert.rejects(failing.connectedAccounts.test('twitch:123'), /Reconnect Twitch\./)
  await assert.rejects(failing.connectedAccounts.watch('twitch:123'), /Reconnect Twitch\./)
  assert.deepEqual(failing.events, Array(2).fill('connected-accounts-changed'))

  // The engine's status and fixed code reach the card, so a sign-in survives
  // a passing failure and stops on a final answer.
  const poll = client()
  poll.respond({ ok: false, status: 503, json: async () => ({ detail: { code: 'provider_unavailable', message: 'Twitch could not be reached. Try again.' } }) })
  await assert.rejects(poll.connectedAccounts.poll('flow'), (error) => {
    assert.equal(error.status, 503)
    assert.equal(error.code, 'provider_unavailable')
    assert.ok(poll.isTransientAccountError(error))
    return true
  })
  for (const [status, code] of [[400, 'access_denied'], [410, 'expired_token'], [404, 'invalid_flow']]) {
    poll.respond({ ok: false, status, json: async () => ({ detail: { code, message: 'Final.' } }) })
    await assert.rejects(poll.connectedAccounts.poll('flow'), (error) => {
      assert.equal(error.code, code)
      assert.ok(!poll.isTransientAccountError(error), code)
      return true
    })
  }
  poll.respond({ ok: false, status: 400, json: async () => ({ detail: { code: 'Not A Code!', message: 'Odd.' } }) })
  await assert.rejects(poll.connectedAccounts.poll('flow'), (error) => error.code === '')
  poll.respond(new Error('offline'))
  await assert.rejects(poll.connectedAccounts.poll('flow'), (error) => {
    assert.equal(error.status, 0)
    assert.ok(poll.isTransientAccountError(error), 'an unreachable engine is worth asking again')
    return true
  })
  await assert.rejects(client('').connectedAccounts.poll('flow'), (error) => !poll.isTransientAccountError(error))
  assert.ok(!poll.isTransientAccountError(new Error('plain')))

  // Disconnect hands back whether Twitch confirmed the revocation.
  const gone = client()
  gone.respond({ ok: true, status: 200, json: async () => ({ disconnected: true, revoked: false }) })
  assert.equal((await gone.connectedAccounts.disconnect('twitch:123')).revoked, false)

  const maliciousId = 'https://attacker.test/token?session=secret#fragment'
  await c.streamWorkflows.startMonitor(maliciousId)
  assert.equal(c.calls[11].url,
    `http://127.0.0.1:8765/stream-workflows/${encodeURIComponent(maliciousId)}/monitor`)

  c.respond({ ok: false, status: 502, json: async () => { throw new Error('raw response carried a credential') } })
  await assert.rejects(c.connectedAccounts.status(), (error) => {
    assert.equal(error.message, 'The account operation failed.')
    assert.ok(!error.message.includes('credential'))
    return true
  })
  c.respond({ ok: false, status: 409, json: async () => ({ detail: { message: 'Reconnect required.' } }) })
  await assert.rejects(c.connectedAccounts.status(), /Reconnect required\./)
  for (const detail of [
    'access_token=private-token', 'Authorization: Bearer private-token',
    'See https://provider.test?refresh_token=private-token',
    { message: 'client_secret: private-token', token: 'private-token' }
  ]) {
    c.respond({ ok: false, status: 500, json: async () => ({ detail }) })
    await assert.rejects(c.streamWorkflows.streams('twitch:123'), (error) => {
      assert.equal(error.message, 'The account operation failed.')
      return true
    })
  }
  c.respond(new Error('Transport failed at https://provider.test?access_token=private-token'))
  await assert.rejects(c.streamWorkflows.startMonitor('twitch:123'), (error) => {
    assert.ok(!error.message.includes('private-token'))
    assert.ok(!error.message.includes('provider.test'))
    return true
  })
  c.respond({ ok: true, status: 200, json: async () => { throw new Error('Malformed private-token') } })
  await assert.rejects(c.streamWorkflows.streams('twitch:123'), /unreadable response/)
  c.respond({ ok: true, status: 204, json: async () => { throw new Error('No JSON for 204') } })
  assert.equal(await c.connectedAccounts.cancel('expired'), undefined)

  assert.equal(c.twitchActivationUrl('https://www.twitch.tv/activate'), 'https://www.twitch.tv/activate')
  assert.equal(c.twitchActivationUrl('https://www.twitch.tv/activate/?user_code=ABCD'), 'https://www.twitch.tv/activate/?user_code=ABCD')
  for (const url of [
    'http://www.twitch.tv/activate', 'https://www.twitch.tv.attacker.test/activate',
    'https://www.twitch.tv@attacker.test/activate', 'https://attacker@www.twitch.tv/activate',
    'https://www.twitch.tv/activate/other', 'https://www.twitch.tv/activate#token',
    'https://www.twitch.tv:8443/activate', 'javascript:alert(1)', 'not a url'
  ]) assert.equal(c.twitchActivationUrl(url), null, url)
  assert.equal(c.accountPollDelay(undefined), 5000)
  assert.equal(c.accountPollDelay(Number.NaN), 5000)
  assert.equal(c.accountPollDelay(12), 12000)
  assert.equal(c.accountPollDelay(0), 1000)
  console.log('Connected Accounts renderer client checks passed.')
}

main().catch((error) => { console.error(error); process.exitCode = 1 })
