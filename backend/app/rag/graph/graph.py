from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.rag.graph.nodes import (
    finalize_node,
    generate_direct_node,
    generate_grounded_node,
    no_answer_node,
    prepare_context_node,
    rerank_node,
    resolve_scope_node,
    retrieve_node,
    rewrite_node,
    route_node,
    select_context_path,
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
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("rerank", rerank_node)
    builder.add_node("prepare_context", prepare_context_node)
    builder.add_node("no_answer", no_answer_node)
    builder.add_node("generate_grounded", generate_grounded_node)

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
    builder.add_edge("rewrite", "retrieve")
    builder.add_edge("retrieve", "rerank")
    builder.add_edge("rerank", "prepare_context")
    builder.add_conditional_edges(
        "prepare_context",
        select_context_path,
        {
            "no_answer": "no_answer",
            "generate_grounded": "generate_grounded",
        },
    )
    builder.add_edge("no_answer", "finalize")
    builder.add_edge("generate_grounded", "finalize")
    return builder.compile()
