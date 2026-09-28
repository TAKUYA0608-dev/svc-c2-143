"""SVC-C2-143 — inner workflow step 3: renewal_brief_compose.

Composes the **RenewalReviewBrief** deliverable: a renewal summary, and a per-contract entry (renewal
status, stated renewal dates, notice requirements, resolved-from amendment trace, open evidence gaps, cited
source / amendment clauses, needs-review mark), each cited to its source record. Contracts are ordered by
renewal urgency (overdue / notice-window first). The brief is candidate / advisory only — it never renews,
signs, or executes a contract, drafts a clause, interprets law, recommends pricing, or contacts a
counterparty. On the 0-contract / rejected branch it emits the out-of-scope safe answer.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import ContractRenewalObligationService
from src.utils.audit import emit_trace_event

_OUT_OF_SCOPE = (
    "整理可能なサービス契約の更新レコードが入力に見つかりませんでした。"
    "contracts 配列に contract_id と agreement（renewal_date・notice_days・auto_renew）、amendments、"
    "delivery_evidence（obligation_key・evidence_ref）等を含む JSON をご指定いただくか、"
    "対象範囲・期間・as_of（基準日）を明確にしてください。"
)


class RenewalBriefComposeNode(FunctionNode):
    """Compose the RenewalReviewBrief deliverable with citations (or safe answer on 0-contract)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        extracted = json.loads(state.get("extracted_contracts") or "[]")
        if state.get("error_code") or not extracted:
            emit_trace_event("renewal_brief_compose.safe", {"reason": state.get("error_code") or "no_extracted"}, state)
            report: dict[str, Any] = {
                "status_kind": "out_of_scope",
                "message": _OUT_OF_SCOPE,
                "renewal_summary": {},
                "renewal_briefs": [],
                "citations": [],
            }
            return {"result": json.dumps(report, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}

        references = json.loads(state.get("evidence_references") or "{}")
        briefs: list[dict[str, Any]] = []
        citations: list[dict[str, str]] = []
        for c in extracted:
            refs = references.get(
                c["contract_id"], {"cited_source_refs": [], "cited_gap_refs": [], "cited_amendment_refs": []}
            )
            briefs.append(ContractRenewalObligationService.compose_brief(c, refs))
            citations.append({"contract_id": c["contract_id"], "source": c["source"]})
        briefs.sort(key=lambda b: (-b["priority_rank"], -len(b["open_evidence_gaps"]), b["contract_id"]))

        summary = ContractRenewalObligationService.renewal_summary(briefs)
        report = {
            "status_kind": "renewal_review_brief",
            "scope": self._scope(state),
            "renewal_summary": summary,
            "renewal_briefs": briefs,
            "citations": citations,
        }
        emit_trace_event(
            "renewal_brief_compose.complete",
            {
                "contract_count": len(briefs),
                "open_gap_count": sum(len(b["open_evidence_gaps"]) for b in briefs),
                "citation_count": len(citations),
            },
            state,
        )
        return {"result": json.dumps(report, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}

    @staticmethod
    def _scope(state: dict[str, Any]) -> dict[str, Any]:
        slots = json.loads(state.get("validated_input") or "{}")
        return {"scope": slots.get("scope"), "period": slots.get("period"), "as_of": slots.get("as_of")}
