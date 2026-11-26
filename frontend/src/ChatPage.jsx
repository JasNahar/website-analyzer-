import { useEffect, useRef, useState } from 'react'
import Markdown from './Markdown.jsx'
import { api, host } from './api.js'

const SUGGESTIONS = [
  'Why is my accessibility score low?',
  'Show me the pages with missing alt text',
  'What should I fix first?',
  'What is this website about?',
]

function Steps({ steps, model, ms, cached }) {
  if (!steps?.length) return null
  const tools = [...new Set(steps.filter((s) => s.type === 'action').map((s) => s.tool))]
  return (
    <details className="steps">
      <summary>
        {cached ? 'From session cache' : tools.length ? `Agent used ${tools.join(', ')}` : 'Agent reasoning'}
        {model && model !== 'offline' && ` · ${model}`}{ms ? ` · ${(ms / 1000).toFixed(1)}s` : ''}
      </summary>
      <ol>
        {steps.map((s, i) => (
          <li key={i} className={`step ${s.type}`}>
            {s.type === 'action' && <><b>Action:</b> <code>{s.tool}[{s.input ? `"${s.input}"` : ''}]</code></>}
            {s.type === 'observation' && <><b>Observation:</b> {s.text}</>}
            {s.type === 'thought' && <><b>Thought:</b> {s.text}</>}
          </li>
        ))}
      </ol>
    </details>
  )
}

export default function ChatPage({ session, update }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const end = useRef(null)
  const messages = session.chat || []

  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [messages.length, busy])

  async function send(event, value = text) {
    event?.preventDefault()
    const q = value.trim()
    if (!q || busy) return
    setText('')
    setError('')
    setBusy(true)
    update((d) => ({ ...d, chat: [...(d.chat || []), { role: 'user', text: q }] }))
    try {
      const reply = await api(`/chat/${session.id}`, { message: q })
      update((d) => ({ ...d, chat: [...d.chat, { role: 'assistant', ...reply }] }))
    } catch (err) {
      setError(err.message)
      update((d) => ({ ...d, chat: d.chat.slice(0, -1) }))
      setText(q)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page chat-page">
      <header className="banner">
        <h1>Ask about this website</h1>
        <span>{host(session.report.url)} · overall score {session.report.score}/100</span>
      </header>

      <div className="chat" aria-live="polite">
        {messages.length === 0 && (
          <div className="empty">
            <p className="muted">Ask anything about {host(session.report.url)}. The agent decides which tools to run and grounds its answer in the site's content.</p>
            <div className="chips">
              {SUGGESTIONS.map((s) => <button key={s} className="chip" onClick={(e) => send(e, s)}>{s}</button>)}
            </div>
          </div>
        )}
        {messages.map((m, i) => m.role === 'user' ? (
          <div key={i} className="bubble user">{m.text}</div>
        ) : (
          <div key={i} className="bubble bot">
            <Markdown text={m.answer} />
            <Steps steps={m.steps} model={m.model} ms={m.ms} cached={m.cached} />
          </div>
        ))}
        {busy && <div className="bubble bot typing"><span /><span /><span /> <em>Agent is thinking…</em></div>}
        <div ref={end} />
      </div>

      {error && <div className="card error" role="alert">{error}</div>}

      <form className="composer" onSubmit={send}>
        <label htmlFor="q" className="visually-hidden">Your question</label>
        <input id="q" placeholder="Type your question..." value={text} onChange={(e) => setText(e.target.value)}
          maxLength={1000} autoComplete="off" />
        <button className="btn" type="submit" disabled={busy || !text.trim()}>Send</button>
      </form>
    </div>
  )
}
