const SERVER_ERROR = "The WebSage server didn't respond properly. Please try again in a moment."

export async function api(path, body) {
  let res
  try {
    res = await fetch(`/api${path}`, body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new Error(SERVER_ERROR)
  }
  const data = await res.json().catch(() => null)
  if (!res.ok || !data) {
    const msg = data?.error?.message || (Array.isArray(data?.detail) ? data.detail[0]?.msg : null) || SERVER_ERROR
    throw new Error(msg)
  }
  return data
}

export function navigate(to) {
  window.history.pushState({}, '', to)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function host(url) {
  try { return new URL(url).host } catch { return url }
}
