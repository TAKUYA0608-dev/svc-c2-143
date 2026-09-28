"""SVC-C2-143 — inner workflow step 4: human_gate (HumanApprovalGate).

Deterministic human-in-the-loop gate. It does **not** execute anything and it never renews, signs, or
interprets a contract — it flags the material renewal decisions (contracts that are overdue, inside the
notice window, in an auto-renew opt-out window, or with open evidence gaps) that require an authorized
commercial / legal owner's sign-off before any renewal action, records them + the review status into the
brief, and sets ``human_review_required``. Skips (no-op) on the rejected / 0-contract safe-answer branch
(no human gate needed) after emitting a skip audit event.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.utils.audit import emit_trace_event

_MATERIAL_STATUSES = frozenset({"renewal_overdue", "notice_window_open", "auto_renew_optout_window"})


class HumanGateNode(FunctionNode):
    """Flag material renewal decisions requiring authorized human owner approval; set human_review_required."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        report = json.loads(state.get("result") or "{}")
        if (
            state.get("error_code")
            or state.get("extracted_count", 0) == 0
            or report.get("status_kind") != "renewal_review_brief"
        ):
            emit_trace_event("human_gate.skip", {"reason": state.get("error_code") or "no_brief"}, state)
            return {
                "human_review_required": False,
                "review_status": "not_required",
                "status": AgentStatus.SUCCESS.value,
            }

        material: list[dict[str, Any]] = []
        for brief in report.get("renewal_briefs", []):
            # Any approaching renewal/notice deadline, auto-renew opt-out window, or open evidence gap needs a
            # commercial/legal owner before any renewal action. The agent compiles; it never renews.
            if brief["renewal_status"] in _MATERIAL_STATUSES or brief["open_evidence_gaps"]:
                material.append(
                    {
                        "contract_id": brief["contract_id"],
                        "renewal_status": brief["renewal_status"],
                        "open_evidence_gaps": brief["open_evidence_gaps"],
                        "reason": "Approaching renewal/notice deadline or open evidence gap — requires authorized "
                        "commercial/legal owner sign-off before any renewal / signing / legal decision",
                    }
                )

        required = bool(material)
        review = {
            "required": required,
            "status": "pending_human_approval" if required else "not_required",
            "note": "Any renewal, signing, execution, or legal interpretation must be confirmed by an "
            "authorized human commercial / legal owner. This agent produces a candidate "
            "renewal-review evidence brief only.",
            "material_decisions": material,
        }
        report["human_review"] = review
        emit_trace_event(
            "human_gate.complete", {"review_required": required, "material_decision_count": len(material)}, state
        )
        return {
            "result": json.dumps(report, ensure_ascii=False),
            "human_review_required": required,
            "review_status": review["status"],
            "status": AgentStatus.SUCCESS.value,
        }
