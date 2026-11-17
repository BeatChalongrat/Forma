import { useEffect, useRef, useState } from 'react'
import { api, type CsvColumnChoice, type CsvTemplateAnalysis, type CsvTemplateInput } from '../lib/api'

/** CSV is an input method for the shared target contract, never a separate mapper. */
export function CsvFormatBuilder({ semantics, disabled = false, onInvalidate, onPrepared }: {
  semantics: Record<string, string>
  disabled?: boolean
  onInvalidate: () => void
  onPrepared: (document: Record<string, unknown>) => void
}) {
  const [content, setContent] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [encoding, setEncoding] = useState('utf-8')
  const [delimiter, setDelimiter] = useState<CsvTemplateInput['delimiter']>('auto')
  const [analysis, setAnalysis] = useState<CsvTemplateAnalysis | null>(null)
  const [columns, setColumns] = useState<CsvColumnChoice[]>([])
  const [schemaId, setSchemaId] = useState('')
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const revision = useRef(0)
  useEffect(() => () => { revision.current++ }, [])
  function invalidate() { revision.current++; onInvalidate(); setError('') }
  async function readFile(selected: File | null, codec: string) {
    invalidate(); setFile(selected); setAnalysis(null); setColumns([]); setContent('')
    const version = revision.current
    if (!selected) return
    if (!selected.name.toLowerCase().endsWith('.csv') || selected.size > 1048576) { setError('เลือกไฟล์ .csv ขนาดไม่เกิน 1 MB ที่มีหัวคอลัมน์และแถวตัวอย่าง'); return }
    try {
      const value = new TextDecoder(codec, { fatal: true }).decode(await selected.arrayBuffer())
      if (version === revision.current) setContent(value)
    } catch { if (version === revision.current) setError('อ่านตัวอักษรไม่ได้ เลือก encoding ให้ตรง หรือบันทึกไฟล์เป็น CSV UTF-8') }
  }
  async function analyze() {
    invalidate(); setBusy(true); setAnalysis(null); setColumns([])
    const version = revision.current
    try {
      const result = await api.analyzeCsvTemplate({ content, delimiter })
      if (version !== revision.current) return
      setAnalysis(result)
      setColumns(result.columns.map(c => ({ name: c.name, data_type: c.suggested_type, required: false, identity: false, semantic: '', description: '', confirmed: false })))
    } catch (e) { if (version === revision.current) setError((e as Error).message) }
    finally { setBusy(false) }
  }
  function update(index: number, patch: Partial<CsvColumnChoice>) {
    invalidate()
    setColumns(previous => previous.map((c, i) => i === index ? { ...c, ...patch, confirmed: 'confirmed' in patch ? !!patch.confirmed : false } : c))
  }
  async function prepare() {
    invalidate(); setBusy(true)
    const version = revision.current
    try {
      const result = await api.prepareCsvTemplate({ content, delimiter: analysis!.delimiter, schema_id: schemaId, title, columns: columns.map(c => ({ ...c, aliases: (c.aliases ?? []).map(a => a.trim()).filter(Boolean) })) })
      if (version === revision.current) onPrepared(result.document)
    } catch (e) { if (version === revision.current) setError((e as Error).message) }
    finally { setBusy(false) }
  }
  const confirmed = columns.filter(c => c.confirmed).length
  const ready = analysis && columns.length > 0 && confirmed === columns.length && columns.every(c => c.fixed_value == null || c.fixed_value.trim()) && columns.some(c => c.identity) && /^[a-z0-9][a-z0-9_-]{0,63}$/.test(schemaId) && title.trim()
  const scalarSemantics = Object.entries(semantics).filter(([key]) => !['prices', 'inventory', 'promotions', 'attributes', 'category_path', 'promo.bundle_skus', 'promo.channels'].includes(key))
  return <div className="space-y-5">
    <p className="text-sm text-muted">ใช้ CSV ตัวอย่างของผลลัพธ์ที่ต้องการ ระบบรักษาชื่อและลำดับคอลัมน์ ไม่ใช่การอัปโหลดข้อมูลลูกค้าเพื่อประมวลผล ไม่มีการเรียก AI ในขั้นตอนนี้</p>
    <fieldset disabled={busy || disabled} className="space-y-4">
      <div className="grid sm:grid-cols-2 gap-3">
        <label>ไฟล์ CSV ตัวอย่าง<input aria-label="ไฟล์ CSV ตัวอย่าง" type="file" accept=".csv,text/csv" className="input mt-1" onChange={e => void readFile(e.target.files?.[0] ?? null, encoding)} /></label>
        <label>ตัวอักษรในไฟล์<select className="input mt-1" value={encoding} onChange={e => { setEncoding(e.target.value); void readFile(file, e.target.value) }}><option value="utf-8">UTF-8 (แนะนำ)</option><option value="windows-874">ภาษาไทย Windows-874 / TIS-620</option><option value="utf-16le">UTF-16 LE</option></select></label>
        <label>ตัวคั่นคอลัมน์<select className="input mt-1" value={delimiter} onChange={e => { invalidate(); setDelimiter(e.target.value as CsvTemplateInput['delimiter']); setAnalysis(null); setColumns([]) }}><option value="auto">ตรวจจากไฟล์</option><option value=",">จุลภาค (,)</option><option value=";">อัฒภาค (;)</option><option value={'\t'}>Tab</option><option value="|">ขีดตั้ง (|)</option></select></label>
      </div>
      <p className="text-xs text-muted">ไม่เกิน 1 MB · 100 คอลัมน์ · 2,000 แถวตัวอย่าง · ใช้หัวคอลัมน์อย่างเดียวได้</p>
      <button className="btn" disabled={!content || busy} onClick={() => void analyze()}>อ่านแม่แบบ CSV</button>
      {analysis && <>
        <p role="status">พบ {columns.length} คอลัมน์ · ตรวจตัวอย่าง {analysis.row_count} แถว · ยืนยันแล้ว {confirmed}/{columns.length} คอลัมน์</p>
        <div className="grid sm:grid-cols-2 gap-3">
          <label>ชื่อรูปแบบ<input className="input mt-1" value={title} maxLength={200} onChange={e => { invalidate(); setTitle(e.target.value) }} placeholder="เช่น สินค้าสำหรับระบบปลายทาง" /></label>
          <label>รหัสรูปแบบ<input className="input mt-1" value={schemaId} maxLength={64} onChange={e => { invalidate(); setSchemaId(e.target.value) }} placeholder="เช่น catalog_export" /><span className="text-xs text-muted">ตัวพิมพ์เล็กอังกฤษ ตัวเลข _ หรือ - · ใช้รหัสเดิมเพื่อสร้างเวอร์ชันใหม่</span></label>
        </div>
        <p className="text-sm text-muted">เลือกคอลัมน์ที่ระบุรายการไม่ซ้ำ เช่น รหัสสินค้า หรือรหัสสินค้า + คลัง ตั้งความหมายและช่องห้ามว่างเอง ตัวอย่างไม่สามารถบอกกฎเหล่านี้ได้ครบ</p>
        {columns.map((column, index) => <section key={column.name} aria-label={`คอลัมน์ ${column.name}`} className="border border-line rounded-xl p-4 space-y-3">
          <h3 className="font-semibold break-all">{index + 1}. {column.name}</h3>
          <p className="text-sm break-all">ตัวอย่าง: {analysis.columns[index].examples.map(v => JSON.stringify(v)).join(' · ') || 'ไม่มี'} · ว่าง {analysis.columns[index].blank_count} แถว</p>
          <p className="text-xs text-muted">{analysis.columns[index].reason}</p>
          <div className="grid sm:grid-cols-2 gap-3">
            <label>ชนิดข้อมูล<select className="input mt-1" value={column.data_type} onChange={e => update(index, { data_type: e.target.value as CsvColumnChoice['data_type'], fixed_value: null })}><option value="string">ข้อความ / รหัส (เก็บเลขศูนย์นำหน้า)</option><option value="integer">จำนวนเต็ม</option><option value="number">ตัวเลข / ทศนิยม (เช่น 1234.50)</option><option value="boolean">ใช่ / ไม่ใช่ (true / false)</option><option value="date">วันที่ (YYYY-MM-DD)</option></select></label>
            <label>ความหมาย<select className="input mt-1" value={column.semantic} onChange={e => update(index, { semantic: e.target.value })}><option value="">ข้อมูลอื่น — อธิบายด้านล่าง</option>{scalarSemantics.map(([key, meaning]) => <option key={key} value={key}>{key} — {meaning}</option>)}</select></label>
          </div>
          <label className="block">ความหมายและกติกาที่ต้องการ<textarea className="input mt-1" maxLength={1000} value={column.description} onChange={e => update(index, { description: e.target.value })} placeholder="เช่น รหัสสินค้าเก็บเลขศูนย์นำหน้า หรือราคาสกุล USD; ระบุเรื่อง VAT / หน่วย / วันที่เมื่อเกี่ยวข้อง ระบบจะใช้เป็นคำอธิบายและถามหากหลักฐานยังไม่พอ" /></label>
          <label className="block">ชื่อเรียกอื่นที่ตรงความหมาย (ถ้าทราบ)<input className="input mt-1" value={(column.aliases ?? []).join('|')} onChange={e => update(index, { aliases: e.target.value.split('|') })} placeholder="เช่น ชื่อสินค้า | Item description" /><span className="text-xs text-muted">คั่นด้วย | ช่วยจับคู่ชื่อคอลัมน์ต่างภาษา ใช้เฉพาะคำที่หมายถึงข้อมูลเดียวกันจริง</span></label>
          {column.data_type === 'string' && <div className="space-y-2">
            <label className="flex items-center gap-2"><input type="checkbox" checked={column.fixed_value != null} onChange={e => update(index, { fixed_value: e.target.checked ? '' : null })} />ใช้ค่านี้ทุกแถว</label>
            {column.fixed_value != null && <label className="block">ค่าที่ต้องการใช้ทุกแถว<input aria-label="ค่าที่ต้องการใช้ทุกแถว" className="input mt-1" maxLength={200} value={column.fixed_value} onChange={e => update(index, { fixed_value: e.target.value })} /><span className="text-xs text-muted">ใช้กับรูปแบบนี้ เช่น สกุลเงินที่คุณยืนยันแล้ว ระบบยังให้ตรวจหากหลักฐานในไฟล์ขัดกัน การเปลี่ยนค่านี้ต้องยืนยันคอลัมน์ใหม่</span></label>}
          </div>}
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2"><input type="checkbox" checked={column.required} onChange={e => update(index, { required: e.target.checked, identity: e.target.checked ? column.identity : false })} />ห้ามว่าง</label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={column.identity} onChange={e => update(index, { identity: e.target.checked, required: e.target.checked || column.required })} />ใช้ระบุรายการ (เลือกหลายคอลัมน์ร่วมกันได้)</label>
          </div>
          <label className="flex items-center gap-2"><input type="checkbox" checked={column.confirmed} disabled={!column.semantic && !column.description.trim()} onChange={e => update(index, { confirmed: e.target.checked })} />ตรวจและยืนยันการตั้งค่าคอลัมน์นี้แล้ว</label>
        </section>)}
        <p className="text-xs text-muted">ช่องไม่บังคับยอมรับ null และส่งออกเป็นช่องว่าง โดยยังเก็บหัวคอลัมน์ไว้ · คำอธิบายช่วยจับคู่ แต่ไม่ใช่กฎคำนวณอัตโนมัติ · ข้อมูลซ้อนหรือกฎซับซ้อนยังใช้ JSON Schema ได้</p>
        <button className="btn btn-primary" disabled={!ready || busy} onClick={() => void prepare()}>ตรวจรูปแบบที่ยืนยันแล้ว</button>
      </>}
    </fieldset>
    {busy && <p role="status">กำลังตรวจแม่แบบ…</p>}
    {error && <p role="alert" className="text-bad">{error}</p>}
  </div>
}
