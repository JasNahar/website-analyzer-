import { useState } from 'react'
import Report from './Report.jsx'

const SERVER_ERROR = "The analyzer server didn't respond properly. Make sure the backend is running, then try again."

export default function App() {
  const [url, setUrl] = useState('')
  const [result, setResult] = useState({ status: 'idle' })
  const loading = result.status === 'loading'

  async function analyze(event) {
    event.preventDefault()
    setResult({ status: 'loading' })
    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      })
      const data = await res.json().catch(() => null)
      if (!res.ok || !data) throw new Error(data?.error?.message || SERVER_ERROR)
      setResult({ status: 'done', report: data })
    } catch (err) {
      setResult({ status: 'error', message: err instanceof TypeError ? SERVER_ERROR : err.message })
    }
  }

  return (
    <main className="container">
      <header className="hero">
        <h1>Website Analyzer</h1>
        <p>Check any page for SEO, accessibility, performance and security header issues.</p>
      </header>

      <form className="search" onSubmit={analyze}>
        <label htmlFor="url" className="visually-hidden">Website URL</label>
        <input
          id="url"
          type="text"
          inputMode="url"
          autoComplete="url"
          autoCapitalize="none"
          spellCheck={false}
          placeholder="example.com"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          required
        />
        <button type="submit" disabled={loading}>{loading ? 'Analyzing…' : 'Analyze'}</button>
      </form>

      <div aria-live="polite">
        {loading && <p className="status">Fetching the page and its files. This can take up to 30 seconds.</p>}
        {result.status === 'error' && (
          <div className="card error" role="alert">
            <strong>Couldn't analyze that page.</strong> {result.message}
          </div>
        )}
      </div>

      {result.status === 'done' && <Report report={result.report} />}
    </main>
  )
}
