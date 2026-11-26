// Tiny, safe renderer for the subset of Markdown the agent produces: paragraphs, "- " bullets,
// numbered lists, **bold**, _italic_/*italic* and `code`. No HTML is ever injected.
function inline(text, keyBase) {
  const parts = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`|_[^_]+_|\*[^*\s][^*]*\*)/g
  let last = 0
  let m
  let i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    const tok = m[0]
    const key = `${keyBase}-${i++}`
    if (tok.startsWith('**')) parts.push(<strong key={key}>{tok.slice(2, -2)}</strong>)
    else if (tok.startsWith('`')) parts.push(<code key={key}>{tok.slice(1, -1)}</code>)
    else parts.push(<em key={key}>{tok.slice(1, -1)}</em>)
    last = m.index + tok.length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

function List({ list, k }) {
  const Tag = list.ordered ? 'ol' : 'ul'
  return (
    <Tag>
      {list.items.map((it, j) => (
        <li key={j}>
          {inline(it.text, `${k}-${j}`)}
          {it.children.length > 0 && (
            <ul>{it.children.map((c, n) => <li key={n}>{inline(c, `${k}-${j}-${n}`)}</li>)}</ul>
          )}
        </li>
      ))}
    </Tag>
  )
}

export default function Markdown({ text }) {
  const blocks = []
  let list = null
  text.split('\n').forEach((raw) => {
    const line = raw.trimEnd()
    const bullet = line.match(/^(\s*)(?:[-*•+]|\d+[.)])\s+(.*)/)
    if (bullet) {
      const nested = bullet[1].length >= 2 && list && list.items.length > 0
      if (nested) { list.items[list.items.length - 1].children.push(bullet[2]); return }
      if (!list) { list = { ordered: /^\s*\d/.test(line), items: [] }; blocks.push(list) }
      list.items.push({ text: bullet[2], children: [] })
      return
    }
    if (!line.trim()) return // blank lines don't end a list (models often space out list items)
    list = null
    blocks.push({ p: line.replace(/^#+\s*/, '') })
  })
  return (
    <div className="md">
      {blocks.map((b, i) => (b.items ? <List key={i} list={b} k={i} /> : <p key={i}>{inline(b.p, i)}</p>))}
    </div>
  )
}
