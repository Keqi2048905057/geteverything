from typing import Any, Dict, List, Optional

from storage import ScanResultStore

from .action import AgentAction


def handle_agent_message(
    message: str,
    store: Optional[ScanResultStore] = None,
    history: Optional[List[Dict[str, str]]] = None,
    debug: bool = False,
    pending_plan: Optional[Dict[str, Any]] = None,
    uploaded_context: Optional[Dict[str, Any]] = None,
    context_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    agent = AgentAction(
        store=store,
        conversation_history=history,
        debug=debug,
        pending_plan=pending_plan,
        uploaded_context=uploaded_context,
        context_state=context_state,
    )
    return agent.run(message)
