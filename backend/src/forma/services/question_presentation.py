"""Plain-language labels around the original question; no generated evidence or assumed answer."""

from typing import Any

LABELS = {
    "unit.sell": "หน่วยขาย",
    "unit.pack_size": "จำนวนชิ้นต่อแพ็ก",
    "currency": "สกุลเงิน",
    "price.amount": "ราคาขาย",
    "price.vat_included": "การรวม VAT",
    "stock.qty": "จำนวนสต็อก",
    "sku": "รหัสสินค้า",
}


def presentation(item: dict[str, Any]) -> dict[str, Any]:
    spec = item["field"]
    label = (
        spec.get("description_th")
        or LABELS.get(spec.get("semantic"))
        or spec.get("description")
        or spec.get("name")
        or spec["pointer"]
    )
    semantic = spec.get("semantic")
    action = item.get("action")
    kind = item["kind"]
    original = item.get("question")
    prompt = None
    why = "หลักฐานยังไม่พอยืนยันความหมายของข้อมูล กรุณาตรวจตัวอย่างและสอบถามเจ้าของไฟล์หากยังไม่ทราบ"
    if action == "value_map":
        prompt = f"ค่า “{item.get('source_value')}” ในไฟล์ หมายถึงอะไรสำหรับ {label}?"
        why = "รหัสนี้ยังไม่มีความหมายที่ยืนยันได้ คำตอบจะเปลี่ยนเฉพาะรหัสนี้และเก็บค่าอื่นตามเดิม"
    elif action == "date_format":
        prompt = f"วันที่ใน {label} เรียงวันกับเดือนอย่างไร?"
        why = "วันที่บางค่าอ่านได้มากกว่าหนึ่งแบบ ต้องยืนยันลำดับวันและเดือนก่อนแปลง"
    elif kind == "choice":
        prompt = f"คอลัมน์ไหนใช้เป็น {label}?"
        why = "พบคอลัมน์ที่อาจใช้ได้มากกว่าหนึ่งคอลัมน์ กรุณาเทียบค่าตัวอย่างกับความหมายที่ต้องการ"
    elif semantic == "price.vat_included":
        prompt = "ราคาในไฟล์รวม VAT แล้วหรือยัง?"
        why = "ตัวเลขราคาอย่างเดียวบอกไม่ได้ว่ารวมภาษีแล้ว ต้องตรวจเงื่อนไขราคาหรือถามเจ้าของไฟล์"
    elif semantic == "unit.pack_size":
        prompt = "หนึ่งแพ็กมีสินค้ากี่ชิ้น?" if kind == "number" else "จำนวนชิ้นต่อแพ็กที่ระบบอ่าน ตรงกับไฟล์ต้นทางหรือไม่?"
        why = "จำนวนชิ้นต่อแพ็กมีผลต่อการแปลงหน่วย กรุณาตรวจหน่วยขายและขนาดบรรจุในไฟล์"
    elif not original:
        prompt = f"การอ่านค่า {label} ถูกต้องหรือไม่?" if kind == "confirm" else f"ค่าที่ถูกต้องสำหรับ {label} คืออะไร?"
    # Preserve custom business wording when no deterministic translation is available.
    return {"question_th": prompt, "reason_th": why, "evidence_scope": "ตัวอย่างจากการสำรวจไฟล์ ไม่ใช่ทุกแถว"}
