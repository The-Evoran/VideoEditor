const P: Record<string, string> = {
  play: 'M7 4.5v15l12-7.5z',
  pause: 'M7 5h4v14H7zM13 5h4v14h-4z',
  back: 'M15 5l-7 7 7 7',
  open: 'M9 5l7 7-7 7',
  vol: 'M4 9v6h4l5 4V5L8 9zM16 9a4 4 0 010 6',
  full: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5',
  exit: 'M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  x: 'M6 6l12 12M18 6L6 18',
  star: 'M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.9-5.2-2.8-5.2 2.8 1-5.9-4.3-4.1 5.9-.8z',
  search: 'M11 4a7 7 0 100 14 7 7 0 000-14zM20 20l-4-4',
  trash: 'M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13',
  spark: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z',
  refresh: 'M20 11a8 8 0 10-2 6M20 5v6h-6',
  gear: 'M12 9a3 3 0 100 6 3 3 0 000-6zM4 12h2M18 12h2M12 4v2M12 18v2',
}

export default function Icon({ n, size = 16 }: { n: keyof typeof P; size?: number }) {
  const fill = n === 'play' || n === 'pause'
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" fill={fill ? 'currentColor' : 'none'}
      stroke={fill ? 'none' : 'currentColor'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d={P[n]} />
    </svg>
  )
}
