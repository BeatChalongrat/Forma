import test from 'node:test'
import assert from 'node:assert/strict'
import { recoverChatTurn, abortableDelay } from '../src/lib/chatRecovery.ts'

const turn = (status, answer = '') => ({ id: 'turn1', request_id: 'same-request', status, reply: { answer } })
const immediate = async () => {}

test('network recovers by reading same attempt; snapshots replace text and never rerun generation', async () => {
  const states = [], snapshots = [], answers = [new TypeError('offline'), turn('pending', 'hello'), turn('pending', 'hello world'), turn('complete', 'hello world!')]
  let reads = 0
  const result = await recoverChatTurn(async () => { reads++; const a = answers.shift(); if (a instanceof Error) throw a; return a }, new AbortController().signal, t => snapshots.push(t.reply.answer), s => states.push(s), immediate)
  assert.equal(result.status, 'complete')
  assert.equal(reads, 4)
  assert.deepEqual(snapshots, ['hello', 'hello world', 'hello world!'])
  assert.deepEqual(states, ['reconnecting', 'following', 'following'])
})

test('a lost acknowledgement waits for acceptance, without submitting another request', async () => {
  let reads = 0
  const result = await recoverChatTurn(async () => { if (++reads < 3) throw {status:404}; return turn('complete') }, new AbortController().signal, () => {}, () => {}, immediate)
  assert.equal(result.status, 'complete')
  assert.equal(reads, 3)
})

test('missing request and lost access stop recovery rather than retrying forever', async () => {
  for (const [status, attempts] of [[404,8], [401,1], [403,1]]) {
    let reads = 0
    await assert.rejects(recoverChatTurn(async () => { reads++; throw {status} }, new AbortController().signal, () => {}, () => {}, immediate))
    assert.equal(reads, attempts)
  }
})

test('provider failure and user stop remain terminal incomplete results', async () => {
  for (const status of ['failed', 'stopped']) {
    const result = await recoverChatTurn(async () => turn(status, 'partial'), new AbortController().signal, () => {}, () => {}, immediate)
    assert.equal(result.status, status)
    assert.equal(result.reply.answer, 'partial')
  }
})

test('leaving view aborts observation, including a pending reconnect delay', async () => {
  const c = new AbortController()
  const waiting = recoverChatTurn(async () => turn('pending'), c.signal, () => {}, () => c.abort())
  await assert.rejects(waiting, {name:'AbortError'})
  await assert.rejects(abortableDelay(10000, c.signal), {name:'AbortError'})
})
