export function pointerParts(pointer: string): string[] {
  return pointer === '' ? [] : pointer.slice(1).split('/').map(p => p.replaceAll('~1', '/').replaceAll('~0', '~'))
}

export function getPointer(obj: unknown, pointer: string): unknown {
  let cur = obj
  for (const p of pointerParts(pointer)) {
    if (!cur || typeof cur !== 'object' || !Object.hasOwn(cur, p)) return undefined
    cur = (cur as Record<string, unknown>)[p]
  }
  return cur
}

/** Overlay staged edits without changing the source record or inherited object properties. */
export function setPointerLocal(obj: Record<string, unknown>, pointer: string, value: unknown) {
  const parts = pointerParts(pointer)
  if (!parts.length) return
  let cur: Record<string, unknown> = obj
  for (const p of parts.slice(0, -1)) {
    if (!Object.hasOwn(cur, p) || cur[p] === null || typeof cur[p] !== 'object') {
      Object.defineProperty(cur, p, { value: {}, writable: true, enumerable: true, configurable: true })
    }
    cur = cur[p] as Record<string, unknown>
  }
  Object.defineProperty(cur, parts.at(-1)!, { value, writable: true, enumerable: true, configurable: true })
}
