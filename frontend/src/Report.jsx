function grade(score) {
  return score >= 90 ? 'good' : score >= 50 ? 'ok' : 'poor'
}

function Score({ value, large = false }) {
  return (
    <div className={`score ${grade(value)}${large ? ' large' : ''}`} style={{ '--value': value }}>
      {value}
      <span className="visually-hidden"> out of 100</span>
    </div>
  )
}

function Issue({ issue }) {
  return (
    <article className={`issue sev-${issue.severity}`}>
      <div className="issue-head">
        <span className="badge">
          {issue.severity}
          <span className="visually-hidden"> severity</span>
        </span>
        <h3>{issue.title}</h3>
      </div>
      <p>{issue.detail}</p>
      <p><strong>How to fix:</strong> {issue.fix}</p>
      {issue.examples.length > 0 && (
        <ul className="examples">
          {issue.examples.map((example, i) => <li key={i}><code>{example}</code></li>)}
        </ul>
      )}
    </article>
  )
}

function Metrics({ metrics }) {
  return (
    <div className="metrics">
      <dl className="stats">
        {metrics.stats.map((stat) => (
          <div key={stat.label}>
            <dt>{stat.label}</dt>
            <dd>{stat.value}</dd>
          </div>
        ))}
      </dl>
      {metrics.largest_images.length > 0 && (
        <>
          <h3>Largest images</h3>
          <ol className="images">
            {metrics.largest_images.map((image) => (
              <li key={image.url}>
                <span className="size">{image.size}</span>
                <a href={image.url} target="_blank" rel="noreferrer">{image.url}</a>
              </li>
            ))}
          </ol>
        </>
      )}
      <p className="note">{metrics.note}</p>
    </div>
  )
}

function Category({ category }) {
  const { id, name, score, issues, passed, metrics } = category
  return (
    <details className="card category" id={id} open={issues.length > 0 || metrics !== null}>
      <summary>
        <div className="category-title">
          <h2>{name}</h2>
          <span className="muted">
            {issues.length ? `${issues.length} issue${issues.length === 1 ? '' : 's'}` : 'No issues found'}
          </span>
        </div>
        <Score value={score} />
      </summary>
      <div className="category-body">
        {metrics && <Metrics metrics={metrics} />}
        {issues.map((issue) => <Issue key={issue.title} issue={issue} />)}
        {passed.length > 0 && (
          <p className="passed"><strong>Passed:</strong> {passed.join(', ')}</p>
        )}
      </div>
    </details>
  )
}

export default function Report({ report }) {
  return (
    <section className="report" aria-label="Analysis report">
      <div className="card summary">
        <Score value={report.score} large />
        <div>
          <h2>Overall score</h2>
          <a className="url" href={report.url} target="_blank" rel="noreferrer">{report.url}</a>
        </div>
      </div>

      <ul className="category-scores">
        {report.categories.map((c) => (
          <li key={c.id}>
            <a className="card" href={`#${c.id}`}>
              <Score value={c.score} />
              <span>{c.name}</span>
            </a>
          </li>
        ))}
      </ul>

      {report.categories.map((c) => <Category key={c.id} category={c} />)}
    </section>
  )
}
