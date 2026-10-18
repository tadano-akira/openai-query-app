import { useEffect, useState } from 'react'

/** 依存ライブラリを増やさないための最小限のルーター（History API） */
export function navigate(to: string, { replace = false } = {}) {
  if (to === location.pathname + location.search) return
  if (replace) history.replaceState(null, '', to)
  else history.pushState(null, '', to)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function useLocation(): { path: string; params: URLSearchParams } {
  const [loc, setLoc] = useState(() => ({ path: location.pathname, search: location.search }))
  useEffect(() => {
    const onPop = () => setLoc({ path: location.pathname, search: location.search })
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  return { path: loc.path, params: new URLSearchParams(loc.search) }
}

/** `/docs/versions/:id` のようなパターンに一致したら params を返す */
export function match(pattern: string, path: string): Record<string, string> | null {
  const p = pattern.split('/')
  const s = path.replace(/\/$/, '').split('/')
  if (p.length !== s.length) return null
  const params: Record<string, string> = {}
  for (let i = 0; i < p.length; i++) {
    if (p[i].startsWith(':')) params[p[i].slice(1)] = decodeURIComponent(s[i])
    else if (p[i] !== s[i]) return null
  }
  return params
}
