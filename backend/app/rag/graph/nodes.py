from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from app.rag.graph.state import RagRuntimeContext, RagState
from app.services.query_router import RouteMode, route_query

DIRECT_SYSTEM_PROMPT = """你是 TraceMind，一个本地优先的个人工程知识助手。
当前消息是简单社交表达，不需要检索知识库。请用简洁、自然的中文回应。
不要声称已经检索资料，不要虚构来源，也不要添加 Citation。"""


def route_node(state: RagState) -> dict[str, RouteMode]:
    return {"route_mode": route_query(state["query"])}


def select_route(state: RagState) -> RouteMode:
    return state["route_mode"]


async def generate_direct_node(
    state: RagState,
    runtime: Runtime[RagRuntimeContext],
) -> dict[str, str]:
    response = await runtime.context.model.ainvoke(
        [
            SystemMessage(content=DIRECT_SYSTEM_PROMPT),
            HumanMessage(content=state["query"]),
        ]
    )
    return {"answer": response.text}


def finalize_node(state: RagState) -> dict[str, str]:
    if "answer" not in state:
        raise ValueError("Direct generation did not produce an answer")
    return {"terminal_status": "completed"}


def rag_not_implemented_node(state: RagState) -> dict[str, str]:
    return {"terminal_status": "rag_pending"}
