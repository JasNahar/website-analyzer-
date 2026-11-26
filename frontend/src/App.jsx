import { useEffect, useState } from 'react'
import Home from './Home.jsx'
import ReportPage from './ReportPage.jsx'
import ChatPage from './ChatPage.jsx'
import { api, navigate } from './api.js'

function useRoute() {
  const [path, setPath] = useState(window.location.pathname)
  useEffect(() => {
    const onPop = () => { setPath(window.location.pathname); window.scrollTo(0, 0) }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  return path
}

// Sessions are cached here so moving between the report and chat screens doesn't refetch.
const cache = {}

function useSession(id) {
  const [state, setState] = useState(() => (cache[id] ? { data: cache[id] } : { loading: true }))
  useEffect(() => {
    if (!id) return
    if (cache[id]) { setState({ data: cache[id] }); return }
    let live = true
    setState({ loading: true })
    api(`/report/${id}`)
      .then((data) => { cache[id] = data; if (live) setState({ data }) })
      .catch((err) => live && setState({ error: err.message }))
    return () => { live = false }
  }, [id])
  const update = (fn) => setState((s) => {
    const data = fn(s.data)
    cache[id] = data
    return { data }
  })
  return [state, update]
}

function Expired({ message }) {
  return (
    <div className="page narrow">
      <div className="card error" role="alert">
        <strong>Couldn't open this report.</strong> {message}
      </div>
      <button className="btn" onClick={() => navigate('/')}>Analyze a website</button>
    </div>
  )
}

function SessionRoute({ id, Page }) {
  const [state, update] = useSession(id)
  if (state.loading) return <div className="page narrow"><p className="status">Loading report…</p></div>
  if (state.error) return <Expired message={state.error} />
  return <Page session={state.data} update={update} />
}

export default function App() {
  const path = useRoute()
  const [status, setStatus] = useState(null)
  useEffect(() => { api('/status').then(setStatus).catch(() => {}) }, [])
  const report = path.match(/^\/report\/([\w-]+)/)
  const chat = path.match(/^\/chat\/([\w-]+)/)
  const id = (report || chat)?.[1]

  return (
    <>
      <nav className="topbar">
        <a href="/" className="brand" onClick={(e) => { e.preventDefault(); navigate('/') }}>
          <span className="logo" aria-hidden="true">W</span> WebSage
        </a>
        {id && (
          <div className="tabs">
            <a href={`/report/${id}`} aria-current={report ? 'page' : undefined}
              onClick={(e) => { e.preventDefault(); navigate(`/report/${id}`) }}>Report</a>
            <a href={`/chat/${id}`} aria-current={chat ? 'page' : undefined}
              onClick={(e) => { e.preventDefault(); navigate(`/chat/${id}`) }}>Ask AI</a>
          </div>
        )}
        {status && (
          <span className={`mode ${status.agentic ? 'on' : 'off'}`} title={status.llm.join(', ') || 'No LLM key configured'}>
            {status.agentic ? 'AI agent online' : 'Offline mode'}
          </span>
        )}
      </nav>
      <main>
        {report ? <SessionRoute key={`r${id}`} id={id} Page={ReportPage} />
          : chat ? <SessionRoute key={`c${id}`} id={id} Page={ChatPage} />
            : <Home onDone={(data) => { cache[data.id] = data; navigate(`/report/${data.id}`) }} />}
      </main>
      <footer className="footer">
        WebSage · agentic website analyzer · LangGraph ReAct agent + ChromaDB RAG
      </footer>
    </>
  )
}
