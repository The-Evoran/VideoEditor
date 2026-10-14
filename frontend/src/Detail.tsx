import { useCallback, useEffect, useState } from 'react'
import Watch from './Watch'
import { api, fileClass, fileLabel, fmtDate, fmtDuration, LABELS, type Job, type Project } from './api'

export default function Detail({ id }: { id: string }) {
  const [p, setP] = useState<Project | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [name, setName] = useState('')
  const [msg, setMsg] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [proj, j] = await Promise.all([api<Project>(`/api/projects/${id}`), api<Job>('/api/transcriptions/current')])
      setP(proj)
      setJob(j)
      setName(n => n || proj.name)
    } catch (e) {
      setMsg((e as Error).message)
    }
  }, [id])

  useEffect(() => { refresh() }, [refresh])

  const running = p?.status === 'running'
  useEffect(() => {
    if (!running) return
    const t = setInterval(refresh, 1500)
    return () => clearInterval(t)
  }, [running, refresh])

  const act = async (fn: () => Promise<unknown>) => {
    setMsg('')
    try { await fn() } catch (e) { setMsg((e as Error).message) }
    refresh()
  }

  const rename = () => act(async () => { await api(`/api/projects/${id}`, 'PATCH', { name }); setName('') })
  const start = () => {
    if (p?.status === 'done' && !confirm('Cette VOD est déjà transcrite. Relancer la transcription ?')) return
    act(() => api(`/api/projects/${id}/transcription`, 'POST'))
  }
  const remove = async () => {
    if (!p || !confirm(`Supprimer le projet « ${p.name} » ?\nLa vidéo et les fichiers SRT/JSON ne seront pas supprimés.`)) return
    try {
      await api(`/api/projects/${id}`, 'DELETE')
      location.hash = '#/'
    } catch (e) { setMsg((e as Error).message) }
  }

  if (!p) return <><a href="#/">← Bibliothèque</a><p className={msg ? 'err' : ''}>{msg || 'Chargement…'}</p></>

  const logs = job?.project_id === id ? job.logs : []
  return (
    <>
      <a href="#/">← Bibliothèque</a>
      <h1>{p.name} <span className={`badge ${p.status}`}>{LABELS[p.status]}</span></h1>
      <div className="row">
        <input value={name} onChange={e => setName(e.target.value)} />
        <button onClick={rename} disabled={!name.trim() || name.trim() === p.name}>Renommer</button>
        <button className="danger" onClick={remove} disabled={running}>Supprimer</button>
      </div>
      {msg && <p className="err">{msg}</p>}
      {p.error && <p className="err">{p.error}</p>}

      <Watch id={id} videoExists={p.video_exists} version={`${p.srt?.exists}${p.json?.exists}${p.status === 'done' ? p.updated_at : ''}`} />

      <h2>Vidéo</h2>
      <p><b>{p.video_name}</b> {!p.video_exists && <span className="err">— fichier introuvable (déplacé ou supprimé ?)</span>}</p>
      <p><code>{p.video_path}</code></p>
      <p>Durée : {fmtDuration(p.duration)} · Créé : {fmtDate(p.created_at)} · Modifié : {fmtDate(p.updated_at)}</p>

      <h2>Transcription</h2>
      <ul>
        <li>SRT : <span className={`badge ${fileClass(p.srt)}`}>{fileLabel(p.srt)}</span> {p.srt && <code>{p.srt.path}</code>}</li>
        <li>JSON : <span className={`badge ${fileClass(p.json)}`}>{fileLabel(p.json)}</span> {p.json && <code>{p.json.path}</code>}</li>
      </ul>
      <button onClick={start} disabled={running || !p.video_exists}>
        {running ? 'Transcription en cours…' : p.status === 'idle' ? 'Lancer la transcription' : 'Relancer la transcription'}
      </button>
      {p.status === 'error' && (p.srt || p.json) && <p>Les résultats précédents ont été conservés.</p>}
      {(running || logs.length > 0) && <pre className="logs">{logs.join('\n') || 'Aucun message.'}</pre>}

      <h2>Historique</h2>
      {p.runs?.length === 0 && <p className="empty">Aucun traitement.</p>}
      <ul>
        {p.runs?.map((r, i) => (
          <li key={i}>
            <span className={`badge ${r.status}`}>{LABELS[r.status]}</span> {fmtDate(r.started_at)}
            {r.ended_at && <> → {fmtDate(r.ended_at)}</>}
            {r.error && <span className="err"> — {r.error}</span>}
          </li>
        ))}
      </ul>
    </>
  )
}
