import type { ChatTurn } from './api'

export function abortableDelay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(new DOMException('Detached', 'AbortError')) }
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, ms)
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) abort()
  })
}

/** Reattach by reading the same saved attempt. Never replay POST or restart tools. */
export async function recoverChatTurn(
  read: () => Promise<ChatTurn>, signal: AbortSignal,
  snapshot: (turn: ChatTurn) => void,
  connection: (state: 'reconnecting' | 'following') => void,
  delay = abortableDelay,
): Promise<ChatTurn> {
  let missing = 0, failures = 0
  while (true) {
    signal.throwIfAborted()
    let turn: ChatTurn | undefined
    try { turn = await read() } catch (error) {
      signal.throwIfAborted()
      const status = (error as { status?: number })?.status
      if (status === 401 || status === 403 || (status === 404 && ++missing >= 8)) throw error
      if (status && status < 500 && status !== 404 && status !== 429) throw error
      failures += 1
      connection('reconnecting')
    }
    signal.throwIfAborted()
    if (turn) {
      failures = 0; missing = 0
      snapshot(turn)
      if (turn.status !== 'pending') return turn
      connection('following')
    }
    await delay(Math.min(5000, 1000 * 2 ** Math.min(failures, 3)), signal)
  }
}
