import { useState } from 'react'
import { RotateCcw, ThumbsUp, ThumbsDown } from 'lucide-react'
import { api } from '../lib/api'
import { usePreferences } from '../lib/preferences'

export function ReplyActions({ chatId, turnId, disabled, onRegenerate, saved }: { saved?: {rating: string; comment: string} | null; chatId: string; turnId: string; disabled: boolean; onRegenerate: () => void }) {
  const { t } = usePreferences()
  const [localRating, setRating] = useState<string>(), [comment, setComment] = useState(saved?.comment ?? ''), [error, setError] = useState(''), [open, setOpen] = useState(false)
  const rating = localRating ?? saved?.rating ?? ''
  async function rate(value: 'helpful' | 'incorrect' | 'unclear') { try { await api.chatFeedback(chatId, turnId, value, comment); setRating(value); setError(''); setOpen(false) } catch { setError(t('Could not save feedback.', 'บันทึกความเห็นไม่ได้')) } }
  return <div className="text-xs text-muted space-y-2"><div className="flex items-center gap-2"><button className="btn btn-ghost btn-sm" disabled={disabled} onClick={onRegenerate} title={t('Rewrite from saved evidence in a new alternative. Does not repeat actions.', 'ตอบใหม่จากข้อมูลเดิมในอีกทางเลือก ไม่ทำรายการซ้ำ')}><RotateCcw size={14} />{t('Answer again', 'ตอบใหม่')}</button><button className="btn btn-ghost btn-sm" aria-label={t('Helpful', 'คำตอบมีประโยชน์')} aria-pressed={rating === 'helpful'} onClick={() => void rate('helpful')}><ThumbsUp size={14} /></button><button className="btn btn-ghost btn-sm" aria-label={t('Give feedback', 'แจ้งปัญหาคำตอบ')} onClick={() => setOpen(!open)}><ThumbsDown size={14} /></button>{rating && <span>{t('Feedback saved', 'บันทึกความเห็นแล้ว')}</span>}</div>{open && <div className="space-y-2"><textarea className="input w-full" maxLength={1000} placeholder={t('What should be improved?', 'ส่วนไหนควรปรับปรุง?')} value={comment} onChange={e => setComment(e.target.value)} /><button className="btn btn-sm" onClick={() => void rate('incorrect')}>{t('Incorrect information', 'ข้อมูลไม่ถูกต้อง')}</button><button className="btn btn-sm ml-2" onClick={() => void rate('unclear')}>{t('Hard to understand', 'เข้าใจยาก')}</button><p>{t('Feedback does not change approved mapping knowledge.', 'ความเห็นนี้ไม่เปลี่ยนความรู้การจับคู่ที่อนุมัติแล้ว')}</p></div>}{error && <p role="alert">{error}</p>}</div>
}
