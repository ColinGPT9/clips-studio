import { useEffect, useState } from 'react'
import { api } from './api'
import type { AIStatus } from './types'

/** Where the AI runs, for the places that suggest OpenRouter while it is
 *  local. Shared and briefly cached: a queue of fifty jobs asks once. */

let cached: { at: number; request: Promise<AIStatus> } | null = null
const FRESH_MS = 30_000

export function forgetAIStatus(): void {
  cached = null
}

export function useAIStatus(): AIStatus | null {
  const [status, setStatus] = useState<AIStatus | null>(null)
  useEffect(() => {
    if (!cached || Date.now() - cached.at > FRESH_MS) {
      cached = { at: Date.now(), request: api.ai() }
    }
    let live = true
    cached.request.then((s) => live && setStatus(s)).catch(() => live && setStatus(null))
    return () => {
      live = false
    }
  }, [])
  return status
}

/** True only once the engine has said the AI runs on this PC. */
export function useLocalAI(): boolean {
  const status = useAIStatus()
  return !!status && status.active.local
}
