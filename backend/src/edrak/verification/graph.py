from langgraph.graph import END, START, StateGraph

from edrak.contracts import VerificationInput, VerificationResult
from edrak.verification.nodes import assess_findings, decide
from edrak.verification.state import VerificationState


def build_graph():
    graph = StateGraph(VerificationState)
    graph.add_node("assess_findings", assess_findings)
    graph.add_node("decide", decide)
    graph.add_edge(START, "assess_findings")
    graph.add_edge("assess_findings", "decide")
    graph.add_edge("decide", END)
    return graph.compile()


def run(payload: VerificationInput) -> VerificationResult:
    """Accept VerificationInput, return the shared VerificationResult contract."""
    final_state = build_graph().invoke(
        {
            "payload": payload,
            "assessments": [],
            "control": {},
            "decision_status": "",
            "result": {},
        }
    )
    result = VerificationResult.model_validate(final_state["result"])
    print("\n" + "=" * 72)
    print("VERIFICATION OUTPUT STATE")
    print("=" * 72)
    print(result.model_dump_json(indent=2))
    return result
