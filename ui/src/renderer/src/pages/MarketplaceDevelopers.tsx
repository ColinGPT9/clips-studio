import { useState } from 'react'
import { t } from '../lib/i18n'
import { gitSource } from '../lib/marketplace'
import type { PluginSource } from '../lib/plugins'

/** The Marketplace's For developers section: install a pipeline you are
 *  writing from a folder on this PC or a Git repository at one commit. It is
 *  reached from a quiet link at the bottom of Browse, not a tab, because
 *  creators find pipelines in Browse; the words here stay precise. Installing
 *  goes through the same install screen as a listing. */
export default function MarketplaceDevelopers({
  manage,
  onInstall,
  onBack
}: {
  manage: boolean
  onInstall: (source: PluginSource) => void
  onBack: () => void
}): JSX.Element {
  const [folder, setFolder] = useState('')
  const [link, setLink] = useState('')
  const [commit, setCommit] = useState('')
  const [sub, setSub] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const pick = window.studio?.pickPluginFolder
  const fromGit = (): void => {
    const r = gitSource(link, commit, sub)
    setProblem(r.problem ?? null)
    if (r.source) onInstall(r.source)
  }
  return (
    <div className="space-y-4 max-w-3xl">
      <button className="btn-ghost !py-1.5" onClick={onBack}>
        ← {t('Back to Browse')}
      </button>
      <div>
        <h3 className="text-xl font-bold">{t('For developers')}</h3>
        <p className="text-sm text-muted mt-1">
          {t('Clips Kitty hasn’t checked pipelines installed here. To find pipelines, use Browse.')}
        </p>
      </div>
      {!manage && (
        <div className="card text-sm text-warn">{t('Installing pipelines needs the Clips Kitty desktop app.')}</div>
      )}
      <p className="text-sm text-warn">
        ⚠{' '}
        {t(
          'A pipeline from a folder or a link is not listed anywhere. Install only code you trust: it can do anything you can do on this PC.'
        )}
      </p>
      <section className="card space-y-3">
        <h3 className="font-semibold">{t('From GitHub or another Git host')}</h3>
        <label className="block space-y-1">
          <span className="label">{t('Repository')}</span>
          <input
            className="input"
            placeholder="https://github.com/example-dev/example-plugin"
            value={link}
            onChange={(e) => setLink(e.target.value)}
          />
        </label>
        <label className="block space-y-1">
          <span className="label">{t('Commit (all 40 characters)')}</span>
          <input
            className="input font-mono"
            placeholder={t('Or paste a GitHub link that ends in /tree/<commit>')}
            value={commit}
            onChange={(e) => setCommit(e.target.value)}
          />
        </label>
        <label className="block space-y-1">
          <span className="label">{t('Folder in the repository (if it isn’t at the top)')}</span>
          <input className="input" placeholder="plugins/my-plugin" value={sub} onChange={(e) => setSub(e.target.value)} />
        </label>
        {problem && <p className="text-sm text-error">{problem}</p>}
        <button className="btn-accent" disabled={!manage || (!link.trim() && !commit.trim())} onClick={fromGit}>
          {t('Look at it before installing')}
        </button>
        <p className="text-xs text-muted">
          {t(
            'A commit, not a branch: what you look at is exactly what gets installed. Clips Kitty reads the files without running anything from them.'
          )}
        </p>
      </section>
      <section className="card space-y-3">
        <h3 className="font-semibold">{t('From a folder on this PC')}</h3>
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder={t('The folder with clipskitty.yaml in it')}
            value={folder}
            onChange={(e) => setFolder(e.target.value)}
          />
          {pick && (
            <button
              className="btn-ghost shrink-0"
              onClick={() => void pick().then((p) => p && setFolder(p))}
            >
              {t('Choose…')}
            </button>
          )}
        </div>
        <button
          className="btn-accent"
          disabled={!manage || !folder.trim()}
          onClick={() => onInstall({ kind: 'folder', path: folder.trim() })}
        >
          {t('Look at it before installing')}
        </button>
        <p className="text-xs text-muted">
          {t('For pipelines you are writing. Installing the same version again replaces it.')}
        </p>
      </section>
    </div>
  )
}
