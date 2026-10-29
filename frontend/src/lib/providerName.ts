export function providerName(provider: string, endpoint?: string | null) {
  try { const host = new URL(endpoint || '').hostname; if (host === 'api.deepseek.com' || host === 'deepseek.com') return 'DeepSeek' } catch { /* Direct providers may omit a custom URL. */ }
  return ({ openai: 'OpenAI', anthropic: 'Anthropic', openai_compatible: 'OpenAI-compatible / Gateway', mock: 'Offline' } as Record<string, string>)[provider] || provider
}
