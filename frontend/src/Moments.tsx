import { useCallback, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import Icon from './Icon'
import { usePref } from './usePref'
import { api } from './api'
import { fmtTime, parseTime } from './transcript'

const CATS: Record<string, string> = {
  topic: 'Sujet principal', argument: 'Argumentaire', punchline: 'Punchline / réaction', controversy: 'Controverse',
  announcement: 'Annonce / info', standalone: 'Clip autonome', review: 'À vérifier',
}
const STATUS: Record<string, string> = { pending: 'En attente', running: 'En cours', done: 'Terminé', cancelled: 'Annulé', error: 'Erreur' }
const DECISION: Record<string, string> = { pending: 'À décider', accepted: 'Accepté', rejected: 'Rejeté' }

type Model = { name: string; size: number | null; context: number | null }
type Cfg = { model: string | null; window: number; overlap: number; num_ctx: number }
type Ollama = { available: boolean; models: Model[]; error: string | null; config: Cfg }
type Analysis = { id: number; model: string; status: string; total: number; done: number; error: string | null; created_at: string; params: Cfg }
export type Moment = {
  id: number; title: string; summary: string; category: string; justification: string; passage: string; score: number
  confidence: string; needs_review: boolean; aligned: boolean; t_start: number | null; t_end: number | null
  auto_start: number | null; auto_end: number | null; hint: [number, number] | null; sources: unknown[]
  status: 'pending' | 'accepted' | 'rejected'; favorite: boolean; note: string; edited: boolean
}

function TimeField({ value, onCommit }: { value: number | null; onCommit: (t: number) => void }) {
  const [txt, setTxt] = useState<string | null>(null)
  const shown = txt ?? (value == null ? '' : fmtTime(value, true))
  const commit = () => {
    const t = txt == null ? null : parseTime(txt)
    if (t != null && t !== value) onCommit(t)
    setTxt(null)
  }
  return <input className="tf" value={shown} placeholder="HH:MM:SS.mmm" onChange={e => setTxt(e.target.value)} onBlur={commit}
    onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') setTxt(null) }} />
}

export default function Moments({ pid, canAnalyse, onPlay, onMoments, slot }: {
  pid: string; canAnalyse: boolean; onPlay: (start: number, end: number | null) => void; onMoments: (m: Moment[]) => void; slot: HTMLElement | null
}) {
  const [ol, setOl] = useState<Ollama | null>(null)
  const [analyses, setAnalyses] = useState<Analysis[]>([])
  const [sel, setSel] = useState<number | null>(null)
  const [moments, setMoments] = useState<Moment[]>([])
  const [msg, setMsg] = useState('')
  const [open, setOpen] = useState<number | null>(null)
  const [cat, setCat] = usePref('vod.cat', '')
  const [min, setMin] = usePref('vod.min', 1)
  const [dec, setDec] = usePref('vod.dec', '')
  const [sort, setSort] = usePref<'time' | 'score'>('vod.sort', 'time')
  const [cfgOpen, setCfgOpen] = useState(false)

  const fail = (e: unknown) => setMsg((e as Error).message)
  const loadAnalyses = useCallback(async () => {
    try { setAnalyses(await api<Analysis[]>(`/api/projects/${pid}/analyses`)) } catch (e) { fail(e) }
  }, [pid])
  const loadOllama = useCallback(async () => {
    try { setOl(await api<Ollama>('/api/ollama')) } catch (e) { fail(e) }
  }, [])
  useEffect(() => { loadOllama(); loadAnalyses() }, [loadOllama, loadAnalyses])

  const running = analyses.find(a => a.status === 'running')
  useEffect(() => {
    if (!running) return
    const t = setInterval(loadAnalyses, 1500)
    return () => clearInterval(t)
  }, [running, loadAnalyses])

  // analyse affichée : celle choisie, sinon la dernière terminée
  const shown = sel ?? analyses.find(a => a.status === 'done')?.id ?? null
  const shownStatus = analyses.find(a => a.id === shown)?.status
  useEffect(() => {
    if (shown == null) { setMoments([]); return }
    api<Moment[]>(`/api/projects/${pid}/moments?analysis_id=${shown}`).then(setMoments, fail)
  }, [pid, shown, shownStatus])

  useEffect(() => { onMoments(moments) }, [moments, onMoments])

  const cfg = ol?.config
  const setCfg = async (patch: Partial<Cfg>) => {
    setMsg('')
    try { await api('/api/analysis/config', 'PUT', patch); loadOllama() } catch (e) { fail(e) }
  }
  const start = async () => {
    setMsg('')
    try { await api(`/api/projects/${pid}/analyses`, 'POST', {}); setSel(null); await loadAnalyses() } catch (e) { fail(e) }
  }
  const cancel = async () => { try { await api(`/api/analyses/${running!.id}/cancel`, 'POST') } catch (e) { fail(e) } }
  const remove = async () => {
    if (shown == null || !confirm('Supprimer cette analyse et ses moments (notes et décisions comprises) ? Le transcript n\'est pas touché.')) return
    try { await api(`/api/analyses/${shown}`, 'DELETE'); setSel(null); loadAnalyses() } catch (e) { fail(e) }
  }
  const patch = async (m: Moment, body: Record<string, unknown>) => {
    try {
      const n = await api<Moment>(`/api/moments/${m.id}`, 'PATCH', body)
      setMoments(ms => ms.map(x => (x.id === n.id ? n : x)))
    } catch (e) { fail(e) }
  }

  const list = useMemo(() => {
    const l = moments.filter(m => (!cat || m.category === cat) && m.score >= min
      && (!dec || (dec === 'fav' ? m.favorite : m.status === dec)))
    const key = (m: Moment) => m.t_start ?? m.hint?.[0] ?? 0
    return l.sort(sort === 'score' ? (a, b) => b.score - a.score || key(a) - key(b) : (a, b) => key(a) - key(b))
  }, [moments, cat, min, dec, sort])

  const play = (m: Moment) => {
    setOpen(m.id)
    if (m.t_start != null && m.t_end != null) onPlay(m.t_start, m.t_end)
    else if (m.hint) onPlay(m.hint[0], null) // zone du bloc : approximation, jamais un timecode inventé
  }
  const label = (m: Model) => `${m.name}${m.size ? ` · ${(m.size / 1e9).toFixed(1)} Go` : ''}${m.context ? ` · ctx ${Math.round(m.context / 1024)}k` : ''}`

  const last = analyses[0]
  const prog = last && last.total > 0 ? ` ${last.done}/${last.total}` : ''
  const tc = (m: Moment) => (m.t_start == null ? `zone ≈ ${fmtTime(m.hint?.[0] ?? 0)}` : `${fmtTime(m.t_start)} → ${fmtTime(m.t_end ?? 0)}`)
  const stop = (e: { stopPropagation: () => void }) => e.stopPropagation()
  const stopped = last && last.status !== 'done' && last.status !== 'running' && last.done > 0

  const headerTools = slot && createPortal(<>
    {last && <span className={`badge ${last.status === 'cancelled' ? '' : last.status}`} title="Statut de l'analyse Ollama">Analyse : {STATUS[last.status]}{prog}</span>}
    {running
      ? <button className="danger" onClick={cancel} title="Annuler l'analyse en cours"><Icon n="x" /> Annuler</button>
      : <button className="primary" onClick={start} disabled={!cfg?.model || !canAnalyse || !ol?.available}
        title={!canAnalyse ? 'Transcription requise' : !cfg?.model ? 'Choisissez un modèle dans les réglages IA' : 'Analyser la VOD avec Ollama (reprend les blocs restants)'}>
        <Icon n="spark" /> {last ? (last.status === 'done' ? "Relancer l'analyse" : "Reprendre l'analyse") : "Lancer l'analyse"}
      </button>}
  </>, slot)

  return (
    <section className="panel" aria-label="Suggestions de l'IA">
      {headerTools}
      <div className="panel-head">
        <h2 title="Suggestions de l'IA — à vérifier, rien n'est validé automatiquement">Suggestions IA</h2>
        <small>{moments.length > 0 ? `${list.length}/${moments.length}` : ''}</small>
        <span className="spacer" />
        <button className={`ghost ${cfgOpen ? 'on' : ''}`} onClick={() => setCfgOpen(o => !o)} title="Réglages de l'analyse (modèle, blocs)" aria-label="Réglages de l'analyse"><Icon n="gear" /></button>
      </div>
      {msg && <p className="msg err">{msg}</p>}
      {ol && !ol.available && <p className="msg err">{ol.error} <button onClick={loadOllama}>Réessayer</button></p>}
      {ol?.available && ol.models.length === 0 && <p className="msg err">Aucun modèle installé dans Ollama (aucun téléchargement automatique).</p>}
      {!canAnalyse && <p className="msg empty" style={{ padding: 6 }}>Transcription requise pour lancer une analyse.</p>}
      {last && (last.error || stopped) && (
        <p className={`msg ${last.status === 'error' ? 'err' : ''}`}>
          {last.error}{stopped && " « Reprendre l'analyse » continue avec les blocs restants."}
        </p>
      )}
      {(cfgOpen || (ol?.available && cfg && !cfg.model)) && ol?.available && cfg && <>
        <div className="row cfg">
          <select aria-label="Modèle" value={cfg.model ?? ''} onChange={e => setCfg({ model: e.target.value })}>
            <option value="" disabled>Choisir un modèle…</option>
            {ol.models.map(m => <option key={m.name} value={m.name}>{label(m)}</option>)}
          </select>
          <label title="Durée d'un bloc analysé">bloc <input type="number" min={60} max={1800} step={30} defaultValue={cfg.window} key={`w${cfg.window}`}
            onBlur={e => +e.target.value !== cfg.window && setCfg({ window: +e.target.value })} /> s</label>
          <label title="Chevauchement entre blocs">chevauch. <input type="number" min={0} step={5} defaultValue={cfg.overlap} key={`o${cfg.overlap}`}
            onBlur={e => +e.target.value !== cfg.overlap && setCfg({ overlap: +e.target.value })} /> s</label>
          <label title="Contexte demandé à Ollama (VRAM)">ctx <input type="number" min={2048} step={1024} defaultValue={cfg.num_ctx} key={`c${cfg.num_ctx}`}
            onBlur={e => +e.target.value !== cfg.num_ctx && setCfg({ num_ctx: +e.target.value })} /></label>
        </div>
        {analyses.length > 0 && (
          <div className="row cfg">
            <select aria-label="Analyse" value={shown ?? ''} onChange={e => setSel(+e.target.value)}>
              {analyses.map(a => <option key={a.id} value={a.id}>
                #{a.id} · {a.model} · {a.params.window}s/{a.params.overlap}s · {STATUS[a.status]}{a.total ? ` ${a.done}/${a.total} blocs` : ''}
              </option>)}
            </select>
            <button className="danger" onClick={remove} disabled={shown == null || shown === running?.id} title="Supprimer cette analyse et ses moments"><Icon n="trash" /> Supprimer</button>
          </div>
        )}
      </>}

      {moments.length > 0 && (
        <div className="filters">
          <select aria-label="Catégorie" value={cat} onChange={e => setCat(e.target.value)}>
            <option value="">Toutes catégories</option>
            {Object.entries(CATS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <select aria-label="Statut" value={dec} onChange={e => setDec(e.target.value)}>
            <option value="">Tous statuts</option><option value="pending">À décider</option><option value="accepted">Acceptés</option>
            <option value="rejected">Rejetés</option><option value="fav">Favoris</option>
          </select>
          <select aria-label="Score minimum" title="Score minimum" value={min} onChange={e => setMin(+e.target.value)} style={{ flex: 'none' }}>
            {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(n => <option key={n} value={n}>score ≥ {n}</option>)}
          </select>
          <select aria-label="Tri" value={sort} onChange={e => setSort(e.target.value as 'time' | 'score')} style={{ flex: 'none' }}>
            <option value="time">Chronologique</option><option value="score">Pertinence</option>
          </select>
        </div>
      )}

      <div className="panel-body">
        {analyses.length === 0 && ol?.available && <p className="empty">Aucune analyse. Lancez-en une depuis l'en-tête.</p>}
        {moments.length > 0 && list.length === 0 && <p className="empty">Aucun moment ne correspond aux filtres.</p>}
        {list.map(m => (
          <div key={m.id} data-cat={m.category} className={`mom ${m.status} ${open === m.id ? 'open' : ''}`} onClick={() => play(m)} title="Lire depuis le début">
            <div className="mhead">
              <span className="score" title="Score de pertinence proposé par l'IA">{m.score}</span>
              <span className="mtitle">{m.title}</span>
              <span className="macts" onClick={stop}>
                <button className={`ghost acc ${m.status === 'accepted' ? 'on' : ''}`} title={m.status === 'accepted' ? "Retirer l'acceptation" : 'Accepter'} aria-label="Accepter" aria-pressed={m.status === 'accepted'}
                  onClick={() => patch(m, { status: m.status === 'accepted' ? 'pending' : 'accepted' })}><Icon n="check" /></button>
                <button className={`ghost danger ${m.status === 'rejected' ? 'on' : ''}`} title={m.status === 'rejected' ? 'Retirer le rejet' : 'Rejeter'} aria-label="Rejeter" aria-pressed={m.status === 'rejected'}
                  onClick={() => patch(m, { status: m.status === 'rejected' ? 'pending' : 'rejected' })}><Icon n="x" /></button>
                <button className={`ghost fav ${m.favorite ? 'on' : ''}`} title={m.favorite ? 'Retirer des favoris' : 'Mettre en favori'} aria-label="Favori" aria-pressed={m.favorite}
                  onClick={() => patch(m, { favorite: !m.favorite })}><Icon n="star" /></button>
                <button className="ghost" title={open === m.id ? 'Réduire' : 'Détails et ajustement des timecodes'} aria-label="Détails" aria-expanded={open === m.id}
                  onClick={() => setOpen(open === m.id ? null : m.id)}><span style={{ display: 'flex', transform: open === m.id ? 'rotate(90deg)' : undefined }}><Icon n="open" /></span></button>
              </span>
            </div>
            <div className="mmeta">
              <span className="ts" title="Début → fin">{tc(m)}</span>
              <span className="cat">{CATS[m.category] ?? m.category}</span>
              {m.status !== 'pending' && <span className={`badge ${m.status === 'accepted' ? 'done' : 'error'}`}>{DECISION[m.status]}</span>}
              {m.needs_review && <span className="badge running" title="Timecode ou contenu à vérifier">À vérifier</span>}
              {!m.aligned && <span className="badge error" title="Passage introuvable dans le transcript : timecodes à saisir">Non aligné</span>}
              {m.edited && <span className="badge" title="Timecodes ajustés manuellement">Modifié</span>}
            </div>
            <p className="msum">{m.summary}</p>
            {open === m.id && (
              <div className="mbody" onClick={stop}>
                <p><b>Intérêt (avis de l'IA) :</b> {m.justification}</p>
                <blockquote>{m.passage}</blockquote>
                <div className="row">
                  <label>début <TimeField value={m.t_start} onCommit={t => patch(m, { start: t, end: m.t_end ?? t + 10 })} /></label>
                  <label>fin <TimeField value={m.t_end} onCommit={t => patch(m, { end: t, start: m.t_start ?? Math.max(0, t - 10) })} /></label>
                  {m.edited && m.auto_start != null && m.auto_end != null &&
                    <button onClick={() => patch(m, { start: m.auto_start, end: m.auto_end })} title="Revenir aux timecodes proposés par l'IA">Rétablir {fmtTime(m.auto_start)}→{fmtTime(m.auto_end)}</button>}
                  <button onClick={() => m.t_start != null && m.t_end != null && onPlay(m.t_start, m.t_end)} disabled={m.t_start == null}><Icon n="play" size={12} /> Revoir</button>
                </div>
                <textarea placeholder="Note personnelle…" defaultValue={m.note} key={`n${m.id}`}
                  onBlur={e => e.target.value !== m.note && patch(m, { note: e.target.value })} />
              </div>
            )}
          </div>
        ))}
        {shown != null && moments.length === 0 && shownStatus === 'done' && <p className="empty">Aucun moment détecté par ce modèle sur cette VOD.</p>}
      </div>
    </section>
  )
}
