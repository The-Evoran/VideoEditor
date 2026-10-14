import { memo, useCallback, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode, type RefObject } from 'react'
import { api } from './api'
import Icon from './Icon'
import { usePref } from './usePref'
import Moments, { type Moment } from './Moments'
import { activeWord, findLast, fmtTime, groupWords, norm, search, type Hit, type Transcript, type Word } from './transcript'

const STEP = 5 // secondes, flèches gauche/droite

/** Appelle cb(t) à chaque image pendant la lecture, et sur les événements qui changent la position à l'arrêt. */
function useClock(video: RefObject<HTMLVideoElement | null>, cb: (t: number) => void) {
  const ref = useRef(cb)
  useEffect(() => { ref.current = cb })
  useEffect(() => {
    const v = video.current
    if (!v) return
    let raf = 0
    const tick = () => { ref.current(v.currentTime); raf = v.paused ? 0 : requestAnimationFrame(tick) }
    const kick = () => { cancelAnimationFrame(raf); tick() }
    const evs = ['play', 'playing', 'pause', 'seeked', 'seeking', 'timeupdate', 'loadedmetadata']
    evs.forEach(e => v.addEventListener(e, kick))
    kick()
    return () => { cancelAnimationFrame(raf); evs.forEach(e => v.removeEventListener(e, kick)) }
  }, [video])
}

function Controls({ video, box, moments }: { video: RefObject<HTMLVideoElement | null>; box: RefObject<HTMLDivElement | null>; moments: Moment[] }) {
  const [t, setT] = useState(0)
  const [dur, setDur] = useState(0)
  const [paused, setPaused] = useState(true)
  const [vol, setVol] = useState(1)
  const [full, setFull] = useState(false)
  const last = useRef(-1)

  useClock(video, cur => {
    const v = video.current!
    if (Math.abs(cur - last.current) >= 0.05 || v.paused) { last.current = cur; setT(cur) }
    setDur(Number.isFinite(v.duration) ? v.duration : 0)
    setPaused(v.paused)
  })
  useEffect(() => {
    const on = () => setFull(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', on)
    return () => document.removeEventListener('fullscreenchange', on)
  }, [])

  const v = () => video.current!
  const skip = (d: number) => { v().currentTime = Math.min(Math.max(0, v().currentTime + d), v().duration || Infinity) }
  const CAT_COLOR: Record<string, string> = {
    topic: '#6ea8fe', argument: '#a78bfa', punchline: '#e6b450', controversy: '#e5675f', announcement: '#52b788', standalone: '#4cc3c3', review: '#8b8f98',
  }
  return (
    <div className="controls">
      <input type="range" aria-label="Position" min={0} max={dur || 0} step={0.01} value={Math.min(t, dur || 0)}
        onChange={e => { v().currentTime = +e.target.value }} />
      {dur > 0 && moments.length > 0 && (
        <div className="marks">
          {moments.filter(m => m.t_start != null && m.t_end != null).map(m => (
            <button key={m.id} className={m.status === 'rejected' ? 'rej' : ''} title={`${m.title} · ${fmtTime(m.t_start!)}`}
              aria-label={`Moment : ${m.title}`} onClick={() => { v().currentTime = m.t_start! }}
              style={{ left: `${(m.t_start! / dur) * 100}%`, width: `${Math.max(((m.t_end! - m.t_start!) / dur) * 100, 0.3)}%`, ['--c' as string]: CAT_COLOR[m.category] }} />
          ))}
        </div>
      )}
      <div className="row">
        <button className="ghost" onClick={() => (v().paused ? v().play().catch(() => {}) : v().pause())} title={paused ? 'Lecture (Espace)' : 'Pause (Espace)'}
          aria-label={paused ? 'Lecture' : 'Pause'}><Icon n={paused ? 'play' : 'pause'} /></button>
        <button className="ghost" onClick={() => skip(-STEP)} title={`−${STEP} s (←)`}>−{STEP}s</button>
        <button className="ghost" onClick={() => skip(STEP)} title={`+${STEP} s (→)`}>+{STEP}s</button>
        <span className="clock" title="Position / durée"><b>{fmtTime(t, true)}</b> / {fmtTime(dur)}</span>
        <span className="spacer" />
        <span title="Volume" style={{ color: 'var(--muted)', display: 'flex' }}><Icon n="vol" /></span>
        <input type="range" aria-label="Volume" className="vol" min={0} max={1} step={0.05} value={vol}
          onChange={e => { v().volume = +e.target.value; v().muted = false; setVol(+e.target.value) }} />
        <button className="ghost" onClick={() => (full ? document.exitFullscreen() : box.current?.requestFullscreen())}
          title={full ? 'Quitter le plein écran' : 'Plein écran'} aria-label="Plein écran"><Icon n={full ? 'exit' : 'full'} /></button>
      </div>
    </div>
  )
}

type RowProps = { i: number; text: string; start: number; words: Word[]; from: number }
// mémoïsé : une ligne n'est jamais re-rendue, l'état actif passe par classList (voir Transcript)
const Row = memo(function Row({ i, text, start, words, from }: RowProps) {
  return (
    <div className="seg" data-s={i}>
      <span className="ts">{fmtTime(start)}</span>
      <span className="txt">
        {words.length === 0 ? text : words.map((w, k) => <span key={k} data-w={from + k}>{w[2]} </span>)}
      </span>
    </div>
  )
})

type Mark = { start: number; end: number } | null

function TranscriptView({ data, video, auto, mark, onSeek, extra }: {
  data: Transcript; video: RefObject<HTMLVideoElement | null>; auto: boolean; mark: Mark; onSeek: (t: number) => void; extra: ReactNode
}) {
  const { segments: segs, words } = data
  const box = useRef<HTMLDivElement>(null)
  const autoRef = useRef(auto)
  useEffect(() => { autoRef.current = auto })
  const segStarts = useMemo(() => segs.map(s => s.start), [segs])
  const wordStarts = useMemo(() => words.map(w => w[0]), [words])
  const ranges = useMemo(() => groupWords(segs, words), [segs, words])
  const rows = useMemo(
    () => segs.map((s, i) => <Row key={i} i={i} text={s.text} start={s.start} words={words.slice(...ranges[i])} from={ranges[i][0]} />),
    [segs, words, ranges])
  const els = useRef<{ s: HTMLElement[]; w: HTMLElement[] }>({ s: [], w: [] })
  const cur = useRef({ s: -1, w: -1, hit: -1 })
  const [q, setQ] = useState('')
  const hits = useMemo(() => search(segs, q), [segs, q])

  useEffect(() => { // index des éléments DOM, construit une fois par transcript
    const b = box.current!
    els.current = { s: [...b.querySelectorAll<HTMLElement>('[data-s]')], w: [...b.querySelectorAll<HTMLElement>('[data-w]')] }
    cur.current = { s: -1, w: -1, hit: -1 }
  }, [segs, words])

  useEffect(() => { // passage d'un moment détecté : segments chevauchant [start, end]
    box.current!.querySelectorAll('.seg.mm').forEach(el => el.classList.remove('mm'))
    if (!mark) return
    const a = Math.max(0, findLast(segStarts, mark.start)), b = findLast(segStarts, mark.end - 0.01)
    for (let i = a; i <= b; i++) els.current.s[i]?.classList.add('mm')
    const el = els.current.s[a], bx = box.current!
    if (el) bx.scrollTo({ top: el.offsetTop - bx.clientHeight / 3 })
  }, [mark, segStarts, segs])

  useClock(video, t => {
    const c = cur.current, e = els.current
    const si = findLast(segStarts, t)
    if (si !== c.s) {
      e.s[c.s]?.classList.remove('active')
      e.s[si]?.classList.add('active')
      c.s = si
      const el = e.s[si], b = box.current
      if (el && b && autoRef.current) b.scrollTo({ top: el.offsetTop - b.clientHeight / 3 })
    }
    const wi = activeWord(wordStarts, words, t)
    if (wi !== c.w) { e.w[c.w]?.classList.remove('active'); e.w[wi]?.classList.add('active'); c.w = wi }
  })

  const goHit = (h: Hit) => {
    const c = cur.current, e = els.current
    e.s[c.hit]?.classList.remove('hit')
    e.s[h.seg]?.classList.add('hit')
    c.hit = h.seg
    onSeek(segs[h.seg].start)
  }
  const click = (ev: MouseEvent) => {
    const t = ev.target as HTMLElement
    const w = t.closest<HTMLElement>('[data-w]')
    if (w) return onSeek(words[+w.dataset.w!][0])
    const s = t.closest<HTMLElement>('[data-s]')
    if (s) onSeek(segs[+s.dataset.s!].start)
  }

  return (
    <div className="transcript">
      <div className="panel-head" style={{ flexWrap: 'nowrap' }}>
        <span className="search">
          <Icon n="search" size={14} />
          <input type="search" aria-label="Rechercher dans la transcription" placeholder="Rechercher dans la transcription…" value={q} onChange={e => setQ(e.target.value)} />
        </span>
        {extra}
      </div>
      {q.trim() && (
        <div className="results">
          {hits.length === 0 ? <p className="empty">Aucun résultat pour « {q.trim()} ».</p> : <>
            <div className="meta">{hits.length}{hits.length >= 500 ? '+' : ''} résultat(s)</div>
            {hits.map((h, k) => {
              const s = segs[h.seg]
              // positions calculées sur le texte normalisé : valables si la normalisation ne change pas la longueur
              const ok = norm(s.text).length === s.text.length
              return (
                <button key={k} className="hit-row" onClick={() => goHit(h)}>
                  <span className="ts">{fmtTime(s.start)}</span>
                  {ok ? <span>{s.text.slice(0, h.at)}<mark>{s.text.slice(h.at, h.at + h.len)}</mark>{s.text.slice(h.at + h.len)}</span> : s.text}
                </button>
              )
            })}
          </>}
        </div>
      )}
      <div className="segs" ref={box} onClick={click}>{rows}</div>
    </div>
  )
}

const VIDEO_ERRORS: Record<number, string> = {
  1: 'Chargement interrompu.',
  2: 'Erreur réseau pendant le chargement de la vidéo.',
  3: 'Vidéo corrompue ou non décodable.',
  4: 'Format non pris en charge par le navigateur (MP4 H.264/AAC ou WebM recommandés), ou fichier introuvable.',
}

export default function Watch({ id, videoExists, version, slot }: { id: string; videoExists: boolean; version: string; slot: HTMLElement | null }) {
  const video = useRef<HTMLVideoElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const [err, setErr] = useState('')
  const [auto, setAuto] = usePref('vod.auto', true)
  const [moments, setMoments] = useState<Moment[]>([])
  const [data, setData] = useState<Transcript | null>(null)
  const [loadErr, setLoadErr] = useState('')

  useEffect(() => { // rechargé uniquement quand les fichiers changent (version), pas à chaque position
    let alive = true
    api<Transcript>(`/api/projects/${id}/transcript`).then(d => alive && (setData(d), setLoadErr('')), e => alive && setLoadErr(e.message))
    return () => { alive = false }
  }, [id, version])

  const [mark, setMark] = useState<Mark>(null)
  const playRange = useCallback((start: number, end: number | null) => {
    const v = video.current
    if (!v) return
    v.currentTime = start
    v.play().catch(() => {})
    setMark(end == null ? null : { start, end })
  }, [])
  const seek = useCallback((t: number) => { if (video.current) video.current.currentTime = t }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (e.repeat || e.ctrlKey || e.metaKey || e.altKey || el.closest('input:not([type=range]), textarea, select, [contenteditable]')) return
      const v = video.current
      if (!v) return
      if (e.code === 'Space') { if (v.paused) v.play().catch(() => {}); else v.pause() }
      else if (e.key === 'ArrowLeft') v.currentTime = Math.max(0, v.currentTime - STEP)
      else if (e.key === 'ArrowRight') v.currentTime = Math.min(v.duration || Infinity, v.currentTime + STEP)
      else if (e.key.toLowerCase() === 'a') setAuto(a => !a)
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setAuto])

  const hasText = !!data && (data.segments.length > 0 || data.words.length > 0)
  const autoBox = (
    <label title="Suivre la lecture dans le transcript (A)">
      <input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} /> Suivi auto <kbd>A</kbd>
    </label>
  )
  return (
    <section className="watch">
      <div className="player" ref={box}>
        {!videoExists ? <p className="err">Vidéo introuvable (déplacée ou supprimée ?).</p> : <>
          <video ref={video} src={`/api/projects/${id}/video`} preload="metadata"
            onClick={() => { const v = video.current!; if (v.paused) v.play().catch(() => {}); else v.pause() }}
            onError={() => setErr(VIDEO_ERRORS[video.current?.error?.code ?? 4] ?? VIDEO_ERRORS[4])} onLoadedData={() => setErr('')} />
          {err && <p className="err">{err}</p>}
          <Controls video={video} box={box} moments={moments} />
        </>}
      </div>
      <section className="panel" aria-label="Transcript">
        {!data && !loadErr && <p className="empty">Chargement du transcript…</p>}
        {loadErr && <p className="err">{loadErr}</p>}
        {data?.warnings.map(w => <p key={w} className="err">{w}</p>)}
        {data && !hasText && !loadErr && <p className="empty">Aucune transcription disponible.</p>}
        {data && hasText && <TranscriptView key={version} data={data} video={video} auto={auto} mark={mark} onSeek={seek} extra={autoBox} />}
        {data && hasText && (data.source === 'json' || data.words.length === 0) && (
          <div className="panel-foot">
            {data.source === 'json' && <small>Segments issus du JSON (SRT absent).</small>}
            {data.words.length === 0 && <small>Pas de JSON : navigation par segments uniquement.</small>}
          </div>
        )}
      </section>
      <Moments pid={id} canAnalyse={hasText} onPlay={playRange} onMoments={setMoments} slot={slot} />
    </section>
  )
}
