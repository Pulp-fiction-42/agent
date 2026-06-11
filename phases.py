# phases.py — phase state, prompts, permission handler

import asyncio
from claude_agent_sdk import ToolPermissionContext, PermissionResultAllow, PermissionResultDeny

phase = "plan"  # "plan" | "execute"

SYSTEM_PROMPT_PLAN = """You are a bioinformatics agent specialized in virus classification."""

SYSTEM_PROMPT_EXECUTE = """You are a bioinformatics agent specialized in virus classification."""


def make_permission_handler(send, confirm_future_holder: list):
    """
    Returns a permission handler bound to a WebSocket connection.

    send: coroutine that sends a JSON message to the client
    confirm_future_holder: a one-element list holding the current asyncio.Future
                           (mutable so the WS receive loop can resolve it)
    """
    async def handler(
        tool_name: str,
        input_data: dict,
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:

        if tool_name == "AskUserQuestion":
            # Send questions to client, await answers
            fut = asyncio.get_event_loop().create_future()
            confirm_future_holder[0] = fut
            await send({
                "type": "ask_user",
                "questions": input_data.get("questions", []),
            })
            answers = await fut  # resolved by WS receive loop
            return PermissionResultAllow(updated_input={**input_data, "answers": answers})

        # All other tools: ask Y/n confirmation
        fut = asyncio.get_event_loop().create_future()
        confirm_future_holder[0] = fut
        await send({
            "type": "tool_confirm",
            "tool": tool_name,
            "input": input_data,
        })
        allowed = await fut  # resolved by WS receive loop
        if allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message="User declined", interrupt=False)

    return handler


def system_prompt() -> str:
    return SYSTEM_PROMPT_PLAN if phase == "plan" else SYSTEM_PROMPT_EXECUTE


def switch_to_execute():
    global phase
    phase = "execute"


def switch_to_plan():
    global phase
    phase = "plan"
