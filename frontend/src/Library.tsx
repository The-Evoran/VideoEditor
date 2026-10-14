import { useEffect, useMemo, useState } from 'react'
import Icon from './Icon'
import { api, fileLabel, fmtDate, fmtDuration, LABELS, type Project } from './api'

export default function Library() {
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [videos, setVideos] = useState<string[]>([])
  const [video, setVideo] = useState('')
  const [name, setName] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('date-desc')
  const [msg, setMsg] = useState('')

  const load = () =>
    api<Project[]>('/api/projects').then(setProjects).catch(e => { setProjects([]); setMsg(e.message) })

  useEffect(() => {
    load()
    api<string[]>('/api/videos').then(setVideos).catch(e => setMsg(e.message))
  }, [])

  const shown = useMemo(() => {
    const s = search.trim().toLowerCase()
    const list = (projects ?? []).filter(p => p.name.toLowerCase().includes(s))
    const by: Record<string, (a: Project, b: Project) => number> = {
      'date-desc': (a, b) => b.created_at.localeCompare(a.created_at),
      'date-asc': (a, b) => a.created_at.localeCompare(b.created_at),
      'name-asc': (a, b) => a.name.localeCompare(b.name, 'fr'),
      'name-desc': (a, b) => b.name.localeCompare(a.name, 'fr'),
    }
    return list.sort(by[sort])
  }, [projects, search, sort])

  const create = async () => {
    setMsg('')
    try {
      const p = await api<Project>('/api/projects', 'POST', { video, name: name || null })
      location.hash = `#/projects/${p.id}`
    } catch (e) {
      setMsg((e as Error).message)
    }
  }

  const remove = async (p: Project) => {
    if (!confirm(`Supprimer le projet « ${p.name} » ?\nLa vidéo et les fichiers SRT/JSON ne seront pas supprimés.`)) return
    try {
      await api(`/api/projects/${p.id}`, 'DELETE')
      setMsg('')
    } catch (e) {
      setMsg((e as Error).message)
    }
    load()
  }

  return (
    <div className="page">
      <h1>Bibliothèque de VOD</h1>
      <div className="row lib-tools">
        <select className="grow" aria-label="Vidéo" value={video} onChange={e => setVideo(e.target.value)}>
          <option value="">— Choisir une vidéo existante —</option>
          {videos.map(v => <option key={v}>{v}</option>)}
        </select>
        <input value={name} onChange={e => setName(e.target.value)} placeholder="Nom (optionnel)" />
        <button className="primary" onClick={create} disabled={!video}>Créer le projet</button>
      </div>
      {msg && <p className="err">{msg}</p>}
      <div className="row lib-tools">
        <span className="search" style={{ position: 'relative', flex: 1, display: 'flex', alignItems: 'center' }}>
          <input type="search" style={{ width: '100%' }} value={search} onChange={e => setSearch(e.target.value)} placeholder="Rechercher un projet…" />
        </span>
        <select value={sort} onChange={e => setSort(e.target.value)} aria-label="Tri">
          <option value="date-desc">Plus récents</option>
          <option value="date-asc">Plus anciens</option>
          <option value="name-asc">Nom A → Z</option>
          <option value="name-desc">Nom Z → A</option>
        </select>
      </div>
      {projects === null && <p className="empty">Chargement…</p>}
      {projects?.length === 0 && !msg && <p className="empty">Aucun projet. Choisissez une vidéo ci-dessus pour en créer un.</p>}
      {projects && projects.length > 0 && shown.length === 0 && <p className="empty">Aucun projet ne correspond à « {search} ».</p>}
      {shown.length > 0 && (
        <div className="lib-list">
          {shown.map(p => (
            <div className="lib-row" key={p.id}>
              <div style={{ minWidth: 0 }}>
                <a className="name" href={`#/projects/${p.id}`}>{p.name}</a>
                <span className={`sub ${p.video_exists ? '' : 'err'}`}>{p.video_name}{p.video_exists ? '' : ' (introuvable)'} · SRT {p.srt?.exists ? '✓' : '—'} · JSON {p.json?.exists ? '✓' : '—'}</span>
              </div>
              <span className="num hide-s" title="Durée">{fmtDuration(p.duration)}</span>
              <span className="num hide-s" title={`Modifié : ${fmtDate(p.updated_at)}`}>{fmtDate(p.created_at)}</span>
              <span className="row" style={{ gap: 10 }}>
                <span className={`badge ${p.status}`} title={`SRT : ${fileLabel(p.srt)} · JSON : ${fileLabel(p.json)}`}>{LABELS[p.status]}</span>
              </span>
              <span className="actions">
                <button className="ghost danger" onClick={() => remove(p)} title="Supprimer le projet" aria-label="Supprimer le projet"><Icon n="trash" /></button>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
