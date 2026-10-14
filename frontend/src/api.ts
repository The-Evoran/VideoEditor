export type FileInfo = { path: string; exists: boolean } | null
export type Status = 'idle' | 'running' | 'done' | 'error'
export type Project = {
  id: string
  name: string
  video_path: string
  video_name: string
  video_exists: boolean
  created_at: string
  updated_at: string
  status: Status
  duration: number | null
  error: string | null
  srt: FileInfo
  json: FileInfo
  runs?: { started_at: string; ended_at: string | null; status: Status; error: string | null }[]
}
export type Job = {
  status: Status
  video: string | null
  project_id: string | null
  logs: string[]
  error: string | null
}

export const LABELS: Record<Status, string> = { idle: 'Non transcrit', running: 'En cours', done: 'Terminé', error: 'Erreur' }

export async function api<T>(url: string, method = 'GET', body?: unknown): Promise<T> {
  let r: Response
  try {
    r = await fetch(url, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new Error('Backend injoignable.')
  }
  const data = await r.json().catch(() => null)
  if (!r.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Erreur inattendue.')
  return data as T
}

export const fmtDate = (s: string) => new Date(s).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })
export const fmtDuration = (s: number | null) => {
  if (s == null) return '—'
  const t = Math.round(s)
  return `${Math.floor(t / 3600)}h${String(Math.floor((t % 3600) / 60)).padStart(2, '0')}m${String(t % 60).padStart(2, '0')}s`
}
export const fileLabel = (f: FileInfo) => (f ? (f.exists ? 'présent' : 'introuvable') : 'absent')
export const fileClass = (f: FileInfo) => (f ? (f.exists ? 'done' : 'error') : '')
