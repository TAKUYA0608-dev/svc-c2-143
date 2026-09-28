# Template Design Specification — SVC-C2-143

Service Provider Contract Renewal Obligation Evidence Brief (Cat 2, GraphNode-in-main).

## Position in AgentCore Architecture

- **Agent Class**: `ServiceProviderContractRenewalObligationEvidenceBriefAgent` (module-level alias of `Graph`)
- **L1 Base**: AgentBaseGraph (L1 direct — Cat 2 GraphNode-in-main; **not** AutonomousBaseGraph). The
  `DocGenerationAgent` L2 pattern is a design reference only; the workflow is implemented directly on
  AgentBaseGraph (2026-05-18 L2-deprecation ruling).
- **Category**: Cat 2 — orchestrates a fixed multi-step workflow to produce one job-to-be-done deliverable
  (a RenewalReviewBrief for a supplied service-provider contract renewal record).
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible); complex fields are JSON strings (ADR-005)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only — no `config` param)
  - Graph: composition (`register_nodes()` for node substitution; domain complexity behind a `GraphNode`)
- **LLM**: none. The template is **fully deterministic** (amendment-chain resolution + set-membership
  coverage matching + keyed clause composition against a seeded approved renewal-review schema / obligation
  taxonomy). There is no model in `config/agent.yaml`, no LLM dependency in `pyproject.toml`, and no LLM call
  anywhere in `src/`. "pre-LLM" in the S-2 discussion below therefore means "before any downstream node
  reads the supplied free-text contract prose".

## Architecture Overview

### Node Configuration (outer 5-slot backbone)

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | schema/session/trust setup | user_input | caller_trust_level, session_id | InitializeNode (default) |
| pre_process | `ContractRecordIngest` + `SensitiveDataDetectAndMinimise` — S-1 normalisation (NFKC, size cap) + S-2 pre-LLM sensitive-data minimisation. **injection/oversize → degraded `SUCCESS + error_code`, offending body discarded (never `status=ERROR`)**. Field-level input hygiene (credential/My-Number/email/phone redaction); **counterparty / personnel PII display fields dropped**; identifiers (`contract_id`/`amendment_id`/`evidence_id`/`counterparty_id`) **UNCONDITIONALLY tokenized to opaque, non-reversible surrogates** (a bare name is opaque like any value, no surrogate→raw rejoin map kept); **provenance `source` resolved to a citation ONLY if it names an authorized contract system of record (privacy-tokenized `src:<sha8>`), else dropped to `None`** — S-3 then blocks; `scope`/`period` hygiened | user_input | validated_input, input_format, enriched_context, (error_code) | PreProcessNode (FunctionNode) |
| main | `RenewalObligationBriefWorkflowGraphNode` — wraps inner `RenewalObligationBriefWorkflow` (composition criterion #9) | validated_input | result, extracted_count, human_review_required, (error_code), status | GraphNode (subgraph) |
| post_process | `OutputSanitise` — S-3 output gate: **fail-closed per-contract citation completeness** (ungrounded brief → `needs_review` degrade, brief body withheld, `error_code=CITATION_INCOMPLETE`) + counterparty-name/company/phone/email/credential/My-Number re-redaction + DRAFT disclaimer, S-4 no-persist audit | result | formatted_output, disclaimer, audit_logged, (error_code) | PostProcessNode (FunctionNode) |
| finalize | build response envelope | formatted_output | output, status | FinalizeNode (default) |

### Inner workflow (`src/graph/domain_workflow_graph.py` — BaseGraph, linear + per-node skip guard)

```
START → renewal_obligation_extract → evidence_gap_reference_map → renewal_brief_compose → human_gate → END
```

| Inner Node | Responsibility | Skip guard |
|------|---------------|-----------|
| renewal_obligation_extract | Deterministic ingest + normalize of the supplied contract records; resolve the **effective renewal terms across the amendment chain** (the latest amendment stating each field supersedes the base agreement — the bounded amendment-chain resolution is the Agent-value core; the date/notice arithmetic is the deterministic Tool part) against the seeded approved renewal-review schema; classify each contract's renewal status (renewal_overdue / notice_window_open / auto_renew_optout_window / evidence_gap / on_track) with matched drivers + evidence; set `extracted_count`. **0 valid contracts → `error_code=NO_CONTRACTS` → out-of-scope safe answer**. Free-text contract prose is never interpreted semantically | — (first node; emits `.skip` on rejected/no-contract input) |
| evidence_gap_reference_map | Deterministically map supplied delivery evidence against the seeded renewal-review schema's required obligations → identify **open evidence gaps** (required obligations with no matching approved evidence); retrieve the cited schema clause refs (`<clause_id>@<version>`) + effective amendment refs, grounding the brief in authorized clauses | no-op `return {}` (after `.skip` emit) on `error_code` / `extracted_count == 0` |
| renewal_brief_compose | Compose the RenewalReviewBrief deliverable: per contract, the stated renewal dates, notice requirements, resolved renewal obligations, open evidence gaps, cited source/amendment refs, a needs-review mark, and the source citation; ordered by renewal urgency. On 0-contract/rejected → out-of-scope safe answer | emits safe answer on `error_code` / no contracts |
| human_gate | Deterministic **HumanApprovalGate**: mark `human_review_required=True` + `review_status="pending_human_approval"`, record material renewal decisions (contracts approaching a renewal/notice deadline, or with open evidence gaps, that require an authorized commercial/legal owner's sign-off before any renewal action) into the brief. The renewal / signing / legal interpretation is **never** made by the agent | no-op `return {}` (after `.skip` emit) on `error_code` / `extracted_count == 0` (safe answer needs no human gate) |

`RenewalObligationBriefWorkflowGraphNode.get_subgraph()` caches the compiled inner workflow on the **class attribute**
(`RenewalObligationBriefWorkflowGraphNode._subgraph`, not `self` — avoids mutable node-instance state per §9; built once; `BaseGraph.invoke()` `_ensure_compiled` is idempotent). `extract_input()`
passes `validated_input` into the inner graph; `merge_output()` surfaces `result / extracted_count /
human_review_required / error_code / status` — with **`error_code` OUTER-first** (`state.get("error_code")
or sub_result.get("error_code")`) so a pre-stage rejection survives to the terminal S-4 audit (the inner
workflow runs on the discarded body and would otherwise overwrite it with `NO_CONTRACTS`).

## Security Model (S-1 … S-5)

- **S-1 (input normalisation + field hygiene)**: NFKC + control-char strip + size cap; every string written
  into `validated_input` is passed through credential/My-Number/email/phone redaction; identifiers are
  tokenized to opaque surrogates; provenance resolved exactly once here.
- **S-2 (pre-LLM sensitive-data minimisation + injection containment, contract-data)**: counterparty /
  personnel PII display fields (signatory / contact name, email, phone, address) are **dropped entirely —
  not masked** — before the workflow runs, so they are never carried into a citation or the brief. Opaque
  IDs are non-reversible one-way hashes with **no surrogate→raw rejoin map in graph state**; the S-4 audit
  references only minimised counts. **Injection containment is pre-LLM**: source prose is treated as quoted
  data (never as an embedded instruction), and prompt-injection markers or oversize input degrade to a safe
  out-of-scope answer *without any semantic execution of the offending text*. Pre-LLM containment (S-2) and
  the S-3 output sanitiser are separately tested.
  - **Degraded contract (SDK 1.0.0)**: an S-2 rejection is surfaced as **`status=SUCCESS` + `error_code`**
    (`INJECTION_REJECTED` / `INPUT_TOO_LONG` / `INPUT_REJECTED`) with the offending body discarded — it is
    **never `status=ERROR`** (which would short-circuit `route()` straight to `finalize`, skipping
    `post_process` and thus the disclaimer / S-3 redaction / S-4 audit). `post_process` therefore always
    runs and always delivers the out-of-scope safe answer + disclaimer + audit. `_extra_security_gate_input`
    MUST NOT raise and MUST `return dict(state)`; `execute()` re-checks the same conditions because the
    local stub framework does not invoke the `@final` hook.
- **S-3 (output gate, fail-closed)**: enforce per-contract citation completeness — a grounded
  RenewalReviewBrief in which any contract lacks a verifiable `source` citation is **never presented**;
  it degrades to `needs_review` with the brief body withheld (`error_code=CITATION_INCOMPLETE`, still
  SUCCESS). The citation allow-list is closed: a candidate `source`/amendment ref becomes a citation only
  when the deterministic provenance resolver accepted it; an unsupported reference degrades to needs-review.
  Re-redact any leaked secret/contact/company-name pattern (defense-in-depth). Append the mandatory DRAFT
  advisory disclaimer. `_extra_security_gate_output` receives the `execute()` result delta and MAY raise to
  block an output missing the disclaimer.
- **S-4 (audit, no-persist)**: every node `execute()` path — including every skip/0-count/degraded branch —
  emits a count-only domain trace event via `src.utils.audit.emit_trace_event`; payloads carry counts /
  status distribution / obligation keys / error codes only (no counterparty name, contract prose, or
  personnel PII). The raw record and any PII are not retained.
- **S-5 (rate limit / abuse)**: enforced at the platform entry point; the agent is read-only and performs
  no external write.

## Read-only / non-execution boundary

The agent **never** renews, signs, or executes a contract, changes a commitment, drafts a new clause,
recommends pricing, interprets law, or contacts / negotiates with a counterparty. All output is
**candidate / needs-review**, and the final renewal / legal decision is always an authorized commercial /
legal owner's, gated by the HumanApprovalGate. The seeded renewal-review schema / obligation taxonomy are a
CoE-calibratable design default (a change-controlled engineer MR + specialist review), not runtime-editable
operational actions. External dependencies (schema / KB / policy) are read-only inputs; the pipeline is
linear (no agent-to-agent cycle).

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: framework/ and shared/ only (no agents/base/ required)

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | Fixed multi-step workflow, not an autonomous loop (Cat 2) |
| Composition pattern | GraphNode (subgraph) | Standalone | **GraphNode (subgraph)** | Cat 2 domain complexity is encapsulated in the inner `RenewalObligationBriefWorkflow` |
| Degraded signalling | status=ERROR | SUCCESS + error_code | **SUCCESS + error_code** | ERROR short-circuits post_process; disclaimer + S-3 + S-4 must always run |
| Provenance/identifier | syntactic passthrough | unconditional tokenize + authorized-registry resolve | **tokenize + authorized-registry** | forged-surrogate defence; a name can never reach a citation |

## Open Items (Stage ③ implementation plan)

The design MR ships `docs/02` + `src/schemas/state.py` only. The Stage ③ implementation MR adds: the six
node implementations (pre_process, the four inner nodes, post_process), the inner/outer graph wiring
(`get_subgraph` caching + `merge_output` outer-first error_code), the deterministic
`ContractRenewalObligationService` (seeded renewal-review schema + obligation taxonomy +
amendment-chain resolution + provenance/opaque-id helpers), `src/utils/audit.py` (S-4 shim), the
`ServiceProviderContractRenewalObligationEvidenceBriefAgent = Graph` registry alias, and the unit /
integration / real-invoke tests (including the forged-surrogate, missing-provenance, and PII-tokenisation
regressions). Seeded schema / obligation taxonomy are CoE-calibratable via a change-controlled engineer MR
+ specialist review — they are not runtime-editable operational actions.
