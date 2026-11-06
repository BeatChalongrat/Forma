import type { ReviewDraft, ReviewDraftPayload } from './api'

export const emptyReviewDraft = (): ReviewDraftPayload => ({ answers: {}, remember: [], acknowledged: [], unsure: {}, section: '', comment: '' })
type Backup = { revision: number; payload: ReviewDraftPayload }
type State = { payload: ReviewDraftPayload; revision: number; loading: boolean; pending: boolean; error: string; conflict: ReviewDraft | null }
type Transport = { read: () => Promise<ReviewDraft>; write: (revision: number, payload: ReviewDraftPayload) => Promise<ReviewDraft> }
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)
export const normalizeDraft = (p: Partial<ReviewDraftPayload>): ReviewDraftPayload => ({ ...emptyReviewDraft(), ...p })

/** A single writer per tab; revision CAS on the server protects against every other tab/user. */
export class ReviewDraftStore {
  private state: State = { payload: emptyReviewDraft(), revision: 0, loading: true, pending: false, error: '', conflict: null }
  private listeners = new Set<() => void>()
  private saving: Promise<boolean> | null = null
  private loadPromise: Promise<void> | null = null
  private transport: Transport
  private backup: (value: Backup | null) => void
  private saved: Backup | null
  constructor(transport: Transport, backup: (value: Backup | null) => void, saved: Backup | null = null) { this.transport = transport; this.backup = backup; this.saved = saved }
  snapshot = () => this.state
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn) } }
  private emit(patch: Partial<State>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(fn => fn()) }
  private persist() { this.backup(this.state.pending ? { revision: this.state.revision, payload: this.state.payload } : null) }
  load = () => this.loadPromise ??= this.transport.read().then(remote => {
    const payload = normalizeDraft(remote.payload), local = this.saved
    if (local && !equal(local.payload, payload)) this.emit({ loading: false, error: '', payload: local.payload, revision: local.revision, pending: true, conflict: local.revision === remote.revision ? null : remote })
    else this.emit({ loading: false, error: '', payload, revision: remote.revision })
    this.persist()
  }).catch((e: Error) => { this.loadPromise = null; this.emit({ error: e.message }) })
  change = (payload: ReviewDraftPayload) => {
    if (this.state.loading) return
    this.emit({ payload, pending: true, error: '' }); this.persist()
  }
  flush = (): Promise<boolean> => {
    if (this.saving) return this.saving
    this.saving = this.drain().finally(() => { this.saving = null })
    return this.saving
  }
  private async drain(): Promise<boolean> {
    while (this.state.pending) {
      if (this.state.conflict || this.state.loading) return false
      const { revision, payload } = this.state
      try {
        const remote = await this.transport.write(revision, payload)
        this.emit({ revision: remote.revision, pending: !equal(payload, this.state.payload), error: '' })
        this.persist()
      } catch (e) {
        if ((e as { status?: number }).status === 409) {
          try { this.emit({ conflict: await this.transport.read(), error: (e as Error).message }) }
          catch { this.emit({ error: (e as Error).message }) }
        } else this.emit({ error: (e as Error).message })
        this.persist(); return false
      }
    }
    return !this.state.loading && !this.state.conflict
  }
  resolve = (keepMine: boolean) => {
    const remote = this.state.conflict
    if (!remote) return
    this.emit({ revision: remote.revision, payload: keepMine ? this.state.payload : normalizeDraft(remote.payload), conflict: null, pending: keepMine, error: '' }); this.persist()
  }
  clearBackup = () => this.backup(null)
}
