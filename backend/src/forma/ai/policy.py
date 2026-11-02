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
    risk_level: str = "medium"  # low | medium | high — what a reviewer should expect (ADR-019)
    risk_factors: list[str] = field(default_factory=list)
    impact: dict[str, Any] = field(default_factory=dict)  # records this mapping changes vs the approved one

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "priority": self.priority,
            "reasons": self.reasons,
            "risk_score": round(self.risk_score, 3),
            "risk_level": self.risk_level,
            "risk_factors": self.risk_factors,
            "impact": self.impact,
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
            "sandbox_failed": 60,
            "sandbox_changed_values": 25,
        },
        "high_impact_ratio": 0.2,  # share of records a new mapping changes that makes it high risk
    },
    "block": {"on_unrelated_file": True, "on_missing_identity": True, "on_zero_rows": True},
    "risk_overrides": {},
    # Mapping simulation sandbox (ADR-018): a new mapping vs the approved one, on the same real files.
    "sandbox": {
        "enabled": True,
        "max_lost_records": 0,  # records the approved mapping produces that the new one drops → block
        "max_new_errors": 0,  # engine + schema errors beyond the approved mapping's → block
        "max_new_invalid_prices": 0,  # records without a valid price beyond the approved mapping's → block
        "max_new_duplicates": 0,  # duplicate identities beyond the approved mapping's → block
        "max_changed_prices_auto": 0,  # more changed prices than this → a human reviews (never auto)
        # ADR-037: when every important field keeps last round's published values on the same SKUs, the
        # sandbox delta describes the *approved* mapping breaking on a moved layout, not a risk in the
        # proposal — and the drift is not a reason either. Off by default; calibration proposes it.
        "trust_data_checks": False,
    },
    # Change intelligence (ADR-022): what counts as unusual in a round's data.
    "anomaly": {
        "max_price_change_pct": 30,  # a price moving more than this vs last round
        "max_discount_pct": 50,  # a discount above this
        "max_stock_drop_pct": 80,  # stock falling more than this vs last round
        "max_removed_pct": 10,  # share of last round's SKUs gone
        "row_count_tolerance_pct": 30,  # records vs the customer's recent average
        "outlier_mad": 6,  # a price this many robust deviations from its category's median
        "history_rounds": 5,  # published rounds the row-count baseline averages
        "block_approval_on_high": True,  # approving data with high-severity anomalies needs an acknowledgement
    },
    # Automatic publishing (ADR-027): data approved and exported without a person when every check passes.
    "publish": {
        "enabled": True,  # a round asked to publish automatically (bulk upload) may be; False: people approve all
        "max_high_anomalies": 0,  # more high-severity anomalies than this → a person checks the data
        "max_error_findings": 0,  # engine errors (duplicate identities, broken joins) → a person checks
    },
    # Confidence from evidence, not from the model's word (ADR-059): what FORMA's own matcher, the
    # self-check and the plausibility rules say about a field bounds the confidence the model gave it.
    "confidence": {
        "agree_floor": 0.9,  # the matcher and the model read a field the same way: at least this sure
        "disagree_cap": 0.7,  # they read it differently: at most this sure, and it becomes a choice
        "unresolved_self_check_cap": 0.8,  # the self-check left problems: the mapping is at most this sure
        "plausibility_cap": 0.75,  # a plausibility warning names the field: at most this sure
    },
    # Learning across customers (ADR-059): a header → field pair this many customers approved is everyone's alias.
    "learning": {"share_after_customers": 2},
    # Spot checks of automation (ADR-059): this share of automatic approvals gets a person's look anyway.
    "audit": {"sample_share": 0.05},
    # The verdict layer (ADR-036): one conclusion per file, from the stored reports.
    "verdict": {
        "min_pairs": 3,  # identity-matched SKUs before data checks may carry a conclusion
        "different_share": 0.8,  # share of checked important fields that no longer match → wrong file
        "min_group": 5,  # anomalies of one kind before they become one conclusion
        "max_must_decide": 3,  # decisions shown on the card; the rest are counted
        "narrative": "on_open",  # on_open | never — when a model may narrate a verdict
    },
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
    number("review.high_impact_ratio", p["review"].get("high_impact_ratio"), 0, 1)
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
    for k, v in p["sandbox"].items():
        if k in ("enabled", "trust_data_checks"):
            if not isinstance(v, bool):
                problems.append(f"sandbox.{k} must be true or false")
        elif k not in DEFAULT_POLICY["sandbox"]:
            problems.append(f"sandbox.{k} is not a known setting")
        elif isinstance(v, bool) or not isinstance(v, int) or v < 0:
            problems.append(f"sandbox.{k} must be a whole number ≥ 0")
    for k, v in p["anomaly"].items():
        if k not in DEFAULT_POLICY["anomaly"]:
            problems.append(f"anomaly.{k} is not a known setting")
        elif isinstance(DEFAULT_POLICY["anomaly"][k], bool):
            if not isinstance(v, bool):
                problems.append(f"anomaly.{k} must be true or false")
        else:
            number(f"anomaly.{k}", v, 0)
    for k, v in p["publish"].items():
        if k not in DEFAULT_POLICY["publish"]:
            problems.append(f"publish.{k} is not a known setting")
        elif isinstance(DEFAULT_POLICY["publish"][k], bool):
            if not isinstance(v, bool):
                problems.append(f"publish.{k} must be true or false")
        elif isinstance(v, bool) or not isinstance(v, int) or v < 0:
            problems.append(f"publish.{k} must be a whole number ≥ 0")
    for sem, risk in p["risk_overrides"].items():
        if risk not in RISK_ORDER:
            problems.append(f"risk_overrides.{sem} must be one of {list(RISK_ORDER)}")
    for k, v in p["confidence"].items():
        if k not in DEFAULT_POLICY["confidence"]:
            problems.append(f"confidence.{k} is not a known setting")
        else:
            number(f"confidence.{k}", v, 0, 1)
    sa = p["learning"].get("share_after_customers")
    if isinstance(sa, bool) or not isinstance(sa, int) or sa < 1:
        problems.append("learning.share_after_customers must be a whole number ≥ 1")
    for k in p["learning"]:
        if k != "share_after_customers":
            problems.append(f"learning.{k} is not a known setting")
    for k, v in p["audit"].items():
        if k != "sample_share":
            problems.append(f"audit.{k} is not a known setting")
        else:
            number("audit.sample_share", v, 0, 1)
    for k, v in p["verdict"].items():
        if k not in DEFAULT_POLICY["verdict"]:
            problems.append(f"verdict.{k} is not a known setting")
        elif k == "narrative":
            if v not in ("on_open", "never"):
                problems.append("verdict.narrative must be on_open or never")
        elif k == "different_share":
            number("verdict.different_share", v, 0, 1)
        elif isinstance(v, bool) or not isinstance(v, int) or v < 1:
            problems.append(f"verdict.{k} must be a whole number ≥ 1")
    return problems


def _data_identical(checks: list[dict[str, Any]] | None, *, min_pairs: int = 3) -> bool:
    """Every important changed field keeps last round's published values on enough SKUs (ADR-037)."""
    important = [
        c
        for c in checks or []
        if c.get("risk") in ("critical", "high") and c.get("verdict") not in (None, "no_data")
    ]
    return bool(important) and all(
        c.get("verdict") == "same" and int(c.get("pairs") or 0) >= min_pairs for c in important
    )


def _meaning_doubts(checks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Self-healing data checks (ADR-021) that doubt a critical/high field still means the same thing."""
    doubtful = ("vat_added", "vat_removed", "scaled", "different")
    return [c for c in checks or [] if c.get("verdict") in doubtful and c.get("risk") in ("critical", "high")]


class PolicyEngine:
    def __init__(self, policy: dict[str, Any] | None = None) -> None:
        self.policy = _merge(DEFAULT_POLICY, policy or {})

    @classmethod
    def load(cls, directory: Path, override: dict[str, Any] | None = None) -> PolicyEngine:
        """``approval.yaml``, with the version saved in the web app on top when there is one."""
        base = file_policy(directory)
        return cls(_merge(base, override) if override else base)

    def for_customer(self, overrides: dict[str, Any] | None) -> PolicyEngine:
        """This policy with a customer's overrides on top (ADR-037); the same engine when there are none."""
        return PolicyEngine(_merge(self.policy, overrides)) if overrides else self

    def evaluate_publish(
        self,
        *,
        mapping_approved_by: str | None,
        rows: int,
        invalid_records: int,
        error_findings: int,
        high_anomalies: int,
    ) -> PolicyDecision:
        """May this dataset be approved and exported without a person (ADR-027)? ``publish`` with the checks
        that passed, or ``review`` with every reason a person has to look first."""
        pp = self.policy["publish"]
        held: list[str] = []
        if not pp["enabled"]:
            held.append("automatic publishing is turned off in the approval policy")
        if not mapping_approved_by:
            held.append("the mapping is not approved")
        if rows == 0:
            held.append("the data has no records")
        if invalid_records:
            held.append(f"{invalid_records} records do not match the target format")
        if error_findings > pp["max_error_findings"]:
            held.append(f"{error_findings} engine errors (duplicate identities, broken links)")
        if high_anomalies > pp["max_high_anomalies"]:
            held.append(f"{high_anomalies} high-severity anomalies vs the last round")
        checks = {
            "mapping_approved_by": mapping_approved_by,
            "rows": rows,
            "invalid_records": invalid_records,
            "error_findings": error_findings,
            "high_anomalies": high_anomalies,
        }
        if held:
            return PolicyDecision("review", 50, held, checks=checks, risk_level="medium")
        passed = [
            "mapping approved automatically"
            if mapping_approved_by == "policy"
            else f"mapping approved by {mapping_approved_by}",
            f"all {rows} records match the target format",
            "no engine errors",
            "no high-severity anomalies"
            if not high_anomalies
            else f"{high_anomalies} high-severity anomalies (within the limit)",
        ]
        return PolicyDecision("publish", 0, passed, checks=checks, risk_level="low")

    def sandbox_problems(self, report: dict[str, Any] | None) -> list[str]:
        """Why the sandbox comparison must stop this mapping; ``[]`` when it may go on (ADR-018)."""
        sb = self.policy["sandbox"]
        delta = (report or {}).get("delta")
        if not sb.get("enabled") or not delta:
            return []
        out: list[str] = []
        if delta["lost"] > sb["max_lost_records"]:
            out.append(f"sandbox: {delta['lost']} records the approved mapping produces are missing")
        if delta["new_errors"] > sb["max_new_errors"]:
            out.append(f"sandbox: {delta['new_errors']} more errors than the approved mapping")
        if delta["new_invalid_prices"] > sb["max_new_invalid_prices"]:
            out.append(f"sandbox: {delta['new_invalid_prices']} more records without a valid price")
        if delta["new_duplicates"] > sb["max_new_duplicates"]:
            out.append(f"sandbox: {delta['new_duplicates']} more duplicate records")
        return out

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
        sandbox: dict[str, Any] | None = None,
        field_checks: list[dict[str, Any]] | None = None,
        open_questions: list[Any] | None = None,
        agreement: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        d = self._decide(
            mapping,
            schema,
            findings=findings,
            rows_out=rows_out,
            drift=drift,
            has_previous_approval=has_previous_approval,
            row_change_ratio=row_change_ratio,
            reused_approved=reused_approved,
            other_customer=other_customer,
            llm_fallback=llm_fallback,
            sandbox=sandbox,
            field_checks=field_checks,
        )
        if open_questions:
            d.checks["unresolved_questions"] = len(open_questions)
            if d.decision == "auto_approve":
                d.decision = "review"
                d.reasons = []
            d.reasons.append(f"{len(open_questions)} questions still need operator confirmation")
            d.priority = max(d.priority, 50)
        if agreement is not None:
            # fields the matcher had mapped and the model dropped are not a confidence question: a person
            # decides whether the file really lacks them (ADR-059)
            d.checks["agreement"] = {k: len(v) for k, v in agreement.items()}
            dropped = [
                p for p in agreement.get("dropped") or [] if self.risk_of(schema, p) in ("critical", "high")
            ]
            if dropped and d.decision != "block":
                if d.decision == "auto_approve":
                    d.decision = "review"
                    d.reasons = []
                d.reasons.append(
                    "the model dropped fields FORMA's matcher had mapped: " + ", ".join(dropped[:6])
                )
                d.priority = max(
                    d.priority,
                    self.policy["review"]["priority_weights"]["critical_field_low_confidence"] // 2,
                )
                d.checks["dropped_high_risk"] = dropped
        self._classify(
            d, findings=findings, rows_out=rows_out, drift=drift, sandbox=sandbox, field_checks=field_checks
        )
        return d

    def _classify(
        self,
        d: PolicyDecision,
        *,
        findings: list[dict[str, Any]],
        rows_out: int,
        drift: str | None,
        sandbox: dict[str, Any] | None,
        field_checks: list[dict[str, Any]] | None = None,
    ) -> None:
        """Low = safe to automate, medium = a human looks, high = a human must decide (ADR-019)."""
        delta = (sandbox or {}).get("delta") or {}
        affected = (
            int(delta.get("changed_records", 0)) + int(delta.get("lost", 0)) + int(delta.get("gained", 0))
        )
        ratio = affected / rows_out if rows_out and delta else None
        high_impact = ratio is not None and ratio >= float(self.policy["review"]["high_impact_ratio"])
        if delta:
            d.impact = {
                "affected_records": affected,
                "of_records": rows_out,
                "ratio": round(ratio, 4) if ratio is not None else None,
                "changed_prices": int(delta.get("changed_prices", 0)),
                "high": high_impact,  # also listed as a risk factor
            }
        factors: list[str] = []
        if d.decision == "block":
            factors.append("blocked by policy")
        if d.checks.get("low_confidence_critical"):
            factors.append("ambiguous critical field (price, discount, identity …)")
        if d.checks.get("errors"):
            factors.append("validation or engine errors")
        # join cardinality (keys that repeat, so rows multiply) — rows that match nothing are data, not mapping risk
        if any(
            f.get("kind") == "join"
            and ("duplicated" in str(f.get("message", "")) or f.get("severity") == "error")
            for f in findings
        ) or any(f.get("kind") == "dedupe" and "duplicate" in str(f.get("message", "")) for f in findings):
            factors.append("join cardinality / duplicate keys")
        if drift in ("columns_removed", "sheets_removed", "types_changed", "unrelated_file"):
            factors.append(f"format change: {drift}")
        for c in _meaning_doubts(field_checks):
            factors.append(f"meaning check on {c['pointer']}: {c['summary']}")
        if high_impact:
            factors.append(f"{affected} of {rows_out} records ({ratio:.0%}) change vs the approved mapping")
        d.risk_factors = factors
        d.risk_level = "high" if factors else "low" if d.decision == "auto_approve" else "medium"

    def _decide(
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
        sandbox: dict[str, Any] | None = None,
        field_checks: list[dict[str, Any]] | None = None,
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
        # Input uncertainty cannot be cleared by a high model confidence or reuse of an old mapping.
        source_notices = [
            str(f.get("message", "source needs review")) for f in findings if f.get("kind") == "source_review"
        ]
        if source_notices:
            reasons.extend(source_notices)
            priority += w["warnings"] * len(source_notices)
            checks["source_review_required"] = True
        if sandbox is not None:
            checks["sandbox"] = sandbox.get("delta")

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
        sandbox_failures = self.sandbox_problems(sandbox)
        if sandbox_failures:
            return PolicyDecision("block", w.get("sandbox_failed", 60), sandbox_failures, 1.0, checks)
        changed = (sandbox or {}).get("delta") or {}
        trusted = bool(self.policy["sandbox"].get("trust_data_checks")) and _data_identical(field_checks)
        checks["data_checks_trusted"] = trusted
        if (
            self.policy["sandbox"]["enabled"]
            and not trusted
            and changed.get("changed_prices", 0) > int(self.policy["sandbox"]["max_changed_prices_auto"])
        ):
            reasons.append(
                f"sandbox: {changed['changed_prices']} prices differ from the approved mapping's output"
            )
            priority += w.get("sandbox_changed_values", 25)
        for c in _meaning_doubts(field_checks)[:3]:
            reasons.append(f"data check: {c['pointer']}: {c['summary']}")
            priority += w["critical_field_low_confidence"]
            risk += 0.3
        if changed.get("override_conflicts"):
            reasons.append(
                f"sandbox: {changed['override_conflicts']} persistent override(s) collide with the new mapping"
            )
            priority += w.get("sandbox_changed_values", 25)
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

        if (
            drift is not None
            and drift not in aa["allowed_drift"]
            and not (trusted and drift != "unrelated_file")
        ):
            reasons.append(f"schema drift: {drift}")
            priority += w.get(f"drift_{drift}", 20)
            risk += 0.2
        if other_customer and not trusted:  # identical values on this customer's own SKUs settle the owner
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
