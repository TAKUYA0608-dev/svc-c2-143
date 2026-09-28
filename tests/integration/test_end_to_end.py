# SVC-C2-143 — Integration: full outer Graph().invoke() across a multi-contract renewal portfolio

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph

_SUCCESS = AgentStatus.SUCCESS.value

_FULL_EVIDENCE = [
    {"evidence_id": f"e-{k}", "obligation_key": k, "evidence_ref": f"R-{k}", "approved": True}
    for k in ("renewal_notice_served", "service_level_evidence", "deliverable_acceptance",
              "security_compliance_review", "data_processing_addendum")]


def _invoke(user_input: str):
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
    return Graph().invoke(user_input, ctx=ctx)


def test_multi_contract_portfolio_prioritised_and_grounded():
    payload = {
        "as_of": "2026-08-01",
        "scope": "emea-renewals",
        "contracts": [
            {"contract_id": "c1", "source": "clm:c1",
             "agreement": {"renewal_date": "2027-09-01", "notice_days": 30},
             "delivery_evidence": _FULL_EVIDENCE},                                   # on_track (far off)
            {"contract_id": "c2", "source": "docusign:c2",
             "agreement": {"renewal_date": "2026-07-20", "notice_days": 30},
             "delivery_evidence": _FULL_EVIDENCE},                                   # renewal_overdue
            {"contract_id": "c3", "source": "ironclad:c3",
             "agreement": {"renewal_date": "2027-01-01", "notice_days": 30},
             "delivery_evidence": []},                                              # evidence_gap
        ],
    }
    out = _invoke(json.dumps(payload))
    assert out["status"] == _SUCCESS
    env = json.loads(out["output"])
    assert env["status_kind"] == "renewal_review_brief"
    assert len(env["renewal_briefs"]) == 3 and len(env["citations"]) == 3
    # overdue contract is ranked first (highest priority)
    assert env["renewal_briefs"][0]["renewal_status"] == "renewal_overdue"
    # summary rolls up the status distribution and the urgent-review list
    assert env["renewal_summary"]["total_contracts"] == 3
    assert env["renewal_briefs"][0]["contract_id"] in env["renewal_summary"]["contracts_needing_urgent_review"]
    assert env["human_review"]["required"] is True
    assert "DRAFT" in env["disclaimer"]


def test_mixed_cited_and_uncited_blocks_whole_brief():
    """Per-contract citation completeness: one uncited contract fails-closed the whole grounded brief."""
    payload = {"as_of": "2026-08-01", "contracts": [
        {"contract_id": "c1", "source": "clm:c1",
         "agreement": {"renewal_date": "2026-08-15", "notice_days": 30},
         "delivery_evidence": _FULL_EVIDENCE},                     # cited
        {"contract_id": "c2",
         "agreement": {"renewal_date": "2026-08-15", "notice_days": 30},
         "delivery_evidence": _FULL_EVIDENCE},                     # uncited (no source)
    ]}
    out = _invoke(json.dumps(payload))
    env = json.loads(out["output"])
    assert env["status_kind"] == "needs_review"
    assert env["renewal_briefs"] == [] and env["citations"] == []


def test_forged_contract_id_surrogate_rehashed():
    # ★ a caller value SHAPED like an internal surrogate (ctr:deadbeef) is re-hashed at S-1 (no syntactic
    # passthrough), so it can never forge an internal join key / reference another contract.
    payload = {"as_of": "2026-08-01", "contracts": [
        {"contract_id": "ctr:deadbeef", "source": "clm:c1",
         "agreement": {"renewal_date": "2026-08-15", "notice_days": 30},
         "delivery_evidence": _FULL_EVIDENCE},
    ]}
    out = _invoke(json.dumps(payload))
    env = json.loads(out["output"])
    assert env["status_kind"] == "renewal_review_brief"
    tok = env["renewal_briefs"][0]["contract_id"]
    assert tok.startswith("ctr:") and tok != "ctr:deadbeef"   # re-hashed, not passthrough
    assert "ctr:deadbeef" not in out["output"]


def test_empty_object_is_out_of_scope():
    out = _invoke(json.dumps({"contracts": []}))
    env = json.loads(out["output"])
    assert env["status_kind"] == "out_of_scope" and env["citations"] == []
