import { test } from 'vitest'
import assert from 'node:assert/strict'
import { assistantPresentation, coverageLabel, safeSourceLink } from '../src/lib/assistantPresentation.ts'

test('full aggregate coverage stays distinct from paginated and shortened detail', () => {
  const text = coverageLabel({ overview: { total: 177, coverage: { complete: true } }, coverage: { matched: 177, returned: 20, complete: false }, truncated: true }, 'th')
  assert.match(text, /ภาพรวมนับครบ 177/)
  assert.match(text, /20 จาก 177/)
  assert.match(text, /ยังไม่ครบ/)
  assert.match(text, /ถูกย่อ/)
  assert.equal(coverageLabel(null, 'en'), '')
  assert.match(coverageLabel({ report: { coverage: { matched: 300, scanned: 300, complete: true } } }, 'th'), /นับ 300 จาก 300.*ครบ/)
})

const file = (id, name = 'chart.png', kind = 'output') => ({ id, name, kind, media_type: 'image/png', size: 100 })
const run = (id, files, status = 'ok') => ({ id, files, status, code: 'private execution receipt', omitted: [] })
const reply = (extra = {}) => ({ answer: 'Here is the chart.', ai: true, evidence: [], ...extra })

test('two attempts of the same graph produce one canonical output, not two images plus a work card', () => {
  const a = file('first'), b = file('final')
  const data = reply({ code_runs: [run('1', [a]), run('2', [b])], artifacts: [{ id: 'art', kind: 'image', version: 1, title: 'chart', file: b }] })
  const original = JSON.stringify(data)
  const out = assistantPresentation(data)
  assert.equal(out.artifacts.length, 1)
  assert.deepEqual(out.files, [])
  assert.equal(out.artifacts[0].file.id, 'final')
  assert.equal(JSON.stringify(data), original, 'history and evidence must not be removed')
})
test('files remain accessible when artifact registration failed or the old API lacks file references', () => {
  const out = assistantPresentation(reply({ code_runs: [run('1', [file('old')]), run('2', [file('new')])] }))
  assert.deepEqual(out.files.map(f => f.id), ['new'])
  const legacy = assistantPresentation(reply({ artifacts: [{ id: 'a', version: 1, kind: 'image' }], code_runs: [run('1', [file('new')])] }))
  assert.equal(legacy.files.length, 1, 'do not guess links from display titles')
})
test('unrelated outputs are not lost and failed or preview files are not promoted as final deliverables', () => {
  const out = assistantPresentation(reply({ code_runs: [run('1', [file('a'), file('b', 'table.csv'), file('preview', 'page.png', 'preview')]), run('2', [file('broken')], 'error')] }))
  assert.deepEqual(out.files.map(f => f.id), ['a', 'b'])
  assert.equal(out.lastRunFailed, true)
})
test('same attachment referenced twice is shown once; successful retry clears last failure', () => {
  const f = file('same')
  const out = assistantPresentation(reply({ code_runs: [run('fail', [], 'timeout'), run('ok', [f, { ...f, name: 'alias.png' }])] }))
  assert.equal(out.files.length, 1)
  assert.equal(out.lastRunFailed, false)
})
test('latest artifact version within a turn is primary while different works and reports are retained', () => {
  const out = assistantPresentation(reply({ artifacts: [{ id: 'a', version: 1 }, { id: 'a', version: 3 }, { id: 'b', version: 1 }], visualizations: [{ id: 'r1' }, { id: 'r1' }, { id: 'r2' }] }))
  assert.deepEqual(out.artifacts.map(a => [a.id, a.version]), [['a', 3], ['b', 1]])
  assert.deepEqual(out.reports.map(r => r.id), ['r1', 'r2'])
})
test('output selection never deduplicates different files by shared chart title or different turns', () => {
  const a = assistantPresentation(reply({ code_runs: [run('1', [file('a', 'jan.png'), file('b', 'feb.png')])] }))
  const b = assistantPresentation(reply({ code_runs: [run('2', [file('c', 'jan.png')])] }))
  assert.equal(a.files.length, 2)
  assert.equal(b.files.length, 1)
})
test('source navigation keeps distinct internal links and rejects external or malformed destinations', () => {
  for (const url of ['//evil.test', '/\\evil.test', 'https://evil.test', '/\n/evil.test']) assert.equal(safeSourceLink(url), false)
  const out = assistantPresentation(reply({ evidence: [{ links: [{ url: '/imports', label: 'A' }, { url: '/imports', label: 'A' }, { url: '//evil.test', label: 'bad' }] }] }))
  assert.equal(out.links.length, 1)
  assert.equal(safeSourceLink('/assistant?chat=one'), true)
})
