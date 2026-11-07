import type { ChatDraft } from './chatDraft'

function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value && typeof value === 'object') return '{' + Object.entries(value).filter(([, v]) => v !== undefined).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => JSON.stringify(k) + ':' + canonical(v)).join(',') + '}'
  return JSON.stringify(value) ?? 'null'
}

export function resolveChatDraft(server: {revision: number; draft: ChatDraft}, local?: {revision: number; draft: ChatDraft; dirty?: boolean} | null) {
  const normalized = (draft: ChatDraft) => ({ input: '', attachment_ids: [], unuploaded: [], clarifications: {}, ...draft })
  // A reload may happen after the database commits but before the browser receives its acknowledgement.
  // Matching content is already saved; it is not a competing edit from another tab.
  const acknowledged = !!local?.dirty && canonical(normalized(local.draft)) === canonical(normalized(server.draft))
  const pending = !!local?.dirty && !acknowledged
  return { draft: pending ? local!.draft : server.draft, conflict: pending && local!.revision !== server.revision, acknowledged }
}
