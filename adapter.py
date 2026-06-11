# adapter.py — OpenAI-compatible API adapter for the BioAgent runtime

import json
import os
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, ResultMessage, PermissionResultAllow
from claude_agent_sdk.types import StreamEvent

import phases

# Initialize FastAPI application
app = FastAPI()

class ChatRequest(BaseModel):
    """Represents a chat completion request from the OpenAI API."""
    model: str = "BioAgent"
    messages: list[dict]
    stream: bool = True


async def auto_allow(tool_name: str, input_data: dict, context) -> PermissionResultAllow:
    """Permission handler that allows all tool requests automatically."""
    return PermissionResultAllow()


def sse(chunk: str) -> str:
    """Wrap a text chunk in OpenAI SSE format (Server-Sent Events).

    Args:
        chunk (str): The text chunk to send to the client.

    Returns:
        str: Formatted SSE data line.
    """
    payload = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",  # Generate a unique short ID for the chunk
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "BioAgent",
        "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
    }
    return f"data: {json.dumps(payload)}\n\n"


@app.get("/v1/models")
@app.get("/models")
async def list_models():
    """
    GET /v1/models

    Lists available models in an OpenAI-compatible format.

    Returns:
        dict: List of available models with ownership metadata.
    """
    return {
        "object": "list",
        "data": [{"id": "BioAgent", "object": "model", "owned_by": "local"}],
    }


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat(req: ChatRequest):
    """
    POST /v1/chat/completions

    Handles chat completion requests, streaming responses following OpenAI SSE format.

    Args:
        req (ChatRequest): The chat request payload with message history and configuration.

    Returns:
        StreamingResponse: FastAPI response that streams partial completions as SSE.
    """
    prompt = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in req.messages
    )

    # Handle /execute phase switch command
    last_user = next((m["content"].strip() for m in reversed(req.messages) if m["role"] == "user"), "")
    if last_user == "/execute":
        phases.switch_to_execute()
        async def switch_stream():
            yield sse("✅ Switched to Execute phase. Tools are now enabled.")
            yield "data: [DONE]\n\n"
        return StreamingResponse(switch_stream(), media_type="text/event-stream")

    if last_user == "/plan":
        phases.switch_to_plan()
        async def plan_stream():
            yield sse("✅ Switched to Plan phase. Tools are now disabled.")
            yield "data: [DONE]\n\n"
        return StreamingResponse(plan_stream(), media_type="text/event-stream")

    api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("MINIMAX_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="Set ANTHROPIC_AUTH_TOKEN or MINIMAX_API_KEY before starting the adapter.",
        )

    # Construct ClaudeAgentOptions, including dynamic system prompt per phase
    options = ClaudeAgentOptions(
        system_prompt=phases.system_prompt(),
        can_use_tool=auto_allow,         # All tool uses are automatically permitted in this adapter
        permission_mode="bypassPermissions",  # This disables user confirmation for tool use
        model="MiniMax-M3",
        include_partial_messages=True,
        env={
            "ANTHROPIC_BASE_URL": "https://api.minimaxi.com/anthropic",
            "ANTHROPIC_AUTH_TOKEN": api_key,
        },
    )

    async def stream():
        """
        Async generator that streams partial completions as SSE to the client.
        """
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            # Iterate over streaming response events from agent
            async for event in client.receive_response():
                if isinstance(event, StreamEvent):
                    e = event.event
                    # Check for content block delta events (partial output)
                    if e.get("type") == "content_block_delta":
                        delta = e.get("delta", {})
                        if delta.get("type") == "text_delta":
                            # Send only actual text deltas to the client
                            yield sse(delta["text"])
        # Signal the end of the stream to the OpenAI-compatible client
        yield "data: [DONE]\n\n"

    # Return a streaming response with SSE for OpenAI compatibility
    return StreamingResponse(stream(), media_type="text/event-stream")
