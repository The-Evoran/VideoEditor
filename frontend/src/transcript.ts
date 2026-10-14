export type Seg = { start: number; end: number; text: string }
export type Word = [start: number, end: number, text: string]
export type Transcript = { segments: Seg[]; words: Word[]; source: 'srt' | 'json' | null; warnings: string[] }

/** Index du dernier élément dont la valeur est <= t (-1 si aucun). Recherche dichotomique. */
export function findLast(starts: ArrayLike<number>, t: number): number {
  let lo = 0, hi = starts.length - 1, r = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (starts[mid] <= t) { r = mid; lo = mid + 1 } else hi = mid - 1
  }
  return r
}

/** Mot actif à t : dernier mot commencé, sauf si t tombe dans un silence de plus de `grace` s après sa fin. */
export function activeWord(wordStarts: ArrayLike<number>, words: Word[], t: number, grace = 0.4): number {
  const i = findLast(wordStarts, t)
  return i >= 0 && t <= words[i][1] + grace ? i : -1
}

/** Plage [from, to) de mots pour chaque segment, selon le début du mot ; les mots avant le 1er segment vont au 1er. */
export function groupWords(segs: Seg[], words: Word[]): [number, number][] {
  const out: [number, number][] = []
  let w = 0
  for (let s = 0; s < segs.length; s++) {
    const next = s + 1 < segs.length ? segs[s + 1].start : Infinity
    const from = w
    while (w < words.length && words[w][0] < next) w++
    out.push([from, w])
  }
  return out
}

export const fmtTime = (t: number, ms = false) => {
  const x = Math.max(0, t)
  const h = Math.floor(x / 3600), m = Math.floor((x % 3600) / 60), s = Math.floor(x % 60)
  const base = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return ms ? `${base}.${String(Math.floor((x % 1) * 1000)).padStart(3, '0')}` : base
}

export const norm = (s: string) => s.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase()

export type Hit = { seg: number; at: number; len: number }

/** Occurrences de `query` (insensible à la casse et aux accents) dans le texte des segments. Ne modifie rien. */
export function search(segs: Seg[], query: string, max = 500): Hit[] {
  const q = norm(query.trim()).replace(/\s+/g, ' ')
  if (!q) return []
  const hits: Hit[] = []
  for (let i = 0; i < segs.length && hits.length < max; i++) {
    const t = norm(segs[i].text)
    for (let at = t.indexOf(q); at >= 0 && hits.length < max; at = t.indexOf(q, at + q.length)) {
      hits.push({ seg: i, at, len: q.length })
    }
  }
  return hits
}

/** « HH:MM:SS.mmm », « MM:SS » ou secondes brutes -> secondes ; null si invalide. */
export function parseTime(s: string): number | null {
  const t = s.trim().replace(',', '.')
  if (!/^\d+(\.\d+)?$|^(\d+:){1,2}\d+(\.\d+)?$/.test(t)) return null
  return t.split(':').reduce((acc, x) => acc * 60 + parseFloat(x), 0)
}
