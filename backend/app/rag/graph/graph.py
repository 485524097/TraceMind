from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.rag.graph.nodes import (
    finalize_node,
    generate_direct_node,
    rag_not_implemented_node,
    resolve_scope_node,
    rewrite_node,
    route_node,
    select_route,
)
from app.rag.graph.state import RagRuntimeContext, RagState


def build_rag_graph() -> CompiledStateGraph[
    RagState,
    RagRuntimeContext,
    RagState,
    RagState,
]:
    builder = StateGraph(RagState, context_schema=RagRuntimeContext)
    builder.add_node("route", route_node)
    builder.add_node("generate_direct", generate_direct_node)
    builder.add_node("finalize", finalize_node)
    builder.add_node("resolve_scope", resolve_scope_node)
    builder.add_node("rewrite", rewrite_node)
    builder.add_node("rag_not_implemented", rag_not_implemented_node)

    builder.add_edge(START, "route")
    builder.add_conditional_edges(
        "route",
        select_route,
        {
            "direct": "generate_direct",
            "rag": "resolve_scope",
        },
    )
    builder.add_edge("generate_direct", "finalize")
    builder.add_edge("finalize", END)
    builder.add_edge("resolve_scope", "rewrite")
    builder.add_edge("rewrite", "rag_not_implemented")
    builder.add_edge("rag_not_implemented", END)
    return builder.compile()
