"""SVC-C2-143 — inner workflow step 1: renewal_obligation_extract.

Deterministic ingest + normalization of the supplied contract records, resolving the effective renewal
terms across the amendment chain (latest amendment stating each field supersedes the base agreement), then
per-contract classification into a policy-defined renewal status (renewal_overdue / notice_window_open /
auto_renew_optout_window / evidence_gap / on_track / unclassified) against the seeded approved renewal-review
schema, with matched drivers + evidence. Sets ``extracted_count``. **0 valid contracts (rejected input,
non-JSON text, or all rows missing contract_id) routes to the out-of-scope safe answer** — the agent never
fabricates a renewal obligation for data it did not receive. The free-text prose is never interpreted
semantically, so prompt-like text in a supplied field cannot influence the extraction.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import ContractRenewalObligationService
from src.utils.audit import emit_trace_event


class RenewalObligationExtractNode(FunctionNode):
    """Ingest + normalize supplied contracts and classify each into a policy-defined renewal status."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # Contracts arrive already validated + provenance-resolved by pre_process (S-1): each `source` is a
        # grounded citation `src:<sha8>` or None (a forged surrogate was dropped at S-1). We do not re-run
        # provenance here — normalize trusts that single upstream resolution.
        slots = json.loads(state.get("validated_input") or state.get("user_input") or "{}")
        if not isinstance(slots, dict):
            slots = {}
        canonical = json.dumps(slots, ensure_ascii=False)
        contracts = slots.get("contracts") if isinstance(slots.get("contracts"), list) else []

        if state.get("error_code") or not contracts:
            emit_trace_event(
                "renewal_obligation_extract.skip", {"reason": state.get("error_code") or "no_contracts"}, state
            )
            return {
                "validated_input": canonical,
                "extracted_contracts": "[]",
                "extracted_count": 0,
                "error_code": state.get("error_code") or "NO_CONTRACTS",
                "status": AgentStatus.SUCCESS.value,
            }

        normalized = ContractRenewalObligationService.normalize(contracts, as_of=slots.get("as_of"))
        if not normalized:
            emit_trace_event("renewal_obligation_extract.skip", {"reason": "all_malformed"}, state)
            return {
                "validated_input": canonical,
                "extracted_contracts": "[]",
                "extracted_count": 0,
                "error_code": "NO_CONTRACTS",
                "status": AgentStatus.SUCCESS.value,
            }

        extracted = [ContractRenewalObligationService.classify(s) for s in normalized]
        distribution: dict[str, int] = {}
        for c in extracted:
            distribution[c["renewal_status"]] = distribution.get(c["renewal_status"], 0) + 1
        emit_trace_event(
            "renewal_obligation_extract.complete",
            {"supplied": len(contracts), "extracted": len(extracted), "status_distribution": distribution},
            state,
        )
        return {
            "validated_input": canonical,
            "extracted_contracts": json.dumps(extracted, ensure_ascii=False),
            "extracted_count": len(extracted),
            "status": AgentStatus.SUCCESS.value,
        }
