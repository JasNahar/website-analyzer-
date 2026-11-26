import { useState } from 'react'
import Markdown from './Markdown.jsx'
import { host, navigate } from './api.js'

const TAG = { seo: 'SEO', accessibility: 'Accessibility', performance: 'Performance', security: 'Security', links: 'Links' }
const SEV = { high: 0, medium: 1, low: 2 }

function grade(score) {
  return score >= 90 ? 'good' : score >= 50 ? 'ok' : 'poor'
}

function Score({ value, large = false }) {
  return (
    <div className={`score ${grade(value)}${large ? ' large' : ''}`} style={{ '--value': value }}>
      {value}<span className="visually-hidden"> out of 100</span>
    </div>
  )
}

function IssueRow({ issue }) {
  return (
    <li>
      <details className="issue-row">
        <summary>
          <span className={`sev sev-${issue.severity}`} title={`${issue.severity} severity`}>
            <span className="visually-hidden">{issue.severity} severity: </span>
          </span>
          <span className="issue-title">{issue.title}</span>
          <span className={`tag tag-${issue.category}`}>{TAG[issue.category]}</span>
        </summary>
        <div className="issue-body">
          <p><span className={`pill sev-${issue.severity}`}>{issue.severity} severity</span> {issue.detail}</p>
          <p><strong>How to fix:</strong> {issue.fix}</p>
          {issue.pages?.length > 0 && <p className="muted"><strong>Pages:</strong> {issue.pages.join(', ')}</p>}
          {issue.examples?.length > 0 && (
            <ul className="examples">{issue.examples.map((ex, i) => <li key={i}><code>{ex}</code></li>)}</ul>
          )}
        </div>
      </details>
    </li>
  )
}

export default function ReportPage({ session }) {
  const { report, summary, trace, rag, id } = session
  const [filter, setFilter] = useState('all')
  const issues = report.categories
    .flatMap((c) => c.issues)
    .sort((a, b) => SEV[a.severity] - SEV[b.severity])
  const shown = filter === 'all' ? issues : issues.filter((i) => i.category === filter)
  const passed = report.categories.flatMap((c) => c.passed.map((p) => `${p}`))
  const perf = report.categories.find((c) => c.id === 'performance')?.metrics

  return (
    <div className="page">
      <header className="banner">
        <h1>Report: {host(report.url)}</h1>
        <a href={report.url} target="_blank" rel="noreferrer">{report.url}</a>
      </header>

      <section className="overview">
        <div className="card summary">
          <Score value={report.score} large />
          <div className="summary-text">
            <h2>AI summary</h2>
            {summary ? <Markdown text={summary} /> : <p className="muted">No summary.</p>}
            <button className="btn" onClick={() => navigate(`/chat/${id}`)}>Ask about this website →</button>
          </div>
        </div>
        <ul className="category-scores">
          {report.categories.map((c) => (
            <li key={c.id}>
              <button className={`card cat ${filter === c.id ? 'active' : ''}`}
                onClick={() => setFilter(filter === c.id ? 'all' : c.id)} aria-pressed={filter === c.id}>
                <Score value={c.score} />
                <span>{c.name}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="issues-h">
        <div className="section-head">
          <h2 id="issues-h">
            {filter === 'all' ? `${issues.length} issues found` : `${shown.length} ${TAG[filter]} issue${shown.length === 1 ? '' : 's'}`}
          </h2>
          {filter !== 'all' && <button className="link" onClick={() => setFilter('all')}>Show all</button>}
        </div>
        {shown.length ? (
          <ul className="issue-list">{shown.map((i) => <IssueRow key={`${i.category}-${i.title}`} issue={i} />)}</ul>
        ) : (
          <p className="card muted">No issues in this category. Nice work.</p>
        )}
        <p className="muted small">Analysed pages: {report.pages.join(', ')}</p>
      </section>

      <div className="two-col">
        <details className="card panel" open>
          <summary><h2>Agent activity</h2></summary>
          <ol className="trace">
            {trace.map((t, i) => (
              <li key={i}><strong>{t.agent}</strong> <span>{t.text}</span>{t.ms > 0 && <em> · {(t.ms / 1000).toFixed(1)}s</em>}</li>
            ))}
          </ol>
          <p className="muted small">RAG index: {rag.chunks} chunks · embeddings: {rag.embedding}</p>
        </details>
        <details className="card panel">
          <summary><h2>Passed checks ({passed.length})</h2></summary>
          <p className="muted">{passed.join(' · ')}</p>
          {perf && (
            <>
              <h3>Performance numbers</h3>
              <dl className="stats">
                {perf.stats.map((s) => <div key={s.label}><dt>{s.label}</dt><dd>{s.value}</dd></div>)}
              </dl>
              <p className="muted small">{perf.note}</p>
            </>
          )}
        </details>
      </div>
    </div>
  )
}
