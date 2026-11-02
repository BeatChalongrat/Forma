# หลักฐานสาธารณะและเหตุผลเลือกกรณี

ตรวจวันที่ 2 พฤศจิกายน 2026 ข้อมูลบนเว็บไซต์บอกถึงงานที่เคยเผยแพร่ ไม่ใช่ทะเบียนสัญญาลูกค้าปัจจุบัน รายชื่อเหล่านี้ไม่ยืนยันว่าเป็นลูกค้า FORMA โดยตรง

## ยืนยันได้จากแหล่งทางการ

| หลักฐาน | สิ่งที่ยืนยันได้ | สิ่งที่ยังยืนยันไม่ได้ |
|---|---|---|
| [aCommerce Career](https://www.acommerce.asia/career/) | ระบุ Samsung, Adidas, Unilever, L’Oréal, Nestlé และตลาด Singapore, Thailand, Malaysia, Indonesia, Philippines | ความสัมพันธ์ปัจจุบันทุกราย, ERP ของทีมที่ส่งไฟล์, export template |
| [aCommerce Success Stories](https://www.acommerce.asia/acommerce-success-stories/) | มีกรณี Brand’s Suntory, Titan ใน Philippines, Ando ใน Indonesia, Galderma Philippines, Pedigree/Whiskas | ปริมาณไฟล์หรือสัดส่วนงาน FORMA และ ERP ของแต่ละแบรนด์ |
| [Ando case study](https://www.acommerce.asia/ando-experiences-remarkable-growth-spurt-in-indonesia-with-acommerce-strategy-and-services/) | งานแบรนด์รองเท้าใน Indonesia | รหัสไซซ์/สีและโครงสร้างไฟล์จริง |
| [Galderma case study](https://www.acommerce.asia/acommerce-x-galderma-success-story/) | งาน Galderma ใน Philippines | รูปแบบข้อมูลราคา หน่วย ภาษี และ ERP |
| [aCommerce Integration](https://www.acommerce.asia/integration/) | Directory มี SAP, NetSuite, Oracle, Shopify และช่องทาง marketplace | ไม่ได้พิสูจน์ว่าลูกค้ารายใดใช้ตัวไหน หรือทุก connector ใช้อยู่จริงในปัจจุบัน |
| [SAP News, 23 Oct 2025](https://news.sap.com/2025/10/nestle-sweetens-digital-future-sap-s4hana-cloud-private-edition/) | Nestlé ใช้ SAP และประกาศอัปเกรด SAP S/4HANA Cloud Private Edition ระลอกแรกครอบคลุม 112 ประเทศ | ไฟล์ที่ aCommerce ได้รับถูก export จาก ERP นี้โดยตรงหรือผ่าน distributor/Excel |

## สมมติฐานเพื่อออกแบบการทดสอบ

- กลุ่ม FMCG/อาหาร/ของใช้: คาดว่าต้องจัดการหน่วยชิ้น–แพ็ก–ลัง สินค้า/ราคา/สต็อกหลายตาราง และราคาแบบมีช่วงเวลา → G01/G03/G11/G12
- กลุ่มความงาม/ดูแลผิว: คาดว่ามี variant ขนาดบรรจุ ชื่อหลายภาษา และราคาที่สำนักงานใหญ่กับประเทศปลายทางใช้รูปแบบต่างกัน → G06/G12/G15
- กลุ่มรองเท้า/แฟชั่น: คาดว่ามี parent style + size + color แต่ SKU ของ variant ต้องไม่รวมกัน → G04
- กลุ่ม electronics: คาดว่าต้องรักษา model/SKU/barcode และหลายสกุลเงิน → G05/G10
- ลูกค้าใน SEA อาจได้ไฟล์จากระบบภูมิภาคหรือสำนักงานใหญ่ จึงทดสอบ EU/US-format/JP/KR เพิ่ม โดยไม่ได้อ้างว่าเป็น export จริงของแบรนด์ในรายชื่อ → G06–G10
- ERP family ที่มีหลักฐานให้เริ่มก่อน: SAP-like, NetSuite-like, Oracle-like และ Excel/CSV ที่ผู้จัดจำหน่ายปรับเอง; ไม่กำหนดเปอร์เซ็นต์การใช้งานเพราะไม่มีข้อมูล
- Dynamics/Odoo/ERP ท้องถิ่นอาจเพิ่มในชุดถัดไปได้ แต่ครั้งนี้ไม่มีหลักฐานพอจับคู่กับแบรนด์ใด จึงไม่ติดชื่อแบรนด์กับระบบเหล่านั้น

การบอกชื่อ ERP ไม่ได้บอกความหมายทุกคอลัมน์: instance, localization, custom fields, saved report และการแก้มือหลัง export เปลี่ยนผลได้ ชุดนี้จึงเน้นพฤติกรรมของไฟล์มากกว่าการจำชื่อ ERP

## สิ่งที่ต้องขอจากข้อมูลภายในในระยะต่อไป

รายชื่อลูกค้าใช้งานจริงแบบไม่ระบุความลับ, ประเทศของต้นทางข้อมูล, ERP/report name หากทราบ, ตัวอย่าง export ที่ลบข้อมูลลับ และตัวอย่าง JSON ที่ฝ่ายปลายทางรับจริง จึงจะถ่วงน้ำหนักชุดทดสอบตามปริมาณงานได้ ข้อมูลสาธารณะใช้เลือกความเสี่ยงเบื้องต้นเท่านั้น
