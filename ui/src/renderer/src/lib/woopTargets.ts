import { platformLabel } from './uploadpost'

export interface WoopAccount {
  id: string
  platform: string
  username: string
}

/** Show the actual profiles, since a platform name does not identify a recipient. */
export function woopTargetLabel(platform: string, accounts: WoopAccount[] = []): string {
  const names = [...new Set(accounts.filter(account => account.platform === platform)
    .map(account => account.username.trim().replace(/^@/, '')).filter(Boolean))]
  return `${platformLabel(platform)}${names.length ? ` ${names.map(name => `@${name}`).join(', ')}` : ''}`
}
