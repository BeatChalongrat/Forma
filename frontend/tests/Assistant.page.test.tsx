// @vitest-environment jsdom
import { deferred, me, mountPage, stub } from './pageHarness'
import { beforeEach, expect, test, vi } from 'vitest'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { AssistantPage } from '../src/components/Assistant'
import { api, type ChatTurn } from '../src/lib/api'

const conversation = { id: 'chat-1', title: 'Customer files', round_id: null, dataset_id: null, archived: false, pinned: false, busy: false }
const reply = { answer: 'There are 12 verified records.', ai: true, evidence: [], actions: [], artifacts: [], code_runs: [] }
const turn: ChatTurn = { id: 'turn-1', request_id: 'request-1', question: 'How many records?', sequence: 1, reply, status: 'complete', created_at: '2026-11-12T10:00:00Z' }
beforeEach(() => {
  stub('me', me)
  stub('assistantStatus', { enabled: true, model: 'mock', provider: 'test', vision: false, code_sandbox: true, file_types: ['.csv', '.xlsx'] })
  stub('chat', { conversation, turns: [], total: 0, older_before: null })
  stub('chatState', { revision: 0, draft: {}, branch: {}, seen_sequence: 0 })
  stub('saveChatState', {}).mockImplementation(async (_id, revision, draft) => ({ revision: revision + 1, draft, branch: {}, seen_sequence: 0 }))
  stub('chatSeen', {})
  stub('documentJobs', { items: [], total: 0 })
})
function mount(url = '/assistant?chat=chat-1') { return mountPage(<AssistantPage />, '/assistant', url) }
async function composer(user: ReturnType<typeof mount>['user'], text = 'How many records?') {
  const input = await screen.findByRole('textbox', { name: 'Your question' })
  await waitFor(() => expect((input as HTMLTextAreaElement).disabled).toBe(false))
  await user.type(input, text)
  return input
}

test('stream renders live text and reconnect status, keeps a newer draft, and completes without resending', async () => {
  const completion = deferred<ChatTurn>()
  let event!: Parameters<typeof api.chatStream>[2]
  stub('chatStream', {}).mockImplementation((_id, _body, emit) => { event = emit; return completion.promise })
  const { user } = mount()
  await composer(user)
  await user.click(screen.getByRole('button', { name: 'Send question' }))
  await waitFor(() => expect(api.chatStream).toHaveBeenCalledTimes(1))
  await act(async () => { event({ type: 'started' } as never); event({ type: 'delta', text: '12 records so far' }) })
  expect(screen.getByText('12 records so far')).toBeTruthy()
  await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'Next question')
  await act(async () => { event({ type: 'connection', state: 'reconnecting' }) })
  expect(screen.getByText('Connection interrupted. Reconnecting to the saved response…')).toBeTruthy()
  await act(async () => { event({ type: 'connection', state: 'following' }); event({ type: 'reset' }) })
  expect(screen.queryByText('12 records so far')).toBeNull()
  vi.mocked(api.chat).mockResolvedValue({ conversation, turns: [turn], total: 1, older_before: null } as never)
  await act(async () => { completion.resolve(turn) })
  await screen.findByText(reply.answer)
  expect((screen.getByRole('textbox', { name: 'Your question' }) as HTMLTextAreaElement).value).toBe('Next question')
  expect(api.chatStream).toHaveBeenCalledTimes(1)
  expect(screen.queryByText('Connection interrupted. Reconnecting to the saved response…')).toBeNull()
})

test('leaving detaches the stream without cancelling the server job; reopening follows the saved turn', async () => {
  let signal!: AbortSignal
  stub('chatStream', {}).mockImplementation((_id, _body, _emit, abort) => { signal = abort; return new Promise<ChatTurn>(() => {}) })
  const stop = stub('chatStop', {})
  const first = mount()
  await composer(first.user)
  await first.user.click(screen.getByRole('button', { name: 'Send question' }))
  await waitFor(() => expect(api.chatStream).toHaveBeenCalledTimes(1))
  first.unmount()
  expect(signal.aborted).toBe(true)
  expect(stop).not.toHaveBeenCalled()
  const pending = { ...turn, status: 'pending', reply: { ...reply, answer: 'Recovered partial answer' } }
  vi.mocked(api.chat).mockResolvedValue({ conversation: { ...conversation, busy: true }, turns: [pending], total: 1, older_before: null } as never)
  mount()
  await screen.findByText('Recovered partial answer')
  expect(screen.getByText('Still working in the background. You can leave this page and return.')).toBeTruthy()
  expect(api.chatStream).toHaveBeenCalledTimes(1)
})

test('Stop targets the existing request; stop failure is visible and can retry without a new question', async () => {
  const pending = { ...turn, status: 'pending', reply: null }
  vi.mocked(api.chat).mockResolvedValue({ conversation: { ...conversation, busy: true }, turns: [pending], total: 1, older_before: null } as never)
  const stopped = { ...turn, status: 'stopped' as const }
  stub('chatStream', {})
  stub('chatStop', stopped).mockRejectedValueOnce(new Error('Offline'))
  const { user } = mount()
  await user.click(await screen.findByRole('button', { name: 'Stop response' }))
  await screen.findByText('Could not stop yet. Try Stop again.')
  vi.mocked(api.chat).mockResolvedValue({ conversation, turns: [stopped], total: 1, older_before: null } as never)
  await user.click(screen.getByRole('button', { name: 'Stop response' }))
  await screen.findByText('Stopped. This reply may be incomplete. You can send another question.')
  expect(api.chatStop).toHaveBeenNthCalledWith(1, 'chat-1', 'request-1')
  expect(api.chatStop).toHaveBeenNthCalledWith(2, 'chat-1', 'request-1')
  expect(api.chatStream).not.toHaveBeenCalled()
})

test('failed transport retry reuses the original request id instead of making duplicate work', async () => {
  const completion = deferred<ChatTurn>()
  stub('chatStream', {}).mockRejectedValueOnce(new Error('Offline')).mockImplementationOnce(() => completion.promise)
  const { user } = mount()
  await composer(user)
  await user.click(screen.getByRole('button', { name: 'Send question' }))
  await screen.findByText('Connection interrupted. The question may still be processing.')
  const first = vi.mocked(api.chatStream).mock.calls[0][1]
  await user.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(api.chatStream).toHaveBeenCalledTimes(2))
  expect(vi.mocked(api.chatStream).mock.calls[1][1]).toEqual(first)
  vi.mocked(api.chat).mockResolvedValue({ conversation, turns: [turn], total: 1, older_before: null } as never)
  await act(async () => { completion.resolve(turn) })
})

test('archived chat prevents sending until explicitly restored', async () => {
  vi.mocked(api.chat).mockResolvedValue({ conversation: { ...conversation, archived: true }, turns: [turn], total: 1, older_before: null } as never)
  const stream = stub('chatStream', {})
  const restore = stub('editChat', {})
  const { user } = mount()
  await screen.findByText(reply.answer)
  expect((screen.getByRole('textbox', { name: 'Your question' }) as HTMLTextAreaElement).disabled).toBe(true)
  await user.click(screen.getByRole('button', { name: 'Restore to continue' }))
  expect(restore).toHaveBeenCalledExactlyOnceWith('chat-1', { archived: false })
  expect(stream).not.toHaveBeenCalled()
})

test('unsupported attachment is rejected before upload; failed supported upload retains the question and file', async () => {
  const attach = stub('attachChatFile', {}).mockRejectedValue(new Error('Upload offline'))
  const stream = stub('chatStream', {})
  const { container, user } = mount()
  await composer(user, 'Read this workbook')
  const input = container.querySelector('input[type=file]')!
  fireEvent.change(input, { target: { files: [new File(['image'], 'photo.png', { type: 'image/png' })] } })
  await screen.findByText(/this model cannot read images/)
  expect(attach).not.toHaveBeenCalled()
  fireEvent.change(input, { target: { files: [new File(['sku\nA'], 'items.csv', { type: 'text/csv' })] } })
  await user.click(screen.getByRole('button', { name: 'Send question' }))
  await screen.findByText('Upload offline')
  expect((screen.getByRole('textbox', { name: 'Your question' }) as HTMLTextAreaElement).value).toBe('Read this workbook')
  expect(screen.getByText('items.csv')).toBeTruthy()
  expect(stream).not.toHaveBeenCalled()
})

test('editing a question creates an alternative, preserving the original conversation', async () => {
  vi.mocked(api.chat).mockResolvedValue({ conversation, turns: [turn], total: 1, older_before: null } as never)
  const branch = stub('chatBranch', { id: 'chat-2' })
  const stream = stub('chatStream', {})
  const { user, router } = mount()
  await screen.findByText(reply.answer)
  await user.click(screen.getByRole('button', { name: 'Edit and ask again' }))
  const input = screen.getByRole('textbox', { name: 'Edit question' })
  await user.clear(input); await user.type(input, 'Show the stock instead')
  await user.click(screen.getByRole('button', { name: 'Send updated question' }))
  await waitFor(() => expect(router.state.location.search).toBe('?chat=chat-2'))
  expect(branch).toHaveBeenCalledWith('chat-1', 'turn-1', 'Show the stock instead', 'edit', expect.any(String))
  expect(stream).not.toHaveBeenCalled()
})

test('clarification keeps answers across Back and explicitly sends unknown information without approving a file', async () => {
  const clarified = { ...turn, reply: { ...reply, clarifications: [
    { question: 'Which period?', reason: 'Choose the data scope', options: ['November', 'October'] },
    { question: 'Which customer?', reason: 'Avoid mixing customers', options: ['Customer One', 'Customer Two'] },
  ] } }
  vi.mocked(api.chat).mockResolvedValue({ conversation, turns: [clarified], total: 1, older_before: null } as never)
  const stream = stub('chatStream', {}).mockImplementation(() => new Promise<ChatTurn>(() => {}))
  const approve = stub('approveDataset', {})
  const { user } = mount()
  await user.click(await screen.findByRole('radio', { name: 'November' }))
  await user.click(screen.getByRole('button', { name: 'Next' }))
  await user.click(screen.getByRole('button', { name: 'Back' }))
  expect((screen.getByRole('radio', { name: 'November' }) as HTMLInputElement).checked).toBe(true)
  await user.click(screen.getByRole('button', { name: 'Next' }))
  await user.click(screen.getByRole('button', { name: 'Not sure / skip' }))
  await waitFor(() => expect(stream).toHaveBeenCalledTimes(1))
  expect(stream.mock.calls[0][1]).toMatchObject({ clarification_turn_id: 'turn-1', question: expect.stringContaining('I do not know yet; leave this unresolved') })
  expect(stream.mock.calls[0][1].question).toContain('November')
  expect(stream.mock.calls[0][1].question).toContain('not approval')
  expect(approve).not.toHaveBeenCalled()
})

test('failed new-chat creation retains the draft and never starts the model request', async () => {
  stub('createChat', {}).mockRejectedValue(new Error('Storage unavailable'))
  const stream = stub('chatStream', {})
  const { user, router } = mount('/assistant?new=1')
  await composer(user, 'Compare customer files')
  await user.click(screen.getByRole('button', { name: 'Send question' }))
  await screen.findByText('Could not start a chat. Your draft is still here; try again.')
  expect((screen.getByRole('textbox', { name: 'Your question' }) as HTMLTextAreaElement).value).toBe('Compare customer files')
  expect(router.state.location.search).toBe('?new=1')
  expect(stream).not.toHaveBeenCalled()
})

test('denied conversation loads no messages or composer and exposes an explicit retry', async () => {
  vi.mocked(api.chat).mockRejectedValue(Object.assign(new Error('Forbidden'), { status: 403 }))
  const stream = stub('chatStream', {})
  mount()
  await screen.findByText('This chat could not be opened. Check your access or try again.', {}, { timeout: 3000 })
  expect(screen.queryByRole('textbox', { name: 'Your question' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  expect(stream).not.toHaveBeenCalled()
})


test('account load failure has retry and home link; retry keeps the requested chat', async () => {
  vi.mocked(api.me).mockRejectedValueOnce(new Error('Offline'))
  const { user, router } = mountPage(<AssistantPage />, '/assistant', '/assistant?chat=c1')
  const alert = await screen.findByRole('alert')
  expect(screen.getByRole('link', { name: /Back to home/ }).getAttribute('href')).toBe('/')
  await user.click(within(alert).getByRole('button', { name: 'Try again' }))
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith('c1'))
  expect(router.state.location.search).toBe('?chat=c1')
})
