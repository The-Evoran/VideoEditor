import { memo, useCallback, useEffect, useMemo, useRef, useState, type MouseEvent, type RefObject } from 'react'
import { api } from './api'
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

function Controls({ video, box }: { video: RefObject<HTMLVideoElement | null>; box: RefObject<HTMLDivElement | null> }) {
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
  return (
    <div className="controls">
      <input type="range" aria-label="Position" min={0} max={dur || 0} step={0.01} value={Math.min(t, dur || 0)}
        onChange={e => { v().currentTime = +e.target.value }} />
      <div className="row">
        <button onClick={() => (v().paused ? v().play().catch(() => {}) : v().pause())}>{paused ? '▶' : '⏸'}</button>
        <button onClick={() => skip(-STEP)} title={`-${STEP} s`}>−{STEP}s</button>
        <button onClick={() => skip(STEP)} title={`+${STEP} s`}>+{STEP}s</button>
        <span className="clock" title="HH:MM:SS.mmm">{fmtTime(t, true)} / {fmtTime(dur)}</span>
        <span className="spacer" />
        <span title="Volume">🔊</span>
        <input type="range" aria-label="Volume" className="vol" min={0} max={1} step={0.05} value={vol}
          onChange={e => { v().volume = +e.target.value; v().muted = false; setVol(+e.target.value) }} />
        <button onClick={() => (full ? document.exitFullscreen() : box.current?.requestFullscreen())}>{full ? '⤡' : '⛶'}</button>
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

function TranscriptView({ data, video, auto, onSeek }: {
  data: Transcript; video: RefObject<HTMLVideoElement | null>; auto: boolean; onSeek: (t: number) => void
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
      <input type="search" placeholder="Rechercher dans la transcription…" value={q} onChange={e => setQ(e.target.value)} />
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

export default function Watch({ id, videoExists, version }: { id: string; videoExists: boolean; version: string }) {
  const video = useRef<HTMLVideoElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const [err, setErr] = useState('')
  const [auto, setAuto] = useState(true)
  const [data, setData] = useState<Transcript | null>(null)
  const [loadErr, setLoadErr] = useState('')

  useEffect(() => { // rechargé uniquement quand les fichiers changent (version), pas à chaque position
    let alive = true
    api<Transcript>(`/api/projects/${id}/transcript`).then(d => alive && (setData(d), setLoadErr('')), e => alive && setLoadErr(e.message))
    return () => { alive = false }
  }, [id, version])

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
  }, [])

  const hasText = !!data && (data.segments.length > 0 || data.words.length > 0)
  return (
    <section className="watch">
      <div className="player" ref={box}>
        {!videoExists ? <p className="err">Vidéo introuvable (déplacée ou supprimée ?).</p> : <>
          <video ref={video} src={`/api/projects/${id}/video`} preload="metadata"
            onClick={() => { const v = video.current!; if (v.paused) v.play().catch(() => {}); else v.pause() }}
            onError={() => setErr(VIDEO_ERRORS[video.current?.error?.code ?? 4] ?? VIDEO_ERRORS[4])} onLoadedData={() => setErr('')} />
          {err && <p className="err">{err}</p>}
          <Controls video={video} box={box} />
        </>}
      </div>
      <aside className="side">
        <div className="row">
          <label><input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} /> Défilement auto <kbd>A</kbd></label>
          <span className="spacer" />
          <small>Espace lecture · ←/→ ±{STEP}s</small>
        </div>
        {loadErr && <p className="err">{loadErr}</p>}
        {data?.warnings.map(w => <p key={w} className="err">{w}</p>)}
        {data && !hasText && !loadErr && <p className="empty">Aucune transcription disponible.</p>}
        {data && hasText && <TranscriptView key={version} data={data} video={video} auto={auto} onSeek={seek} />}
        {data?.source === 'json' && <small>Segments issus du JSON (SRT absent).</small>}
        {data && data.words.length === 0 && hasText && <small>Pas de JSON : navigation par segments uniquement.</small>}
      </aside>
    </section>
  )
}
