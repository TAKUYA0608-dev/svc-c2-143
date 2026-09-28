"""SVC-C2-143 — inner domain workflow graph (Cat 2).

Instantiated by RenewalObligationBriefWorkflowGraphNode.get_subgraph() in graph.py. Linear topology with
per-node skip guards (the portable Cat 2 form; conditional edges don't propagate across the subgraph
boundary):

    START → renewal_obligation_extract → evidence_gap_reference_map → renewal_brief_compose → human_gate → END

On rejected / 0-contract input, renewal_obligation_extract sets extracted_count=0 (+error_code);
evidence_gap_reference_map and human_gate no-op and renewal_brief_compose emits the out-of-scope safe answer
— no fabricated brief.
"""

from __future__ import annotations
from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState

from src.nodes.evidence_gap_reference_map_node import EvidenceGapReferenceMapNode
from src.nodes.human_gate_node import HumanGateNode
from src.nodes.renewal_brief_compose_node import RenewalBriefComposeNode
from src.nodes.renewal_obligation_extract_node import RenewalObligationExtractNode
from src.schemas.state import State


class RenewalObligationBriefWorkflow(BaseGraph):
    """Inner graph: renewal_obligation_extract → evidence_gap_reference_map → renewal_brief_compose → human_gate."""

    @property
    def name(self) -> str:
        return "RenewalObligationBriefWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        pass

    def register_nodes(self) -> None:
        # No super() — BaseGraph.register_nodes() is abstract.
        self._nodes["renewal_obligation_extract"] = RenewalObligationExtractNode()
        self._nodes["evidence_gap_reference_map"] = EvidenceGapReferenceMapNode()
        self._nodes["renewal_brief_compose"] = RenewalBriefComposeNode()
        self._nodes["human_gate"] = HumanGateNode()

    def add_edges(self) -> None:
        # Static linear backbone; the 0-contract / rejected skip is handled by per-node guards.
        self._sg.add_edge(START, "renewal_obligation_extract")
        self._sg.add_edge("renewal_obligation_extract", "evidence_gap_reference_map")
        self._sg.add_edge("evidence_gap_reference_map", "renewal_brief_compose")
        self._sg.add_edge("renewal_brief_compose", "human_gate")
        self._sg.add_edge("human_gate", END)

    def route(self, state: AgentState) -> str:
        """Required by the BaseGraph ABC. Linear topology → not wired to a conditional edge."""
        if state.get("error_code") or state.get("extracted_count", 0) == 0:
            return "renewal_brief_compose"
        return "evidence_gap_reference_map"

    def get_output(self, state: AgentState) -> dict[str, Any]:
        return {
            "output": state.get("result"),
            "status": state.get("status"),
            "extracted_count": state.get("extracted_count", 0),
            "human_review_required": state.get("human_review_required", False),
            "error_code": state.get("error_code"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
