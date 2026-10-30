"""Translate known policy diagnostics without asking a model or changing their meaning."""

from __future__ import annotations

import re

from forma.services.context import ServiceContext

LABELS_TH = {
    "sku": "รหัสสินค้า",
    "product_name": "ชื่อสินค้า",
    "barcode": "บาร์โค้ด",
    "price.amount": "ราคาขาย",
    "price.vat_included": "การรวม VAT",
    "price.vat_rate": "อัตรา VAT",
    "unit.pack_size": "จำนวนชิ้นต่อแพ็ก",
    "unit.sell": "หน่วยขาย",
    "stock.qty": "จำนวนสต็อก",
    "stock.warehouse": "คลังสินค้า",
    "stock.reserved": "จำนวนที่จองไว้",
    "promo.code": "รหัสโปรโมชัน",
    "promo.type": "ประเภทโปรโมชัน",
    "promo.start_date": "วันเริ่มโปรโมชัน",
    "promo.end_date": "วันสิ้นสุดโปรโมชัน",
    "promo.price": "ราคาโปรโมชัน",
    "discount.percent": "ส่วนลดเปอร์เซ็นต์",
    "discount.amount": "ส่วนลดจำนวนเงิน",
}


def explain(ctx: ServiceContext, reasons: list[str], target: str | None) -> list[dict[str, str]]:
    fields = {f.pointer: f for f in ctx.registry.get_ref(target).leaf_fields()} if target else {}
    result = []
    for reason in reasons:
        pointer = re.search(r"(/[^\s:,]+)", reason)
        spec = fields.get(pointer[1]) if pointer else None
        en = (spec.description or spec.name) if spec else "This field"
        th = (
            (spec.description_th or LABELS_TH.get(spec.semantic or "") or spec.description or spec.name)
            if spec
            else "ข้อมูลนี้"
        )
        pair: tuple[str, str]
        if match := re.search(r"only ([\d.]+)% of (\d+) SKUs keep their value", reason):
            pair = (
                f"{en}: {match[1]}% of {match[2]} matched items kept the previous value. Compare the source column and confirm whether the change is expected.",
                f"{th}: จาก {match[2]} รายการที่เทียบได้ มี {match[1]}% ที่ค่าเท่าเดิม ตรวจคอลัมน์ต้นทางและยืนยันว่าเป็นการเปลี่ยนข้อมูลตามปกติหรือไม่",
            )
        elif match := re.search(r"([\d.]+)% of (\d+) SKUs now have another value", reason):
            pair = (
                f"{en}: {match[1]}% of {match[2]} matched items changed. Open the questions to compare old and new values.",
                f"{th}: {match[1]}% ของ {match[2]} รายการที่เทียบได้มีค่าเปลี่ยนไป เปิดคำถามเพื่อเทียบค่าเดิมกับค่าครั้งนี้",
            )
        elif "low confidence on" in reason:
            pair = (
                f"{en}: the suggested interpretation is not confirmed. Check source examples before answering.",
                f"{th}: ระบบยังไม่มั่นใจว่าอ่านความหมายถูกต้อง ตรวจตัวอย่างในไฟล์ก่อนตอบ หากยังไม่ทราบให้เว้นไว้",
            )
        elif "mapping confidence" in reason:
            pair = (
                "The overall matching has not met the automatic approval threshold. Review the unconfirmed fields.",
                "การจับคู่คอลัมน์โดยรวมยังไม่ถึงเกณฑ์อนุมัติอัตโนมัติ ต้องตรวจจุดที่ระบบยังไม่แน่ใจ",
            )
        elif "columns_removed" in reason:
            pair = (
                "Some columns from the previous format are missing. Check that required information is still present.",
                "มีคอลัมน์จากรูปแบบเดิมหายไป ตรวจว่าข้อมูลที่จำเป็นยังอยู่ครบหรือย้ายไปคอลัมน์อื่น",
            )
        elif match := re.search(r"customer (.+?)'s approved format", reason):
            pair = (
                f"The layout matches customer {match[1]}'s approved format. This does not prove ownership; verify the sender and customer.",
                f"โครงสร้างไฟล์เหมือนรูปแบบที่เคยอนุมัติของ {match[1]} แต่ยังบอกไม่ได้ว่าเป็นไฟล์ของใคร ตรวจชื่อผู้ส่งและลูกค้าให้ตรงกัน",
            )
        elif match := re.search(r"row count changed ([\d.]+)%", reason):
            pair = (
                f"The row count changed by {match[1]}% from the previously approved data. Confirm that the delivery is complete.",
                f"จำนวนแถวต่างจากข้อมูลที่เคยอนุมัติ {match[1]}% ตรวจว่ารอบนี้ส่งข้อมูลครบตามที่ต้องการ",
            )
        elif "first time" in reason:
            pair = (
                "This customer or format is new. Confirm the matching before using it as a reference.",
                "ระบบเพิ่งพบลูกค้าหรือรูปแบบนี้ครั้งแรก ต้องตรวจการจับคู่ก่อนใช้เป็นรูปแบบอ้างอิง",
            )
        else:
            pair = (
                "An additional check needs review. Open the questions and source evidence; technical details are available below.",
                "มีผลตรวจเพิ่มเติมที่ต้องยืนยัน เปิดคำถามและหลักฐานต้นทางเพื่อตรวจต่อ ดูข้อความระบบต้นฉบับได้ด้านล่าง",
            )
        item = {"en": pair[0], "th": pair[1]}
        if item not in result:
            result.append(item)
    return result
