import type { Me } from './api'

export const chatIdentity = (me: Me) => JSON.stringify([me.mode, me.subject, me.customers, me.effective_roles])
export const newChatUrl = () => '/assistant?new=' + crypto.randomUUID()
