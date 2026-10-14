import { useEffect, useState } from 'react'
import './App.css'
import Library from './Library'
import Detail from './Detail'

export default function App() {
  const [hash, setHash] = useState(location.hash)
  useEffect(() => {
    const on = () => setHash(location.hash)
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  const id = /^#\/projects\/([\w-]+)$/.exec(hash)?.[1]
  return <main>{id ? <Detail key={id} id={id} /> : <Library />}</main>
}
