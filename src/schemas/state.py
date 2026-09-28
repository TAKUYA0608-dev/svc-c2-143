"""SVC-C2-143 — Agent state (Service Provider Contract Renewal Obligation Evidence Brief, Cat 2).

ADR-005: State is a flat TypedDict — never a validation/BaseModel instance. Complex fields are stored
as JSON strings (``NotRequired[str]`` + ``# JSON:``); nodes ``json.dumps`` on write / ``json.loads`` on read.

Read-only / advisory: the agent ingests a supplied contract record (service agreement + approved
amendments + delivery evidence + an applicable renewal-review checklist), resolves the effective renewal
obligations across the amendment chain against the seeded, approved renewal-review schema, identifies open
evidence gaps, and composes a candidate **RenewalReviewBrief** deliverable — it never renews, signs, or
executes a contract, changes a commitment, interprets law, recommends pricing, or contacts a counterparty.
The final renewal / legal decision is always an authorized commercial / legal owner's, and the brief output
is candidate / needs-review only.

All agent-specific fields are NotRequired (populated progressively; absent at empty-start invoke).
"""

from __future__ import annotations


from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Agent state for the contract-renewal obligation extraction + evidence-brief composition workflow."""

    # ── pre_process (ContractRecordIngest + SensitiveDataDetectAndMinimise; S-1 + S-2 pre-LLM) ──
    validated_input: str  # JSON: {contracts[], scope, period} (counterparty/personnel PII minimised)
    input_format: str  # "json" | "text" | "empty" | "rejected"
    enriched_context: str  # JSON: {source, channel} (read-only caller context)

    # ── inner workflow (renewal_obligation_extract → evidence_gap_reference_map → renewal_brief_compose → human_gate) ─
    extracted_contracts: str  # JSON: [{contract_id, renewal_terms, obligations[], source}]
    extracted_count: int  # contracts with extractable renewal obligations (0 → out-of-scope safe answer)
    evidence_references: str  # JSON: {contract_id: {open_evidence_gaps[], cited_source_refs[], cited_amendment_refs[]}}
    result: str  # JSON: assembled RenewalReviewBrief (incl. human_review)
    human_review_required: bool  # True once the HumanApprovalGate flags material renewal decisions
    review_status: str  # "pending_human_approval" | "not_required"

    # ── post_process (OutputSanitise — S-3 gate + S-4 audit) ──────────────────
    formatted_output: str  # JSON: final response envelope (brief + disclaimer)
    disclaimer: str  # mandatory DRAFT / advisory-only disclaimer
    audit_logged: bool  # True once the terminal audit event is emitted

    # ── degraded-path signalling (SUCCESS + error_code, never status=ERROR) ───
    # INPUT_REJECTED | INJECTION_REJECTED | INPUT_TOO_LONG | NO_CONTRACTS | CITATION_INCOMPLETE
    error_code: str
    error_message: str  # operator-facing detail
