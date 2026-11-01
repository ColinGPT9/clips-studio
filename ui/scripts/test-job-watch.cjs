// A missing terminal event must recover even while automation keeps talking.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const source = fs.readFileSync(path.join(__dirname, '../src/renderer/src/lib/useJobWatch.ts'), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
}).outputText

function harness({ active = true, jobs = [], unreachable = false } = {}) {
  const clock = { now: 0 }
  const lastEventAt = { current: 0 }
  const timers = new Map()
  const cleanups = []
  const state = { calls: 0, settled: 0 }
  const module = { exports: {} }
  let nextTimer = 0
  class ClockDate extends Date { static now() { return clock.now } }
  vm.runInNewContext(compiled, {
    module, exports: module.exports, Date: ClockDate,
    setInterval(callback, interval) {
      const id = ++nextTimer
      timers.set(id, { callback, interval, next: clock.now + interval })
      return id
    },
    clearInterval(id) { timers.delete(id) },
    require(name) {
      if (name === 'react') return {
        useRef: value => ({ current: value }),
        useEffect: effect => { const cleanup = effect(); if (cleanup) cleanups.push(cleanup) }
      }
      if (name === './api') return { api: { jobs: async () => {
        state.calls++
        if (unreachable) throw new Error('Fixture backend unavailable')
        return jobs
      } } }
      throw new Error('Unexpected import: ' + name)
    }
  }, { filename: 'useJobWatch.js' })
  module.exports.useJobWatch({ active, lastEventAt, onSettled: () => state.settled++ })
  return {
    state,
    async advance(ms, continuousEvents = false) {
      for (let elapsed = 0; elapsed < ms; elapsed += 1000) {
        clock.now += 1000
        if (continuousEvents) lastEventAt.current = clock.now
        for (const timer of timers.values()) {
          if (timer.next <= clock.now) { timer.next += timer.interval; timer.callback() }
        }
        await Promise.resolve()
        await Promise.resolve()
      }
    },
    cleanup() { for (const cleanup of cleanups) cleanup() }
  }
}

async function main() {
  const finished = harness()
  await finished.advance(60_000, true)
  assert.ok(finished.state.calls > 0, 'Continuous watch events must not prevent an authoritative job check')
  assert.equal(finished.state.settled, 1, 'A finished queue must clear stale processing after a missed terminal event')
  finished.cleanup()

  for (const status of ['running', 'queued']) {
    const live = harness({ jobs: [{ id: 1, status }] })
    await live.advance(110_000, true)
    assert.ok(live.state.calls >= 2, 'Busy event streams must still allow periodic verification')
    assert.equal(live.state.settled, 0, status + ' work must not be cleared')
    live.cleanup()
  }

  const unavailable = harness({ unreachable: true })
  await unavailable.advance(60_000, true)
  assert.ok(unavailable.state.calls > 0)
  assert.equal(unavailable.state.settled, 0, 'An unreachable backend is not evidence that work finished')
  unavailable.cleanup()

  const idle = harness({ active: false })
  await idle.advance(60_000, true)
  assert.equal(idle.state.calls, 0, 'An idle page must not start polling')
  idle.cleanup()
  console.log('Job-watch recovery passed: continuous events, running/queued work, unreachable backend and idle state.')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
