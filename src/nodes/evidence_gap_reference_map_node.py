"""SVC-C2-143 — inner workflow step 2: evidence_gap_reference_map.

Deterministically maps the supplied delivery evidence against the seeded renewal-review schema's required
obligations for each classified contract, surfacing the open evidence gaps and the cited schema-clause +
effective-amendment references (``<clause_id>@<version>`` from the seeded approved schema) so the renewal
brief is grounded in authorized clauses. Skips (no-op) on rejected / 0-classified input, after emitting a
skip audit event.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import ContractRenewalObligationService
from src.utils.audit import emit_trace_event


class EvidenceGapReferenceMapNode(FunctionNode):
    """Map open evidence gaps + cited schema/amendment references per classified contract."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code") or state.get("extracted_count", 0) == 0:
            emit_trace_event(
                "evidence_gap_reference_map.skip", {"reason": state.get("error_code") or "no_extracted"}, state
            )
            return {}

        extracted = json.loads(state.get("extracted_contracts") or "[]")
        references = {c["contract_id"]: ContractRenewalObligationService.map_references(c) for c in extracted}
        emit_trace_event(
            "evidence_gap_reference_map.complete",
            {
                "contracts": len(references),
                "open_gap_count": sum(len(r["open_evidence_gaps"]) for r in references.values()),
                "source_ref_count": sum(len(r["cited_source_refs"]) for r in references.values()),
            },
            state,
        )
        return {"evidence_references": json.dumps(references, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}
