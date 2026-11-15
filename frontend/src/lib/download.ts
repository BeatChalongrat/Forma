import { authHeaders } from './auth'

/** Downloads use the same authenticated origin as the app, including S3-backed exports. */
export async function downloadFile(path: string, fallback = 'forma-download') {
  if (!path.startsWith('/api/') || path.includes('\\')) throw new Error('Invalid download path')
  const url = new URL(path, window.location.origin)
  url.searchParams.set('proxy', 'true')
  const response = await fetch(url.pathname + url.search, { headers: authHeaders() })
  if (!response.ok) {
    let message = `Download failed (${response.status}). Please retry.`
    try { const value = await response.json(); message = value.message || message } catch { /* proxy response */ }
    throw new Error(message)
  }
  const header = response.headers.get('Content-Disposition') || ''
  let filename = /filename="([^"]+)"/i.exec(header)?.[1] || fallback
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)?.[1]
  if (encoded) { try { filename = decodeURIComponent(encoded) } catch { /* use fallback */ } }
  const objectUrl = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = objectUrl; anchor.download = filename
  document.body.appendChild(anchor)
  try { anchor.click() } finally { anchor.remove(); setTimeout(() => URL.revokeObjectURL(objectUrl), 1000) }
}
