# ชุดไฟล์จำลอง 25 รูปแบบ — ไฟล์แบบที่ลูกค้าส่งจริง พร้อมเฉลย

ทุกไฟล์สร้างจากแคตตาล็อกเดียวกับชุด ERP (`manual_test_files/tools/catalogue.py`: สินค้า 12 รายการ โปรโมชั่น 4 รายการ
คลัง 2 แห่ง) แต่เขียนออกมาในรูปแบบที่ต่างกัน แต่ละโฟลเดอร์มี `r1` (และ `r2`/`r3` สำหรับ 4 กรณีที่ทดสอบความจำและ drift)
ทุกรอบมี `expected.json` (เขียนจากแคตตาล็อก ไม่ใช่จากผลของระบบ) และ `ผลที่ควรได้.md` อธิบายกับดัก

โฟลเดอร์ `A*` (ไฟล์เสีย/ปลอมนามสกุล), `B*` (อัปโหลดเป็นชุด) และ `L*` (layout ชุดแรก) เป็นชุดทดสอบด้วยมือรุ่นก่อน
ที่อยู่ในโฟลเดอร์เดียวกัน ไม่ได้ถูกสร้างหรือให้คะแนนโดยเครื่องมือในหน้านี้

| กรณี | กับดัก |
|---|---|
| `C01_wide_stock_thai` | สต็อกเป็นคอลัมน์กว้างคลังละคอลัมน์ หัวไทย; r2 ค่าเปลี่ยน, r3 หัวราคาเปลี่ยนชื่อ + คอลัมน์ใหม่ |
| `C02_two_header_rows_codes` | หัวตาราง 2 ชั้น merge; สถานะรหัส A/I/D + legend; สต็อกกว้าง |
| `C03_sections_subtotals` | แถวหมวดเซลล์เดียว + แถวรวมหมวด + รวมทั้งหมด ไม่มีคอลัมน์หมวด; r2/r3 |
| `C04_cp874_semicolon_csv` | CSV เข้ารหัส cp874 คั่นด้วย ; ราคาเป็นข้อความมีจุลภาค |
| `C05_tsv_bom_net_prices` | TSV มี BOM ราคาเป็น Net (ex VAT) ตามหัว |
| `C06_dates_every_form` | วันที่โปรโมชั่น 4 รูปแบบ ไทย/พ.ศ./ISO/อังกฤษ |
| `C07_numbers_messy` | ราคาเป็นข้อความหลายแบบ: 7.-, ฿14, 39 บาท, เลขไทย ๒๐, ช่องว่าง |
| `C08_vat_exclusive_note_row` | ราคาก่อน VAT บอกไว้ในแถวหมายเหตุเหนือตาราง หัวราคาเฉย ๆ |
| `C09_vat_flag_per_row` | คอลัมน์ รวมภาษี Y/N ต่างกันทีละแถว ราคาเขียนตามนั้น |
| `C10_two_price_columns` | ราคาปลีกและราคาส่งคนละคอลัมน์ → prices 2 รายการ |
| `C11_units_pack_in_text` | หน่วยเป็นข้อความมีขนาดแพ็ค: ลัง(24), แพ็ค 6, โหล, ขวด |
| `C12_status_numeric_legend` | สถานะเป็นเลข 0/1/2 ความหมายอยู่ชีต legend |
| `C13_category_code_and_name` | รหัสหมวดกับชื่อหมวดอยู่คู่กัน เฉลยใช้ชื่อ |
| `C14_leading_zeros_duplicates` | รหัสมีศูนย์นำหน้า (ข้อความ) และแถวซ้ำ 3 แถว |
| `C15_merged_title_blank_columns_injection` | หัวเรื่อง merge, คอลัมน์ว่างคั่น, คอลัมน์/แถวท้ายว่าง, หมายเหตุมีคำสั่ง AI |
| `C16_three_files` | สินค้า xlsx + สต็อก csv + โปร xlsx ในรอบเดียว; r2/r3 ชื่อคอลัมน์สต็อกเปลี่ยน |
| `C17_abbreviations_en` | หัวคอลัมน์ย่อล้วน + ชีตสต็อกยาว; r2/r3 เพิ่มคอลัมน์ |
| `C18_inventory_subtotals` | ชีตสต็อกยาวมีแถวรวมต่อคลังและรวมทั้งหมด |
| `C19_hidden_calc_sheet` | ชีตซ่อนที่มีสูตร (ไม่มีค่า cache) อยู่ข้างชีตสินค้า |
| `C20_promotions_on_product_rows` | โปรโมชั่นอยู่บนแถวสินค้า ส่วนใหญ่ว่าง |
| `C21_codes_as_numbers` | รหัสและบาร์โค้ดถูก Excel เก็บเป็นตัวเลข (8.85E+12) |
| `C22_mixed_language_headers` | หัวคอลัมน์สองภาษาในช่องเดียว |
| `C23_usd_currency_column` | ราคาเป็น USD มีคอลัมน์สกุลเงิน |
| `C24_blank_rows_and_footer` | แถวว่างคั่นทุก 4 แถวในตาราง + ท้ายรายงาน 2 บรรทัด |
| `C25_old_and_new_price` | คอลัมน์ ราคา และ ราคา (ใหม่ มีผล 1 ต.ค. 69) |

สร้างใหม่: `backend/.venv/Scripts/python manual_test_files/cases/tools/make_cases.py`

ให้คะแนนทั้งชุดคำสั่งเดียว (heuristic ล้วน ไม่เสียเงิน):

    cd backend && .venv/Scripts/forma benchmark ../manual_test_files/cases/manifest.json ../manual_test_files/cases/report.json

ให้โมเดลจริง map (ต้องตั้ง provider ไว้แล้ว มีค่าใช้จ่าย):

    cd backend && .venv/Scripts/forma benchmark ../manual_test_files/cases/manifest.json ../manual_test_files/cases/report-llm.json --llm

รันผ่าน stack จริงทั้ง r1/r2/r3 (ความจำ, drift, การ์ดคำถาม) ด้วย `manual_test_files/tools/airun.py`:

    backend/.venv/Scripts/python manual_test_files/tools/airun.py cases OUT.json [CODE ...] [r1 r2 r3]

เทียบไฟล์ทีละรอบ: `backend/.venv/Scripts/python manual_test_files/erp/tools/score.py got.json expected.json`

เทียบก่อน/หลังทั้งชุด (รายงาน benchmark สองไฟล์ หรือ airun JSON):

    backend/.venv/Scripts/python manual_test_files/cases/tools/compare.py manual_test_files/cases/report-heuristic.json report-llm.json

`report-heuristic.json` คือตัวเลขก่อน (heuristic ล้วน ไม่มีโมเดล) ที่เก็บไว้ในโฟลเดอร์นี้ สร้างใหม่ได้ด้วยคำสั่ง benchmark ด้านบน

ข้อควรรู้: `forma benchmark` เทียบทุกฟิลด์ตรงตัว รวมชื่อ `price_list` ที่สัญญาปล่อยให้คนตั้ง (C10) ส่วน `score.py` ไม่ให้คะแนนชื่อนั้น
กรณี C13 และ C25 เป็นเรื่องตัดสิน: ระบบที่ **ถาม** ว่าควรใช้คอลัมน์ไหนถือว่าทำถูก แม้คะแนนรอบแรกจะไม่ตรงเฉลย
