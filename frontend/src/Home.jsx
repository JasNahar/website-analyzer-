import { useEffect, useState } from 'react'
import { api } from './api.js'

const STAGES = [
  'Supervisor is planning the analysis…',
  'Crawler agent is fetching pages (respecting robots.txt)…',
  'Link checker is testing every link…',
  'SEO, accessibility and performance agents are running checks…',
  'RAG agent is chunking and embedding the content into ChromaDB…',
  'Report composer is writing your summary…',
]

const EXAMPLES = ['python.org', 'developer.mozilla.org', 'wikipedia.org']

export default function Home({ onDone }) {
  const [url, setUrl] = useState('')
  const [state, setState] = useState({ status: 'idle' })
  const [stage, setStage] = useState(0)
  const loading = state.status === 'loading'

  useEffect(() => {
    if (!loading) return
    setStage(0)
    const t = setInterval(() => setStage((s) => Math.min(s + 1, STAGES.length - 1)), 3500)
    return () => clearInterval(t)
  }, [loading])

  async function analyze(event, value = url) {
    event?.preventDefault()
    if (!value.trim()) return
    setUrl(value)
    setState({ status: 'loading' })
    try {
      onDone(await api('/analyze', { url: value }))
    } catch (err) {
      setState({ status: 'error', message: err.message })
    }
  }

  return (
    <div className="page narrow home">
      <header className="hero">
        <h1>Analyze any website with an AI agent</h1>
        <p>
          WebSage crawls the site, runs SEO, accessibility, performance and link checks, then lets you ask
          follow-up questions answered from the site's own content.
        </p>
      </header>

      <form className="search" onSubmit={analyze}>
        <label htmlFor="url" className="visually-hidden">Website URL</label>
        <input id="url" type="text" inputMode="url" autoComplete="url" autoCapitalize="none" spellCheck={false}
          placeholder="Enter website URL, e.g. example.com" value={url} onChange={(e) => setUrl(e.target.value)}
          disabled={loading} required />
        <button type="submit" className="btn" disabled={loading}>{loading ? 'Analyzing…' : 'Analyze'}</button>
      </form>
      {!loading && (
        <p className="examples-row">
          Try: {EXAMPLES.map((ex) => (
            <button key={ex} type="button" className="link" onClick={(e) => analyze(e, ex)}>{ex}</button>
          ))}
        </p>
      )}

      <div aria-live="polite">
        {loading && (
          <div className="card progress">
            <div className="spinner" aria-hidden="true" />
            <div>
              <strong>Agents at work</strong>
              <p className="muted">{STAGES[stage]}</p>
              <p className="muted small">Usually 10–30 seconds.</p>
            </div>
          </div>
        )}
        {state.status === 'error' && (
          <div className="card error" role="alert"><strong>Couldn't analyze that site.</strong> {state.message}</div>
        )}
      </div>

      <section className="how" aria-label="How it works">
        <div className="card"><h2>1 · Agentic analysis</h2><p>A supervisor delegates to crawler, SEO, performance/accessibility and link-checker agents.</p></div>
        <div className="card"><h2>2 · RAG index</h2><p>Page content and findings are chunked, embedded with Gemini and stored in ChromaDB.</p></div>
        <div className="card"><h2>3 · Ask follow-ups</h2><p>A ReAct agent picks tools, searches the index and answers grounded in the site's content.</p></div>
      </section>
    </div>
  )
}
