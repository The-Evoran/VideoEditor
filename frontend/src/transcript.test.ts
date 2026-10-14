import test from 'node:test'
import assert from 'node:assert/strict'
import { activeWord, findLast, fmtTime, groupWords, search, type Word } from './transcript.ts'

const words: Word[] = [[1, 2, 'a'], [2, 3, 'b'], [10, 11, 'c']]
const starts = words.map(w => w[0])

test('findLast', () => {
  assert.equal(findLast(starts, 0.5), -1)
  assert.equal(findLast(starts, 2), 1)
  assert.equal(findLast(starts, 99), 2)
})

test('activeWord: silence et bornes', () => {
  assert.equal(activeWord(starts, words, 0.5), -1)
  assert.equal(activeWord(starts, words, 2.5), 1)
  assert.equal(activeWord(starts, words, 3.2), 1) // tolérance
  assert.equal(activeWord(starts, words, 6), -1) // silence
  assert.equal(activeWord(starts, words, 10.5), 2)
})

test('groupWords', () => {
  const segs = [{ start: 2, end: 3, text: '' }, { start: 10, end: 11, text: '' }]
  assert.deepEqual(groupWords(segs, words), [[0, 2], [2, 3]])
})

test('fmtTime', () => {
  assert.equal(fmtTime(3725.5), '01:02:05')
  assert.equal(fmtTime(3725.5, true), '01:02:05.500')
  assert.equal(fmtTime(-3), '00:00:00')
})

test('search: accents, casse, plusieurs occurrences, vide', () => {
  const segs = [{ start: 0, end: 1, text: 'Le cœur, le COEUR' }, { start: 1, end: 2, text: "L'hôtel de Léa" }]
  assert.equal(search(segs, 'le c').length, 2)
  assert.deepEqual(search(segs, 'hotel'), [{ seg: 1, at: 2, len: 5 }])
  assert.equal(search(segs, 'léa')[0].seg, 1)
  assert.equal(search(segs, '  ').length, 0)
  assert.equal(search(segs, 'zzz').length, 0)
})
