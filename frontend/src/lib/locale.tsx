import type { ReactNode } from 'react'
import { usePreferences } from './preferences'

const TH: Record<string, string> = {
  'Usage tips': 'คำแนะนำการใช้งาน',
  'Settings': 'ตั้งค่าระบบ', 'Customers': 'ลูกค้า', 'New customer': 'เพิ่มลูกค้า', 'New import': 'นำเข้าไฟล์',
  'Feature guide': 'คู่มือฟีเจอร์', 'System health': 'สถานะระบบ', 'Options': 'ตัวเลือก',
  'Target formats': 'รูปแบบปลายทาง', 'Approval policy': 'กฎการอนุมัติ', 'Audit log': 'ประวัติการทำงาน', 'System': 'ระบบ',
  'Connect AI': 'เชื่อมต่อ AI', 'Backup keys': 'คีย์สำรอง', 'Advanced settings': 'การตั้งค่าเพิ่มเติม',
  'Files': 'ไฟล์', 'Source files': 'ไฟล์ต้นทาง', 'Mapping versions': 'เวอร์ชันการจับคู่', 'Dataset versions': 'เวอร์ชันข้อมูล',
  'What changed': 'สิ่งที่เปลี่ยน', 'Compare': 'เปรียบเทียบ', 'Preview': 'ตัวอย่าง', 'Fields': 'ฟิลด์',
  'Relationships': 'การเชื่อมโยงชีต', 'Problems': 'ปัญหา', 'Changes': 'ความเปลี่ยนแปลง', 'Versions': 'เวอร์ชัน',
  'Sources': 'ต้นทาง', 'Results': 'ผลลัพธ์', 'Explain': 'อธิบาย', 'History': 'ประวัติ', 'Copilot': 'ผู้ช่วยแก้ข้อมูล',
  'Save': 'บันทึก', 'Cancel': 'ยกเลิก', 'Close': 'ปิด', 'Clear': 'ล้าง', 'Discard': 'ยกเลิกการแก้ไข',
  'Approve': 'อนุมัติ', 'Reject': 'ไม่อนุมัติ', 'Approve data': 'อนุมัติข้อมูล', 'Approve mapping': 'อนุมัติการจับคู่',
  'Review fields': 'ตรวจการจับคู่', 'Review data': 'ตรวจผลลัพธ์', 'Upload files': 'อัปโหลดไฟล์',
  'Export JSON': 'ดาวน์โหลด JSON', 'Download JSON': 'ดาวน์โหลด JSON', 'Download all': 'ดาวน์โหลดทั้งหมด',
  'Test connection': 'ทดสอบการเชื่อมต่อ', 'Add backup key': 'เพิ่มคีย์สำรอง', 'Remove': 'นำออก',
  'Error': 'ข้อผิดพลาด', 'Check': 'ต้องตรวจ', 'Info': 'ข้อมูล', 'Open': 'เปิด', 'Done': 'เสร็จแล้ว',
  'Uploaded': 'รับไฟล์แล้ว', 'Profiled': 'อ่านไฟล์แล้ว', 'Review': 'รอตรวจ', 'Blocked': 'ต้องตรวจไฟล์ต้นทาง',
  'Approved': 'อนุมัติแล้ว', 'Rejected': 'ไม่อนุมัติ', 'Transformed': 'สร้างผลลัพธ์แล้ว', 'Published': 'ส่งออกแล้ว',
  'Failed': 'ไม่สำเร็จ', 'Proposed': 'รอยืนยัน', 'Superseded': 'มีเวอร์ชันใหม่แล้ว', 'Ready': 'พร้อม',
  'No results': 'ไม่พบผลลัพธ์', 'No changes': 'ไม่มีการเปลี่ยนแปลง', 'No findings': 'ไม่พบปัญหา',
  'AI provider, target formats and the approval policy — edited here, checked before saving, recorded in the audit log, applied without a restart.': 'ตั้งค่า AI รูปแบบข้อมูลปลายทาง และกฎการอนุมัติ ตรวจสอบก่อนบันทึกและใช้งานได้ทันที',
  'Drop all the files here': 'ลากไฟล์ทั้งหมดมาวางที่นี่', 'Excel (.xlsx) or CSV — any number, from any customers': 'Excel (.xlsx) หรือ CSV จากลูกค้าหลายรายพร้อมกันได้',
  'Approve and export automatically': 'อนุมัติและสร้างไฟล์อัตโนมัติเมื่อผ่านการตรวจ',
  'What happens after upload?': 'หลังอัปโหลด ระบบทำอะไรต่อ?', 'Customer / delivery': 'ลูกค้า / รอบนำเข้า',
  'Next action': 'ขั้นตอนถัดไป', 'Records': 'รายการ', 'Updated': 'อัปเดตล่าสุด',
  'Workspace statistics': 'สถิติการใช้งาน', 'Nothing needs your attention': 'ไม่มีงานที่ต้องตรวจเพิ่ม',
  'You can upload another batch or open a recent file below.': 'อัปโหลดชุดใหม่หรือเปิดไฟล์ล่าสุดด้านล่างได้เลย',
  'Drop Excel or CSV files here': 'ลากไฟล์ Excel หรือ CSV มาวางที่นี่', 'Customer': 'ลูกค้า',
  'Label': 'ชื่อรอบ', 'Open round': 'เปิดรอบนำเข้า', 'Choose customer': 'เลือกลูกค้า', 'Resume upload': 'ทำต่อ',
}

export function Localized({ children }: { children: ReactNode }) {
  const { prefs } = usePreferences()
  return <>{prefs.language === 'th' && typeof children === 'string' ? TH[children] ?? children : children}</>
}
