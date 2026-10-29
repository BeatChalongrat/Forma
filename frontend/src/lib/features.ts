import { useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import {
  Server, ArrowLeftRight, MessageSquare, MessageCircleMore, ClipboardCheck, Download, FileClock, FileJson, FileSearch, GitCompareArrows, History, Inbox,
  Network, ScrollText, ShieldAlert, ShieldCheck, SlidersHorizontal, Table2, TriangleAlert, Upload, Users, Workflow,
  type LucideIcon,
} from 'lucide-react'
import { api } from './api'

/** Where a feature opens. Features inside a round open on the round you are looking at, else the most recent one. */
export type FeatureTarget =
  | { kind: 'route'; to: string }
  | { kind: 'round' }
  | { kind: 'mapping'; tab?: string; view?: 'relationships' }
  | { kind: 'dataset'; tab?: string; tool?: 'trace' }

export type Step = 'import' | 'mapping' | 'data' | 'export' | 'setup'

export type Feature = {
  id: string
  title: string
  /** what it does, in Thai */
  th: string
  /** where it lives, as a path through the UI */
  where: string
  step: Step
  icon: LucideIcon
  keywords: string[]
  target: FeatureTarget
  adminOnly?: boolean
}

export const STEPS: { id: Step; title: string; th: string }[] = [
  { id: 'import', title: '1 · Upload files', th: 'รับไฟล์จากลูกค้า' },
  { id: 'mapping', title: '2 · Answer questions', th: 'ตรวจว่าคอลัมน์ไปลง field ถูกไหม' },
  { id: 'data', title: '2 · Check results', th: 'ตรวจและแก้ข้อมูลก่อนส่งออก' },
  { id: 'export', title: '3 · Download', th: 'อนุมัติแล้วดาวน์โหลด JSON' },
  { id: 'setup', title: 'Setup', th: 'ตั้งค่าระบบ ลูกค้า และ format' },
]

export const FEATURES: Feature[] = [
  // 1 · receive files
  { id: 'new-import', step: 'import', icon: Upload, title: 'Upload many files at once', th: 'ลากไฟล์ทั้งหมดมาวางพร้อมกัน ระบบดูจาก format ว่าเป็นของลูกค้าไหน แปลง และออก JSON ไฟล์ละ 1 ชุดให้อัตโนมัติ', where: 'Home / Imports › Upload files', keywords: ['upload', 'import', 'bulk', 'many', 'excel', 'csv', 'file', 'อัปโหลด', 'ไฟล์', 'นำเข้า', 'หลายไฟล์'], target: { kind: 'route', to: '/imports/new' } },
  { id: 'single-import', step: 'import', icon: Inbox, title: 'One delivery made of several files', th: 'ลูกค้าที่ส่งแยกไฟล์ (สินค้า + สต็อก + โปร) ที่ต้องรวมเป็นชุดเดียว เลือกลูกค้าเองแล้วอัปทีละรอบ', where: 'Upload files › single delivery', keywords: ['single', 'delivery', 'combine', 'รวมไฟล์', 'แยกไฟล์'], target: { kind: 'route', to: '/imports/new/single' } },
  { id: 'unknown-format', step: 'import', icon: Users, title: 'Files with an unknown format', th: 'ไฟล์ที่ไม่ตรงกับ format ของลูกค้ารายไหน เลือกลูกค้าหรือสร้างใหม่ แล้วระบบทำต่อเอง', where: 'Imports › Recent uploads › upload', keywords: ['unknown', 'customer', 'assign', 'ไม่รู้จัก', 'เลือกลูกค้า'], target: { kind: 'route', to: '/imports' } },
  { id: 'foresight', step: 'import', icon: Inbox, title: 'Who has not sent yet, and who will need a check', th: 'ระบบคาดวันส่งของลูกค้าแต่ละรายจากประวัติ เตือนว่ารายไหนเลยกำหนด และรายไหนรอบหน้าน่าจะต้องตรวจ', where: 'Home › Expected deliveries · Customers › Next expected', keywords: ['expected', 'overdue', 'late', 'cadence', 'watchlist', 'ยังไม่ส่ง', 'เลยกำหนด', 'คาดการณ์'], target: { kind: 'route', to: '/' } },
  { id: 'imports', step: 'import', icon: Inbox, title: 'Work rounds and file history', th: 'เลือกชื่อและวันที่รอบเอง เพิ่มไฟล์เข้ารอบเดิมได้หลายครั้ง กรองงานที่ต้องทำต่อ และดูวันอัปโหลดจริงที่แท็บไฟล์ทั้งหมด', where: 'Imports › Work rounds / All files', keywords: ['rounds', 'deliveries', 'status', 'รอบ', 'สถานะ', 'วันที่', 'label', 'history'], target: { kind: 'route', to: '/imports' } },
  { id: 'format-drift', step: 'import', icon: FileSearch, title: 'What changed in the file format', th: 'เทียบไฟล์กับ format ที่เคย approve: คอลัมน์ไหนเปลี่ยนชื่อ หาย หรือเพิ่ม และเตือนเมื่อไฟล์ไม่ใช่ของลูกค้ารายนี้', where: 'Imports › round › step 1', keywords: ['drift', 'format', 'columns', 'renamed', 'wrong file', 'เปลี่ยน', 'คอลัมน์'], target: { kind: 'round' } },
  // 2 · mapping
  { id: 'verdict', step: 'mapping', icon: ShieldCheck, title: 'The verdict: what FORMA concludes about a file', th: 'ทุกไฟล์มีข้อสรุป 1 ประโยค ความมั่นใจ สิ่งที่คนต้องตัดสิน และปุ่มยอมรับคลิกเดียวเมื่อปลอดภัย', where: 'Review › top of the page · Home · file rows', keywords: ['verdict', 'conclusion', 'confidence', 'accept', 'คำตัดสิน', 'ข้อสรุป', 'ยอมรับ'], target: { kind: 'route', to: '/review' } },
  { id: 'review', step: 'mapping', icon: ShieldCheck, title: 'Questions and issues', th: 'ตอบคำถามที่ระบบยังไม่แน่ใจ ดูหลักฐานและตัวอย่างก่อนบันทึกเวอร์ชันใหม่', where: 'Review', keywords: ['review', 'queue', 'approve', 'reject', 'รอตรวจ', 'อนุมัติ'], target: { kind: 'route', to: '/review' } },
  { id: 'risk', step: 'mapping', icon: TriangleAlert, title: 'Risk level and reasons', th: 'ทุกการตัดสินใจมีระดับความเสี่ยง Low / Medium / High พร้อมเหตุผล และจำนวน record ที่กระทบ', where: 'Imports › round › step 2', keywords: ['risk', 'trust', 'policy', 'ความเสี่ยง', 'เหตุผล'], target: { kind: 'round' } },
  { id: 'studio', step: 'mapping', icon: Workflow, title: 'Mapping Studio', th: 'ดูว่าคอลัมน์ไหนไปลง field ไหน พร้อมความมั่นใจ แก้ได้โดยลากคอลัมน์ไปวางที่ field', where: 'Imports › round › Open Mapping Studio', keywords: ['mapping', 'studio', 'columns', 'fields', 'drag', 'แมป', 'ลากวาง'], target: { kind: 'mapping' } },
  { id: 'relationships', step: 'mapping', icon: Network, title: 'How the sheets connect', th: 'แผนภาพความสัมพันธ์ระหว่างชีต/ตาราง (สินค้า–ราคา–สต็อก) วัดจากข้อมูลจริง ลากเพื่อเชื่อมเองได้', where: 'Mapping Studio › Relationships', keywords: ['relationships', 'join', 'sheets', 'diagram', 'ความสัมพันธ์', 'ชีต', 'เชื่อม'], target: { kind: 'mapping', view: 'relationships' } },
  { id: 'what-changed', step: 'mapping', icon: ArrowLeftRight, title: 'What changed vs the approved mapping', th: 'เมื่อลูกค้าเปลี่ยน format ระบบซ่อม mapping ให้และตรวจทีละ field กับข้อมูลจริง เช่น จับได้ว่าราคารวม VAT 7%', where: 'Mapping Studio › What changed', keywords: ['self-healing', 'changed', 'vat', 'field review', 'ซ่อม', 'เปลี่ยน'], target: { kind: 'mapping', tab: 'changes' } },
  { id: 'compare', step: 'mapping', icon: GitCompareArrows, title: 'Compare with the approved mapping', th: 'รัน mapping ใหม่คู่กับตัวที่ใช้อยู่บนไฟล์เดียวกัน ดูว่า record ไหนหาย ราคาไหนเปลี่ยน ก่อนจะยอมให้แทน', where: 'Mapping Studio › Compare', keywords: ['sandbox', 'compare', 'simulation', 'เทียบ', 'ทดลอง'], target: { kind: 'mapping', tab: 'compare' } },
  // 3 · data
  { id: 'editor', step: 'data', icon: Table2, title: 'Data Editor', th: 'แก้ข้อมูลเหมือน Excel: วางจาก Excel ได้ เลือกหลายแถวแล้วแก้พร้อมกัน ดูผลกระทบก่อนบันทึก', where: 'Imports › round › Open Data Editor', keywords: ['edit', 'grid', 'spreadsheet', 'paste', 'bulk', 'แก้ข้อมูล', 'ตาราง'], target: { kind: 'dataset' } },
  { id: 'trace', step: 'data', icon: FileClock, title: 'Trace a value to its Excel cell', th: 'ย้อนดูว่าค่านี้มาจากไฟล์ ชีต แถว คอลัมน์ไหน ผ่านสูตรอะไร และทำไมถึงต่างจากรอบก่อน', where: 'Data Editor › Trace value (หรือกด T)', keywords: ['lineage', 'trace', 'source', 'cell', 'why', 'ที่มา', 'ย้อน'], target: { kind: 'dataset', tool: 'trace' } },
  { id: 'anomalies', step: 'data', icon: ShieldAlert, title: 'Changes vs last round & anomalies', th: 'สรุปสิ่งที่เปลี่ยนจากรอบก่อน และของที่ดูผิดปกติ เช่น ราคาขึ้นแรง ส่วนลดเกิน สินค้าหาย พร้อม % ปกติ', where: 'Data Editor › Changes', keywords: ['anomaly', 'diff', 'price jump', 'outlier', 'ผิดปกติ', 'ราคา'], target: { kind: 'dataset', tab: 'diff' } },
  { id: 'copilot', step: 'data', icon: MessageSquare, title: 'Copilot — edit or ask in Thai', th: 'สั่งแก้เป็นภาษาไทย เช่น "ลดราคาหมวด X 5% เฉพาะที่สต็อกเกิน 100" หรือถามว่า "ทำไม SKU นี้ join ไม่ได้"', where: 'Data Editor › Copilot', keywords: ['copilot', 'ai', 'ask', 'natural language', 'ถาม', 'สั่ง'], target: { kind: 'dataset', tab: 'copilot' } },
  { id: 'findings', step: 'data', icon: ClipboardCheck, title: 'Records that break the format', th: 'record ที่ไม่ตรง Target format ต้องแก้หรือลบก่อน approve คลิกเพื่อไปที่แถวนั้น', where: 'Data Editor › Problems', keywords: ['schema', 'errors', 'invalid', 'findings', 'ผิด format', 'ข้อผิดพลาด'], target: { kind: 'dataset', tab: 'findings' } },
  { id: 'versions', step: 'data', icon: History, title: 'Versions and rollback', th: 'ทุกการแก้เป็นเวอร์ชันใหม่ ย้อนกลับได้ และรู้ว่าใครแก้อะไร', where: 'Data Editor › Versions', keywords: ['version', 'rollback', 'history', 'เวอร์ชัน', 'ย้อนกลับ'], target: { kind: 'dataset', tab: 'versions' } },
  // 4 · approve & export
  { id: 'download-all', step: 'export', icon: Download, title: 'Download all JSON of an upload', th: 'ดาวน์โหลด JSON ของทุกไฟล์ในการอัปโหลดเป็น zip ชื่อไฟล์ตามไฟล์ต้นทาง', where: 'Imports › Recent uploads › Download all JSON', keywords: ['zip', 'download', 'all', 'json', 'ดาวน์โหลดทั้งหมด'], target: { kind: 'route', to: '/imports' } },
  { id: 'export', step: 'export', icon: Download, title: 'Approve and export JSON', th: 'approve ข้อมูลแล้วดาวน์โหลด JSON / JSONL / CSV ที่มีแค่ field ตาม Target format', where: 'Imports › round › step 3 (หรือ Data Editor)', keywords: ['export', 'download', 'json', 'publish', 'ส่งออก', 'ดาวน์โหลด'], target: { kind: 'round' } },
  // setup
  { id: 'assistant', step: 'setup', icon: MessageCircleMore, title: 'FORMA assistant', th: 'สนทนาเพื่อค้นข้อมูลจริง ตรวจปัญหา และถามวิธีใช้ พร้อมแหล่งอ้างอิง', where: 'Assistant', keywords: ['assistant', 'chat', 'ผู้ช่วย', 'ถาม', 'สนทนา'], target: { kind: 'route', to: '/assistant' } },
  { id: 'customers', step: 'setup', icon: Users, title: 'Customers', th: 'ลูกค้าแต่ละราย ประวัติการส่งไฟล์ และ format ที่ใช้', where: 'Customers', keywords: ['customer', 'client', 'ลูกค้า'], target: { kind: 'route', to: '/customers' } },
  { id: 'ai', step: 'setup', icon: MessageSquare, title: 'AI connection, backup keys, model', th: 'วาง API key เพื่อเปิด AI เพิ่ม key สำรองและเลือก model', where: 'Settings › AI', keywords: ['ai', 'key', 'api key', 'model', 'backup', 'คีย์', 'โมเดล'], target: { kind: 'route', to: '/settings?tab=ai' } },
  { id: 'formats', step: 'setup', icon: FileJson, title: 'Target formats (JSON Schema)', th: 'อัปโหลด Target JSON ที่ระบบปลายทางต้องการ ตรวจก่อนใช้ และตั้งเป็นค่าเริ่มต้น', where: 'Settings › Target formats', keywords: ['schema', 'target', 'json', 'format', 'contract'], target: { kind: 'route', to: '/settings?tab=formats' } },
  { id: 'policy', step: 'setup', icon: SlidersHorizontal, title: 'Approval rules and anomaly limits', th: 'กำหนดว่าเมื่อไรระบบ approve เองได้ และเกณฑ์ความผิดปกติ (ราคาเปลี่ยนกี่ %)', where: 'Settings › Approval policy', keywords: ['policy', 'rules', 'threshold', 'กฎ', 'เกณฑ์'], target: { kind: 'route', to: '/settings?tab=policy' } },
  { id: 'learning', step: 'setup', icon: SlidersHorizontal, title: 'What FORMA learned from your corrections', th: 'ทุกครั้งที่คุณแก้แล้วอนุมัติ ระบบจำชื่อคอลัมน์และค่าที่ยืนยันไว้ รอบถัดไปใช้เอง และเสนอให้ใช้กับทุกลูกค้าเมื่อหลายรายสอนเหมือนกัน', where: 'Settings › What FORMA learned', keywords: ['learn', 'learning', 'alias', 'synonym', 'correction', 'เรียนรู้', 'จำ', 'แก้'], target: { kind: 'route', to: '/settings?tab=learning' } },
  { id: 'audit', step: 'setup', icon: ScrollText, title: 'Audit log', th: 'ใครทำอะไร เมื่อไร ทุกการเปลี่ยนแปลง', where: 'Settings › Audit log', keywords: ['audit', 'log', 'who', 'ประวัติ'], target: { kind: 'route', to: '/settings?tab=audit' } },
  { id: 'ops', step: 'setup', icon: Server, title: 'System health', th: 'คิวงาน worker งานที่ล้มเหลว และค่าใช้จ่าย AI', where: 'Ops', keywords: ['ops', 'queue', 'jobs', 'health', 'ระบบ'], target: { kind: 'route', to: '/ops' }, adminOnly: true },
]

export function matches(f: Feature, q: string): boolean {
  const s = q.trim().toLowerCase()
  if (!s) return true
  return [f.title, f.th, f.where, ...f.keywords].some((x) => x.toLowerCase().includes(s))
}

/** Opens a feature: on the round, mapping or dataset you are looking at, else on the most recent one that has it. */
export function useOpenFeature(onMissing: (message: string) => void): (f: Feature) => Promise<void> {
  const nav = useNavigate()
  const loc = useLocation()
  const qc = useQueryClient()
  return useCallback(async (f: Feature) => {
    const t = f.target
    if (t.kind === 'route') { nav(t.to); return }
    const path = loc.pathname
    const here = {
      round: /^\/rounds\/([^/]+)/.exec(path)?.[1],
      mapping: /^\/rounds\/[^/]+\/mapping\/([^/]+)/.exec(path)?.[1],
      dataset: /^\/datasets\/([^/]+)/.exec(path)?.[1],
    }
    const dash = await qc.fetchQuery({ queryKey: ['dashboard'], queryFn: api.dashboard, staleTime: 10000 })
    const recent = dash.recent_rounds
    if (t.kind === 'round') {
      const id = here.round ?? recent[0]?.id
      if (id) nav(`/rounds/${id}`)
      else onMissing('No import yet — upload a customer\'s files first.')
      return
    }
    if (t.kind === 'mapping') {
      const q = new URLSearchParams()
      if (t.tab) q.set('tab', t.tab)
      if (t.view) q.set('view', t.view)
      const suffix = q.toString() ? `?${q}` : ''
      if (here.mapping && here.round) { nav(`/rounds/${here.round}/mapping/${here.mapping}${suffix}`); return }
      const round = here.round ? await qc.fetchQuery({ queryKey: ['round', here.round], queryFn: () => api.round(here.round!) }) : null
      const fromRound = round?.mappings?.[0]
      if (round && fromRound) { nav(`/rounds/${round.id}/mapping/${fromRound.id}${suffix}`); return }
      const open = await qc.fetchQuery({ queryKey: ['review', 'open'], queryFn: () => api.review('open'), staleTime: 10000 })
      const task = open.find((x) => x.mapping_version_id)
      if (task?.mapping_version_id) { nav(`/rounds/${task.round_id}/mapping/${task.mapping_version_id}${suffix}`); return }
      const r = recent.find((x) => x.summary.mapping_version_id)
      if (r?.summary.mapping_version_id) nav(`/rounds/${r.id}/mapping/${r.summary.mapping_version_id}${suffix}`)
      else onMissing('No mapping yet — upload a customer\'s files first.')
      return
    }
    const q = new URLSearchParams()
    if (t.tab) q.set('tab', t.tab)
    if (t.tool) q.set('tool', t.tool)
    const suffix = q.toString() ? `?${q}` : ''
    if (here.dataset) { nav(`/datasets/${here.dataset}${suffix}`); return }
    const round = here.round ? await qc.fetchQuery({ queryKey: ['round', here.round], queryFn: () => api.round(here.round!) }) : null
    const id = round?.datasets?.[0]?.id ?? recent.find((x) => x.summary.dataset_version_id)?.summary.dataset_version_id
    if (id) nav(`/datasets/${id}${suffix}`)
    else onMissing('No data yet — approve a mapping and create the data (step 3 of a round) first.')
  }, [nav, loc.pathname, qc, onMissing])
}
