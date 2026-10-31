import { test } from 'node:test'
import assert from 'node:assert/strict'
import { pageIdentity, recordVisit } from '../src/lib/navigation.ts'

const visit = (index, url, key = String(index)) => ({ index, key, url, scroll: 0 })

test('review details, chat conversations and history tabs are separate views', () => {
  assert.notEqual(pageIdentity('/review?round=r'), pageIdentity('/review?mapping=m&round=r'))
  assert.notEqual(pageIdentity('/assistant?chat=a'), pageIdentity('/assistant?chat=b'))
  assert.notEqual(pageIdentity('/imports'), pageIdentity('/imports?view=files'))
  assert.equal(pageIdentity('/review?mapping=m'), pageIdentity('/review?mapping=m&step=2'))
})

test('browser back/forward retains history; a new branch drops forward entries', () => {
  const history = [visit(0, '/imports'), visit(1, '/imports/new'), visit(2, '/imports/new/single')]
  const back = recordVisit(history, history[1], 'POP')
  assert.equal(back.length, 3)
  assert.equal(recordVisit(back, history[2], 'POP').length, 3)
  assert.deepEqual(recordVisit(back, visit(2, '/customers', 'new'), 'PUSH').map(v => v.url), ['/imports', '/imports/new', '/customers'])
})

test('replace updates the current location and retains its parent', () => {
  const current = visit(1, '/imports/new?batch=b', 'replacement')
  const history = recordVisit([visit(0, '/uploads/b'), visit(1, '/imports/new')], current, 'REPLACE')
  assert.deepEqual(history.map(v => v.url), ['/uploads/b', '/imports/new?batch=b'])
})

test('an unknown history entry starts again instead of trusting stale history', () => {
  const current = visit(2, '/imports/new/single', 'new-tab')
  assert.deepEqual(recordVisit([visit(0, '/customers'), visit(2, '/ops', 'old')], current, 'POP'), [current])
})
