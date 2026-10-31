import { createLucideIcon } from 'lucide-react'

// Lucide's sparkle with the green dot of the FORMA wordmark: the assistant's one icon, everywhere it appears.
export const AssistantIcon = createLucideIcon('forma-assistant', [
  ['path', { d: 'M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z', key: 'star' }],
  ['circle', { cx: '20', cy: '4', r: '2.2', fill: 'var(--color-brand-green)', stroke: 'none', key: 'dot' }],
])
