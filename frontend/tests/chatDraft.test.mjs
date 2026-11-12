import { test } from 'vitest'
import assert from 'node:assert/strict'
import { resolveChatDraft } from '../src/lib/chatDraftState.ts'

test('reload after commit but before acknowledgement is not a false cross-tab conflict', () => {
  const saved = { revision: 2, draft: { clarifications: {}, input: '', attachment_ids: [], unuploaded: [] } }
  const local = { revision: 1, dirty: true, draft: { input: '', unuploaded: [], attachment_ids: [] } }
  assert.deepEqual(resolveChatDraft(saved, local), { draft: saved.draft, conflict: false, acknowledged: true })
})

test('a real competing edit remains visible and cannot silently replace the saved draft', () => {
  const local = { revision: 1, dirty: true, draft: { input: 'My unsent text', clarifications: { t: { step: 0, answers: { 0: 'No' }, custom: {} } } } }
  const result = resolveChatDraft({ revision: 2, draft: { input: 'Other tab' } }, local)
  assert.equal(result.conflict, true)
  assert.deepEqual(result.draft, local.draft)
  assert.equal(resolveChatDraft({ revision: 1, draft: {} }, local).conflict, false)
})

test('an acknowledged local copy cannot resurrect an old question or lose server attachments', () => {
  const draft = { input: 'Next', attachment_ids: ['file-2'], pending_chat_id: 'chat-1' }
  assert.deepEqual(resolveChatDraft({ revision: 5, draft }, { revision: 3, dirty: false, draft: { input: 'Old' } }).draft, draft)
})
