"""Policy engine — turns proposal metadata, engine findings and drift into an approval decision."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from forma.mapping_dsl.models import MappingDocument
from forma.schema_registry import RISK_ORDER, TargetSchema


@dataclass
class PolicyDecision:
    decision: str  # auto_approve | review | block
    priority: int
    reasons: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    checks: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "priority": self.priority,
            "reasons": self.reasons,
            "risk_score": round(self.risk_score, 3),
            "checks": self.checks,
        }


DEFAULT_POLICY: dict[str, Any] = {
    "auto_approve": {
        "min_mapping_confidence": 0.95,
        "min_field_confidence_critical": 0.9,
        "min_field_confidence_high": 0.8,
        "max_warning_findings": 5,
        "allow_errors": False,
        "allowed_drift": ["identical", "header_moved", "columns_added"],
        "require_previous_approval_for_auto": True,
        "max_row_change_ratio": 0.3,
    },
    "review": {
        "priority_weights": {
            "critical_field_low_confidence": 50,
            "errors": 40,
            "drift_columns_removed": 30,
            "drift_columns_renamed": 20,
            "unrelated_file": 100,
            "new_customer": 15,
            "warnings": 2,
        }
    },
    "block": {"on_unrelated_file": True, "on_missing_identity": True, "on_zero_rows": True},
    "risk_overrides": {},
}


def file_policy(directory: Path) -> dict[str, Any]:
    """``approval.yaml`` as written (the baseline the web app edits on top of)."""
    p = directory / "approval.yaml"
    return dict(yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


def validate_policy(policy: dict[str, Any]) -> list[str]:
    """Why a policy edited in the web app cannot be used; ``[]`` when it can (ADR-017)."""
    from forma.fingerprint.fingerprint import DRIFT_KINDS

    p = _merge(DEFAULT_POLICY, policy)
    problems: list[str] = []
    unknown = set(policy) - set(DEFAULT_POLICY) - {"version"}
    if unknown:
        problems.append(f"unknown sections: {sorted(unknown)}")
    aa = p["auto_approve"]

    def number(path: str, v: Any, lo: float, hi: float | None = None) -> None:
        if isinstance(v, bool) or not isinstance(v, int | float) or v < lo or (hi is not None and v > hi):
            problems.append(f"{path} must be a number from {lo}" + (f" to {hi}" if hi is not None else " up"))

    for k in ("min_mapping_confidence", "min_field_confidence_critical", "min_field_confidence_high"):
        number(f"auto_approve.{k}", aa.get(k), 0, 1)
    number("auto_approve.max_row_change_ratio", aa.get("max_row_change_ratio"), 0)
    mwf = aa.get("max_warning_findings")
    if isinstance(mwf, bool) or not isinstance(mwf, int) or mwf < 0:
        problems.append("auto_approve.max_warning_findings must be a whole number ≥ 0")
    for k in ("allow_errors", "require_previous_approval_for_auto"):
        if not isinstance(aa.get(k), bool):
            problems.append(f"auto_approve.{k} must be true or false")
    drift = aa.get("allowed_drift")
    allowed = set(DRIFT_KINDS) - {"unrelated_file"}  # a wrong file is never auto-approved
    if not isinstance(drift, list) or not set(drift) <= allowed:
        problems.append(f"auto_approve.allowed_drift must be a subset of {sorted(allowed)}")
    weights = p["review"].get("priority_weights", {})
    known = set(DEFAULT_POLICY["review"]["priority_weights"]) | {f"drift_{k}" for k in DRIFT_KINDS}
    for k, v in weights.items():
        if k not in known:
            problems.append(f"review.priority_weights.{k} is not a known weight")
        elif isinstance(v, bool) or not isinstance(v, int) or v < 0:
            problems.append(f"review.priority_weights.{k} must be a whole number ≥ 0")
    for k, v in p["block"].items():
        if k not in DEFAULT_POLICY["block"] or not isinstance(v, bool):
            problems.append(f"block.{k} must be a known switch set to true or false")
    for sem, risk in p["risk_overrides"].items():
        if risk not in RISK_ORDER:
            problems.append(f"risk_overrides.{sem} must be one of {list(RISK_ORDER)}")
    return problems


class PolicyEngine:
    def __init__(self, policy: dict[str, Any] | None = None) -> None:
        self.policy = _merge(DEFAULT_POLICY, policy or {})

    @classmethod
    def load(cls, directory: Path, override: dict[str, Any] | None = None) -> PolicyEngine:
        """``approval.yaml``, with the version saved in the web app on top when there is one."""
        base = file_policy(directory)
        return cls(_merge(base, override) if override else base)

    def risk_of(self, schema: TargetSchema, pointer: str) -> str:
        try:
            sem = schema.field(pointer).semantic
        except Exception:
            sem = None
        if sem and sem in self.policy["risk_overrides"]:
            return str(self.policy["risk_overrides"][sem])
        return schema.risk_of(pointer)

    def evaluate(
        self,
        mapping: MappingDocument,
        schema: TargetSchema,
        *,
        findings: list[dict[str, Any]],
        rows_out: int,
        drift: str | None = None,
        has_previous_approval: bool = False,
        row_change_ratio: float | None = None,
        reused_approved: bool = False,
        other_customer: dict[str, Any] | None = None,
        llm_fallback: bool = False,
    ) -> PolicyDecision:
        aa = self.policy["auto_approve"]
        w = self.policy["review"]["priority_weights"]
        reasons: list[str] = []
        priority = 0
        checks: dict[str, Any] = {}
        risk = 0.0

        errors = [f for f in findings if f.get("severity") == "error"]
        warnings = [f for f in findings if f.get("severity") == "warning"]
        checks["errors"] = len(errors)
        checks["warnings"] = len(warnings)
        checks["rows_out"] = rows_out
        checks["drift"] = drift
        checks["reused_approved"] = reused_approved
        checks["llm_fallback"] = llm_fallback

        # ---- hard blocks
        blk = self.policy["block"]
        if blk["on_unrelated_file"] and drift == "unrelated_file":
            return PolicyDecision(
                "block",
                w["unrelated_file"],
                ["file structure does not match this customer's approved format (possible wrong file)"],
                1.0,
                checks,
            )
        if blk["on_zero_rows"] and rows_out == 0:
            return PolicyDecision("block", w["errors"], ["transformation produced zero records"], 1.0, checks)
        if blk["on_missing_identity"] and any(
            "identity" in str(f.get("message", "")) and f.get("severity") == "error" for f in findings
        ):
            reasons.append("identity keys missing on some rows")
            priority += w["errors"]
            risk += 0.4

        # ---- confidence per field (critical/high)
        low_critical: list[str] = []
        low_high: list[str] = []
        all_fields = list(mapping.fields) + [f for c in mapping.collections for f in c.fields]
        for fm in all_fields:
            r = self.risk_of(schema, fm.target)
            conf = fm.confidence if fm.confidence is not None else (mapping.meta.confidence or 0.5)
            if reused_approved:
                conf = 1.0  # a human already approved these exact field mappings
            if r == "critical" and conf < aa["min_field_confidence_critical"]:
                low_critical.append(f"{fm.target} ({conf:.2f})")
            elif r == "high" and conf < aa["min_field_confidence_high"]:
                low_high.append(f"{fm.target} ({conf:.2f})")
            risk += (1 - conf) * (RISK_ORDER.get(r, 1) + 1) / 4
        checks["low_confidence_critical"] = low_critical
        checks["low_confidence_high"] = low_high
        if low_critical:
            reasons.append("low confidence on critical fields: " + ", ".join(low_critical[:6]))
            priority += w["critical_field_low_confidence"]
        if low_high:
            reasons.append("low confidence on high-risk fields: " + ", ".join(low_high[:6]))
            priority += w["critical_field_low_confidence"] // 3

        mconf = mapping.meta.confidence if mapping.meta.confidence is not None else 0.0
        checks["mapping_confidence"] = mconf
        if mconf < aa["min_mapping_confidence"]:
            reasons.append(f"mapping confidence {mconf:.2f} < {aa['min_mapping_confidence']}")
            priority += 10

        if errors and not aa["allow_errors"]:
            reasons.append(f"{len(errors)} engine/validation errors")
            priority += w["errors"]
            risk += 0.3
        if len(warnings) > aa["max_warning_findings"]:
            reasons.append(f"{len(warnings)} warnings > {aa['max_warning_findings']}")
            priority += w["warnings"] * len(warnings)

        if drift is not None and drift not in aa["allowed_drift"]:
            reasons.append(f"schema drift: {drift}")
            priority += w.get(f"drift_{drift}", 20)
            risk += 0.2
        if other_customer:
            code = other_customer.get("customer_code") or other_customer.get("customer_id")
            reasons.append(
                f"structure is identical to customer {code}'s approved format — confirm this file belongs here"
            )
            priority += w["unrelated_file"] // 2
            risk += 0.3
        if llm_fallback:
            # The model failed, timed out or its output was rejected by the guard: the deterministic
            # draft is in use. Never auto-approve a fallback (ADR-013 / ADR-015).
            reasons.append(
                "AI mapping unavailable (provider failure or rejected output); heuristic draft used"
            )
            priority += w["new_customer"]
            risk += 0.2
        if aa["require_previous_approval_for_auto"] and not has_previous_approval:
            reasons.append("first time this customer/format is seen")
            priority += w["new_customer"]
        if row_change_ratio is not None and row_change_ratio > aa["max_row_change_ratio"]:
            reasons.append(f"row count changed {row_change_ratio:.0%} vs previous approved dataset")
            priority += 15
            risk += 0.15

        if not reasons:
            return PolicyDecision("auto_approve", 0, ["all policy checks passed"], min(risk, 1.0), checks)
        return PolicyDecision("review", priority, reasons, min(risk, 1.0), checks)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
