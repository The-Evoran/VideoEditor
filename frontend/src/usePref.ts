import { useEffect, useState } from 'react'

/** Préférence d'affichage persistée (localStorage peut être indisponible). */
export function usePref<T>(key: string, init: T) {
  const [v, set] = useState<T>(() => { try { return JSON.parse(localStorage.getItem(key) ?? '') as T } catch { return init } })
  useEffect(() => { try { localStorage.setItem(key, JSON.stringify(v)) } catch { /* ignoré */ } }, [key, v])
  return [v, set] as const
}
