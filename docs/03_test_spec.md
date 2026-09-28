# SVC-C2-143 — Test Specification

## Strategy

Three layers, all deterministic (no LLM, no network):

- **unit** — `tests/unit/test_nodes.py`: the deterministic `ContractRenewalObligationService` (privacy
  tokenize vs provenance resolution, amendment-chain resolution, normalize, classify each renewal status +
  drivers, evidence-gap reference mapping, brief composition, renewal summary) and each node in isolation
  (S-1/S-2 hygiene + degrade, inner-node skip guards with S-4 emit, S-3 fail-closed + disclaimer gate).
- **unit (graph + real invoke)** — `tests/unit/test_graph.py`: outer GraphNode wiring (alias, subgraph
  cache, extract/merge, outer-first error_code), inner-workflow route/registration, and **end-to-end
  through the real `Graph().invoke()`** for grounded / out-of-scope / injection-degrade / oversize-degrade /
  missing-provenance / unsafe-source / PII-tokenised / forged-surrogate paths.
- **integration** — `tests/integration/test_end_to_end.py`: multi-contract portfolio prioritisation +
  grounding, mixed cited/uncited fail-closed, forged surrogate re-hash, empty → out-of-scope.

## Local result (local SDK stub)

- Core suites (`tests/unit/test_graph.py`, `tests/unit/test_nodes.py`, `tests/integration/`): **100 passed,
  1 skipped** (server import skipped when the platform module is unavailable in a local stub env),
  **coverage = 95%** (`--cov=src`, target ≥ 80%).
- Full `tests/` run: **102 passed, 3 skipped, 3 known env-diff failures** (`test_pb_invoke_order`,
  `test_framework_compliance_tc06_tc07::tc06/tc07`). These three assert framework-level `@final`
  enforcement that the local SDK stub shim does not implement; they **pass under the real SDK in CI** and are the
  unchanged scaffold conditional-stub / compliance files (byte-identical to the shipped scaffold and to the
  reference a sibling template).

## Key security test cases (real `Graph().invoke()`)

| # | Case | Expectation |
|---|------|-------------|
| TC-01 | Grounded contract in the notice window (authorized source) | `status=SUCCESS`, `status_kind=renewal_review_brief`, `notice_window_open`, amendment-chain-resolved `notice_days=45`, cited source/amendment refs, `human_review.required=True`, DRAFT disclaimer |
| TC-02 | NL text / empty | out-of-scope safe answer, `citations=[]`, disclaimer present |
| TC-03 | Injection payload | degraded `SUCCESS` (never ERROR), post ran (`PostProcessNode` in `node_history`), out-of-scope envelope, marker body absent, terminal S-4 audit carries `error_code=INJECTION_REJECTED` |
| TC-04 | Oversize (> 400 000 chars) | degraded `SUCCESS`, S-4 audit carries `error_code=INPUT_TOO_LONG` |
| TC-05 | Missing provenance | S-3 fail-closed → `needs_review`, brief body withheld, S-4 `error_code=CITATION_INCOMPLETE` |
| TC-06 | Unverifiable / unsafe source (counterparty name, phone) | `needs_review`, raw source never in output |
| TC-07 | Forged surrogate source (`src:1a2b3c4d` / `ctr:deadbeef` / `acct:deadbeef`) | `needs_review`, `citations=[]`, forged value never in output |
| TC-08 | PII / no-space-name `contract_id` (`Acme` / `TaroYamada`) | tokenized `ctr:<sha8>`, name never in output, referential integrity across brief ↔ citations |
| TC-09 | PII display fields (counterparty/signatory/contact name, phone, email) | dropped pre-LLM / redacted — none appear in output |
| TC-10 | Unknown caller field carrying PII | whitelist-by-construction — never reaches output |
| TC-11 | Mixed cited + uncited portfolio | whole grounded brief fails-closed to `needs_review` |

## Proof-of-Boundary Tests

PB-1..PB-6 are covered by the scaffold conditional-stub files (`tests/proof_of_boundary/`) + the S-4 emit /
import-isolation / state-safety assertions above. **PB-7** (HITL interrupt propagation) is **Auto-waived —
non-HITL**: `config/agent.yaml` does not set `hitl.enabled: true`; the conditional stub records 2 SKIPPED.

## Reproduce

```bash
source .venv/bin/activate
python -m pytest tests/unit/test_graph.py tests/unit/test_nodes.py tests/integration/ -q --cov=src --cov-report=term
ruff check src tests
python scripts/check_trust_level.py src/
python scripts/check_cat_consistency.py
python scripts/check_dep_pinning.py
```
