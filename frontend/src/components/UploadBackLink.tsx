import { Link, useLocation } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { usePreferences } from '../lib/preferences'

export function UploadBackLink({ fallback = '/imports' }: { fallback?: string }) {
  const { t } = usePreferences()
  const { state } = useLocation()
  const origin = state as { uploadReturnTo?: unknown; uploadReturnState?: unknown } | null
  const target = typeof origin?.uploadReturnTo === 'string' && /^\/(?:imports(?:\/new)?(?:\?|$)|uploads\/[^/?#]+(?:\?|$))/.test(origin.uploadReturnTo) ? origin.uploadReturnTo : fallback
  const label = target.startsWith('/uploads/') ? t('Back to this round', 'กลับรอบงานนี้') : target.startsWith('/imports/new') ? t('Back to upload files', 'กลับหน้าอัปโหลดไฟล์') : t('Back to upload history', 'กลับประวัติไฟล์')
  return <Link to={target} state={origin?.uploadReturnState} className="btn btn-ghost btn-sm mb-5"><ArrowLeft size={15} />{label}</Link>
}
