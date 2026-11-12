import { ApiError } from '../lib/api'
import { usePreferences } from '../lib/preferences'

/** Recover in place; do not reset the page or discard local drafts on a failed read. */
export function LoadError({ error, retry, busy = false, retained = false }: { error: unknown; retry: () => void; busy?: boolean; retained?: boolean }) {
  const { t } = usePreferences()
  const status = error instanceof ApiError ? error.status : 0
  const message = status === 403 ? t('You do not have access to this data.', 'คุณไม่มีสิทธิ์ดูข้อมูลนี้ ติดต่อผู้ดูแลหากต้องใช้งาน')
    : status === 404 ? t('This data could not be found.', 'ไม่พบข้อมูลนี้ อาจถูกย้ายหรือยกเลิกการใช้แล้ว')
    : status === 401 ? t('Please sign in again to access this data.', 'กรุณาเข้าสู่ระบบอีกครั้งเพื่อดูข้อมูลนี้')
    : t('Could not load data. Check your connection and try again.', 'โหลดข้อมูลไม่สำเร็จ ตรวจการเชื่อมต่อแล้วลองอีกครั้ง')
  return <div role="alert" className="card p-4 space-y-3">
    <p>{message}</p>
    {retained && <p className="text-sm text-muted">{t('Your unsaved edits remain on this page. Previously loaded data may be out of date.', 'การแก้ที่ยังไม่บันทึกยังอยู่ในหน้านี้ ข้อมูลที่โหลดไว้ก่อนหน้าอาจไม่เป็นปัจจุบัน')}</p>}
    <button className="btn btn-sm" disabled={busy} onClick={retry}>{busy ? t('Retrying…', 'กำลังลองใหม่…') : t('Try again', 'ลองอีกครั้ง')}</button>
  </div>
}
