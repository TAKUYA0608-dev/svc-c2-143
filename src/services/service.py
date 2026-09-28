"""SVC-C2-143 — deterministic domain services (no framework imports, no LLM).

ContractRenewalObligationService: normalizes a supplied service-provider contract record (base service
agreement + approved amendments + delivery evidence) into a canonical renewal signal set, resolves the
**effective renewal terms across the amendment chain** (the latest amendment stating each field supersedes
the base agreement), classifies each contract's renewal status against the seeded, approved renewal-review
schema, maps the supplied delivery evidence onto the schema's required obligations to identify **open
evidence gaps**, retrieves the cited schema-clause + effective-amendment references, and composes a
candidate RenewalReviewBrief.

Everything here is deterministic and auditable (amendment-chain resolution + threshold banding + set
membership + keyed clause composition) — there is **no LLM** (no model in config/agent.yaml, no LLM
dependency in pyproject, no LLM call anywhere in src/). Records are keyed by opaque, non-reversible
``contract_id`` / ``amendment_id`` / ``evidence_id`` surrogates; the raw counterparty reference and any
free-text prose are never carried into the brief, and the S-3 output gate re-redacts anything that leaks.
The seeded renewal-review schema / obligation taxonomy are overridable by CoE (a change-controlled engineer
MR + specialist review) without touching node logic.
"""

from __future__ import annotations

import datetime
import hashlib
import re
from typing import Any

# Two SEPARATE concerns — do not conflate them:
#   (1) PRIVACY (opaque_id): every caller identifier (contract_id / amendment_id / evidence_id /
#       counterparty_id) is UNCONDITIONALLY tokenized to a deterministic, non-reversible opaque surrogate so
#       contract/counterparty PII (even a bare name like ``Acme`` / ``Taro.Yamada`` / ``TaroYamada``, no
#       spaces/symbols) can never reach a citation or the renewal brief. Tokenizing is a privacy measure — it
#       does NOT assert the value is authorized/verifiable. Surrogates are one-way hashes; graph state never
#       stores a surrogate→raw rejoin map, so the opaque ID is non-linkable back to the party.
#   (2) PROVENANCE (resolve_provenance): a caller ``source`` becomes a grounded CITATION only when it is
#       resolvable against the authorized provenance registry (names a trusted contract system of record).
#       Any other free text (a counterparty name, ``unknown``, a fabricated value, or a caller value merely
#       SHAPED like a surrogate ``src:1a2b3c4d``) is NOT verifiable provenance → it yields NO citation → S-3
#       blocks the brief as CITATION_INCOMPLETE (fail-closed). "Tokenized" is never sufficient for a citation.
# Tokenization is UNCONDITIONAL (no syntactic passthrough): a caller value merely *shaped* like a surrogate
# (``ctr:deadbeef``) is re-hashed, never trusted, so it can never forge an internal join key. Identifiers /
# provenance are resolved exactly once at S-1 (pre_process); downstream trusts that resolution verbatim.
_SAFE_TOKEN = re.compile(r"^[a-z0-9_\-]{1,64}$")

# Authorized provenance registry: the contract / document / delivery systems of record a service provider
# trusts as verifiable data sources. A caller ``source`` is accepted as a grounded citation ONLY when its
# leading namespace names one of these (the "trusted context"). This is the deploying org's / CoE's registry
# — overridable without touching node logic; it is a SEMANTIC allowlist of authorized systems, not a
# syntactic character class.
AUTHORIZED_PROVENANCE_SYSTEMS = frozenset(
    {
        "clm",
        "contract_management",
        "contract_lifecycle",
        "contract_repository",
        "contracts",
        "dms",
        "document_management",
        "sharepoint",
        "docstore",
        "docusign",
        "adobe_sign",
        "esignature",
        "ironclad",
        "agiloft",
        "conga",
        "icertis",
        "ariba",
        "sap_ariba",
        "coupa",
        "procurement",
        "sourcing",
        "delivery_evidence",
        "evidence_system",
        "servicenow",
        "jira",
        "ticketing",
        "system_of_record",
        "sor",
        "authorized_feed",
        "contract_feed",
    }
)


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def opaque_id(value: Any, prefix: str) -> str:
    """PRIVACY tokenize a caller identifier to a deterministic, non-reversible opaque surrogate
    ``<prefix>:<sha8>``.

    Caller identifiers are **always** tokenized — no syntactic passthrough — so a counterparty name (with or
    without spaces) can never survive into a citation or the brief, and a caller value merely *shaped* like a
    surrogate (``ctr:deadbeef``) is re-hashed rather than trusted (it can never forge an internal join key).
    Same input → same surrogate (brief / citations / summary stay joinable within one invocation). This is a
    privacy measure only; it makes no claim that the identifier is authorized, and no surrogate→raw rejoin
    map is ever kept.
    """
    return f"{prefix}:{_sha8(str(value or '').strip())}"


def resolve_provenance(value: Any) -> str | None:
    """Resolve a **raw** caller ``source`` to a grounded, privacy-tokenized CITATION — or ``None``.

    Provenance validation (separate from privacy) and the **single** resolution point (S-1 / pre_process).
    A citation is emitted **only** when the source names an authorized contract system of record
    (``<authorized-namespace>[:<ref>]``). Any other value — a counterparty name, ``unknown``, a fabricated
    value, **or a value that merely looks like a surrogate (``src:1a2b3c4d``)** — is not verifiable
    provenance and returns ``None`` so the S-3 gate blocks the brief as CITATION_INCOMPLETE (fail-closed).
    When authorized, the raw label is never used verbatim: the citation is a privacy hash (``src:<sha8>``) of
    the authorized reference. No synthetic provenance is fabricated.

    ★ Forged-surrogate defence: there is **no format-based passthrough**. A caller-supplied ``src:<hex>``
    has namespace ``src`` (not an authorized system of record), so it resolves to ``None`` — it is dropped
    here at S-1 and can never reach a citation. Because provenance is resolved exactly once (here), the
    produced ``src:<sha8>`` is the trusted citation downstream and is **never** fed back through this
    function (which would, correctly, reject it), so no forged value can imitate an internal surrogate.
    """
    text = str(value or "").strip()
    if not text:
        return None
    namespace = text.split(":", 1)[0].strip().lower()
    if namespace not in AUTHORIZED_PROVENANCE_SYSTEMS:
        return None  # unverifiable / forged-surrogate provenance → fail-closed (no citation → needs_review)
    return "src:" + _sha8(text)


# ── seeded renewal-status taxonomy: policy-defined status → human-readable description ──
RENEWAL_STATUS_TAXONOMY: dict[str, str] = {
    "renewal_overdue": "The stated renewal date has already passed as of the review date",
    "notice_window_open": "The renewal-notice deadline falls within the required notice window from now",
    "auto_renew_optout_window": "The contract auto-renews and the opt-out decision window is open",
    "evidence_gap": "One or more required renewal-review obligations have no approved delivery evidence",
    "on_track": "No approaching deadline and no open evidence gap was detected for this contract",
    "unclassified": "No renewal signal matched; routed for manual review",
}

# ── seeded, approved renewal-review schema (authorized obligation checklist, CoE-calibratable) ──
# clause_id@version is a stable, citable reference to the approved schema clause for each required obligation.
# Each caller contract's supplied delivery_evidence is mapped onto these obligation keys; a required
# obligation with no matching approved evidence is an OPEN EVIDENCE GAP.
RENEWAL_REVIEW_SCHEMA: dict[str, dict[str, Any]] = {
    "renewal_notice_served": {
        "label": "Renewal / non-renewal notice served within the notice window",
        "required": True,
        "clause_id": "RS-NOTICE",
        "version": "v2",
    },
    "service_level_evidence": {
        "label": "Service-level attainment evidence for the current term",
        "required": True,
        "clause_id": "RS-SLA",
        "version": "v2",
    },
    "deliverable_acceptance": {
        "label": "Signed acceptance of the contracted deliverables",
        "required": True,
        "clause_id": "RS-DELIV",
        "version": "v2",
    },
    "security_compliance_review": {
        "label": "Security / compliance review evidence for the renewal term",
        "required": True,
        "clause_id": "RS-SEC",
        "version": "v2",
    },
    "data_processing_addendum": {
        "label": "Current data-processing addendum on file",
        "required": True,
        "clause_id": "RS-DPA",
        "version": "v2",
    },
    "pricing_confirmation": {
        "label": "Confirmed renewal pricing schedule attached (owner-confirmed)",
        "required": False,
        "clause_id": "RS-PRICE",
        "version": "v2",
    },
}


def _int(value: Any, default: int | None = 0) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _iso_date(value: Any) -> datetime.date | None:
    """Deterministically parse an ISO ``YYYY-MM-DD`` date; anything else → None (no fuzzy parsing)."""
    text = str(value or "").strip()[:10]
    try:
        return datetime.date.fromisoformat(text)
    except (ValueError, TypeError):
        return None


class ContractRenewalObligationService:
    """Deterministic amendment-chain resolution, renewal classification, evidence-gap mapping, and brief
    composition."""

    # ── amendment-chain resolution ─────────────────────────────────────────────
    @staticmethod
    def _resolve_renewal_terms(agreement: dict[str, Any], amendments: list[dict[str, Any]]) -> dict[str, Any]:
        """Resolve the EFFECTIVE renewal terms across the amendment chain.

        The base service agreement supplies the starting renewal_date / notice_days / auto_renew; each
        amendment, applied in ascending ``seq`` order, supersedes any field it restates (bounded
        amendment-chain interpretation). Records which amendment last set each field so the brief is
        traceable. Deterministic — the free-text prose is never interpreted semantically.
        """
        renewal_date = _iso_date(agreement.get("renewal_date"))
        notice_days = _int(agreement.get("notice_days"), None)
        auto_renew = bool(agreement.get("auto_renew", False))
        resolved_from = {
            "renewal_date": "agreement" if renewal_date else None,
            "notice_days": "agreement" if notice_days is not None else None,
            "auto_renew": "agreement",
        }

        # Amendments are applied in a deterministic order: caller ``seq`` (int) ascending, then opaque id.
        ordered = sorted(
            (a for a in amendments if isinstance(a, dict)),
            key=lambda a: (_int(a.get("seq"), 0) or 0, str(a.get("amendment_id") or "")),
        )
        for amd in ordered:
            # resolved_from is surfaced in the brief → tokenize the amendment reference unconditionally
            # (defense-in-depth; a raw amendment_id / free-text label can never leak into the trace).
            amd_ref = opaque_id(amd.get("amendment_id"), "amd")
            if amd.get("renewal_date") is not None:
                d = _iso_date(amd.get("renewal_date"))
                if d is not None:
                    renewal_date, resolved_from["renewal_date"] = d, amd_ref
            if amd.get("notice_days") is not None:
                n = _int(amd.get("notice_days"), None)
                if n is not None:
                    notice_days, resolved_from["notice_days"] = n, amd_ref
            if amd.get("auto_renew") is not None:
                auto_renew, resolved_from["auto_renew"] = bool(amd.get("auto_renew")), amd_ref

        return {
            "renewal_date": renewal_date.isoformat() if renewal_date else None,
            "notice_days": notice_days,
            "auto_renew": auto_renew,
            "resolved_from": resolved_from,
        }

    # ── normalization ────────────────────────────────────────────────────────
    @staticmethod
    def normalize(contracts: list[dict[str, Any]], as_of: str | None = None) -> list[dict[str, Any]]:
        """Validate + canonicalize supplied contract records into a renewal signal set. Rows without a
        ``contract_id`` are dropped.

        ``contract_id`` / ``amendment_id`` / ``evidence_id`` are always privacy-tokenized. ``source`` was
        already resolved to a grounded citation (``src:<sha8>``) or ``None`` by pre_process (S-1), the single
        provenance-resolution point — a forged surrogate was dropped there. normalize trusts that value
        verbatim; it never re-resolves and never fabricates provenance.
        """
        as_of_date = _iso_date(as_of)
        out: list[dict[str, Any]] = []
        for raw in contracts or []:
            if not isinstance(raw, dict):
                continue
            raw_id = str(raw.get("contract_id") or raw.get("id") or "").strip()
            if not raw_id:
                continue
            contract_id = opaque_id(raw_id, "ctr")
            source = raw.get("source")  # already resolved (src:<sha8> or None) at S-1

            agreement: Any = raw.get("agreement") if isinstance(raw.get("agreement"), dict) else {}
            amendments: Any = raw.get("amendments") if isinstance(raw.get("amendments"), list) else []
            terms = ContractRenewalObligationService._resolve_renewal_terms(agreement, amendments)

            renewal_date = _iso_date(terms["renewal_date"])
            supplied_days = _int(raw.get("days_to_renewal"), None)
            if supplied_days is not None:
                days_to_renewal = supplied_days
            elif renewal_date is not None and as_of_date is not None:
                days_to_renewal = (renewal_date - as_of_date).days
            else:
                days_to_renewal = None

            # Evidence: map supplied delivery_evidence onto obligation keys (approved evidence only).
            evidenced_obligations: set[str] = set()
            evidence_refs: list[str] = []
            for ev in raw.get("delivery_evidence") or []:
                if not isinstance(ev, dict):
                    continue
                key = str(ev.get("obligation_key") or "").strip().lower()
                approved = bool(ev.get("approved", True))  # only approved evidence counts (default approved)
                has_ref = bool(str(ev.get("evidence_ref") or "").strip())
                if key in RENEWAL_REVIEW_SCHEMA and approved and has_ref:
                    evidenced_obligations.add(key)
                    ev_id = str(ev.get("evidence_id") or ev.get("evidence_ref") or "").strip()
                    if ev_id:
                        evidence_refs.append(opaque_id(ev_id, "evi"))

            # applicable_obligations lets the caller scope which seeded obligations apply (subset of seeded
            # keys only — unknown keys ignored). Absent → all seeded obligations apply.
            applicable = {
                str(k).strip().lower()
                for k in (raw.get("applicable_obligations") or [])
                if str(k).strip().lower() in RENEWAL_REVIEW_SCHEMA
            }
            amendment_ids = sorted(
                {
                    opaque_id(a.get("amendment_id"), "amd")
                    for a in amendments
                    if isinstance(a, dict) and a.get("amendment_id")
                }
            )

            out.append(
                {
                    "contract_id": contract_id,
                    "renewal_date": terms["renewal_date"],
                    "notice_days": terms["notice_days"],
                    "auto_renew": terms["auto_renew"],
                    "resolved_from": terms["resolved_from"],
                    "days_to_renewal": days_to_renewal,
                    "evidenced_obligations": sorted(evidenced_obligations),
                    "evidence_ref_count": len(evidence_refs),
                    "amendment_ids": amendment_ids,
                    "applicable_obligations": sorted(applicable),
                    "source": source,
                }
            )
        return out

    # ── classification ───────────────────────────────────────────────────────
    @staticmethod
    def classify(signals: dict[str, Any]) -> dict[str, Any]:
        """Classify one normalized contract into a policy-defined renewal status + matched drivers.

        Deterministic threshold / set-membership matching only — the free-text prose is never interpreted
        semantically, so prompt-like text in a supplied field can never influence the classification.
        """
        matched: list[dict[str, str]] = []
        days = signals["days_to_renewal"]
        notice = signals["notice_days"]

        open_gaps = ContractRenewalObligationService._open_gaps(signals)
        if open_gaps:
            matched.append(
                {"status": "evidence_gap", "severity": "high", "evidence": f"open_evidence_gaps={open_gaps}"}
            )
        if days is not None and days < 0:
            matched.append({"status": "renewal_overdue", "severity": "high", "evidence": f"days_to_renewal={days}"})
        elif days is not None and notice is not None and days <= notice:
            matched.append(
                {
                    "status": "notice_window_open",
                    "severity": "high",
                    "evidence": f"days_to_renewal={days}, notice_days={notice}",
                }
            )
        if signals["auto_renew"] and days is not None and notice is not None and days <= notice:
            matched.append(
                {
                    "status": "auto_renew_optout_window",
                    "severity": "med",
                    "evidence": f"auto_renew, days_to_renewal={days}, notice_days={notice}",
                }
            )

        # Deterministic primary priority (most material renewal signal first).
        priority = ("renewal_overdue", "notice_window_open", "auto_renew_optout_window", "evidence_gap")
        matched_status = next((t for t in priority if any(m["status"] == t for m in matched)), None)
        if matched_status is not None:
            primary = matched_status
        elif signals["renewal_date"] is not None or signals["days_to_renewal"] is not None:
            primary = "on_track"  # dates known, no approaching deadline / gap
        else:
            primary = "unclassified"  # no renewal signal could be computed
        rank = {
            "renewal_overdue": 4,
            "notice_window_open": 3,
            "auto_renew_optout_window": 2,
            "evidence_gap": 1,
            "on_track": 0,
            "unclassified": 0,
        }

        return {
            "contract_id": signals["contract_id"],
            "renewal_date": signals["renewal_date"],
            "notice_days": signals["notice_days"],
            "auto_renew": signals["auto_renew"],
            "days_to_renewal": signals["days_to_renewal"],
            "resolved_from": signals["resolved_from"],
            "amendment_ids": signals["amendment_ids"],
            "renewal_status": primary,
            "matched_drivers": matched,
            "open_evidence_gaps": open_gaps,
            "priority_rank": rank[primary],
            "source": signals["source"],
        }

    @staticmethod
    def _applicable_keys(signals: dict[str, Any]) -> list[str]:
        """Required seeded obligations in scope for this contract (caller subset, else all required)."""
        scoped = signals.get("applicable_obligations") or []
        keys = scoped if scoped else list(RENEWAL_REVIEW_SCHEMA.keys())
        return [k for k in keys if RENEWAL_REVIEW_SCHEMA.get(k, {}).get("required")]

    @staticmethod
    def _open_gaps(signals: dict[str, Any]) -> list[str]:
        """Required obligations (in scope) with no matching approved delivery evidence."""
        evidenced = set(signals.get("evidenced_obligations") or [])
        return sorted(k for k in ContractRenewalObligationService._applicable_keys(signals) if k not in evidenced)

    # ── evidence-gap reference mapping ─────────────────────────────────────────
    @staticmethod
    def map_references(classified: dict[str, Any]) -> dict[str, Any]:
        """Deterministically map the cited schema clauses (for each required obligation in scope) and the
        effective amendment refs for one classified contract. Clause refs are ``<clause_id>@<version>`` from
        the seeded authorized schema."""
        cited_source_refs: list[str] = []
        for key, spec in RENEWAL_REVIEW_SCHEMA.items():
            if spec.get("required"):
                cited_source_refs.append(f"{spec['clause_id']}@{spec['version']}")
        gap_refs = [
            f"{RENEWAL_REVIEW_SCHEMA[k]['clause_id']}@{RENEWAL_REVIEW_SCHEMA[k]['version']}"
            for k in classified["open_evidence_gaps"]
            if k in RENEWAL_REVIEW_SCHEMA
        ]
        return {
            "open_evidence_gaps": classified["open_evidence_gaps"],
            "cited_source_refs": cited_source_refs,
            "cited_gap_refs": gap_refs,
            "cited_amendment_refs": list(classified["amendment_ids"]),
        }

    # ── brief composition ──────────────────────────────────────────────────────
    @staticmethod
    def compose_brief(classified: dict[str, Any], refs: dict[str, Any]) -> dict[str, Any]:
        """Compose the per-contract candidate renewal-review entry (needs-review, cited).

        The entry is a candidate only — the final renewal / legal decision defers to a commercial / legal
        owner. No renewal is executed, no clause is drafted, no law is interpreted, no pricing recommended.
        """
        return {
            "contract_id": classified["contract_id"],
            "renewal_status": classified["renewal_status"],
            "renewal_status_description": RENEWAL_STATUS_TAXONOMY.get(classified["renewal_status"], ""),
            "stated_renewal_dates": [classified["renewal_date"]] if classified["renewal_date"] else [],
            "notice_requirements": (
                {"notice_days": classified["notice_days"], "auto_renew": classified["auto_renew"]}
                if classified["notice_days"] is not None or classified["auto_renew"]
                else {}
            ),
            "days_to_renewal": classified["days_to_renewal"],
            "resolved_from": classified["resolved_from"],
            "open_evidence_gaps": classified["open_evidence_gaps"],
            "matched_drivers": classified["matched_drivers"],
            "priority_rank": classified["priority_rank"],
            "cited_source_refs": refs["cited_source_refs"],
            "cited_gap_refs": refs["cited_gap_refs"],
            "cited_amendment_refs": refs["cited_amendment_refs"],
            "status_kind": "needs_review",
            "note": "Candidate renewal-review brief only — the final renewal / signing / legal "
            "interpretation is an authorized commercial / legal owner's; this agent compiles "
            "evidence and does not renew, sign, execute, or interpret.",
            "citation": classified["source"],
        }

    @staticmethod
    def renewal_summary(briefs: list[dict[str, Any]]) -> dict[str, Any]:
        """Portfolio-level rollup: contract count, status distribution, contracts needing urgent review."""
        distribution: dict[str, int] = {}
        for b in briefs:
            distribution[b["renewal_status"]] = distribution.get(b["renewal_status"], 0) + 1
        urgent = [
            b["contract_id"]
            for b in briefs
            if b["renewal_status"] in ("renewal_overdue", "notice_window_open") or b["open_evidence_gaps"]
        ]
        return {
            "total_contracts": len(briefs),
            "status_distribution": distribution,
            "contracts_needing_urgent_review": urgent,
        }
