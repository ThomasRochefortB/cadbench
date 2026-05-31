import asyncio
import json
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from config import DEFAULT_MCP_SERVER_URL
import freecad_context
import openrouter_client
from prompts import CADBENCH_SYSTEM_PROMPT, MCP_ASSISTED_PROMPT_TEMPLATE, MCP_REPAIR_PROMPT_TEMPLATE


@dataclass
class MCPAssistedResult:
    script: str
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    context_bundle: str = ""


class MCPToolBridgeError(RuntimeError):
    pass


@dataclass
class MCPToolBridge:
    server_url: str = field(default_factory=lambda: os.getenv("CADBENCH_MCP_SERVER_URL", DEFAULT_MCP_SERVER_URL))
    http_client_factory: Callable[[], Any] | None = None
    last_validation_handle: str | None = None

    def list_openrouter_tools(self) -> list[dict[str, Any]]:
        return self._run_async(self._list_openrouter_tools())

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_arguments = self._normalize_validation_arguments(name, arguments)
        result = self._run_async(self._call_tool(name, tool_arguments))
        if name == "run_freecad_script" and isinstance(result.get("handle"), str):
            self.last_validation_handle = result["handle"]
        return result

    def close(self) -> None:
        return None

    async def _list_openrouter_tools(self) -> list[dict[str, Any]]:
        async with self._client_session() as session:
            tools_response = await session.list_tools()
        return [_mcp_tool_to_openrouter_tool(tool) for tool in tools_response.tools]

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with self._client_session() as session:
            result = await session.call_tool(name, arguments)
        return _mcp_call_result_to_dict(result)

    @asynccontextmanager
    async def _client_session(self):
        if self.http_client_factory:
            async with self.http_client_factory() as http_client:
                async with streamable_http_client(self.server_url, http_client=http_client) as (
                    read_stream,
                    write_stream,
                    _session_id,
                ):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        yield session
            return

        async with streamable_http_client(self.server_url) as (read_stream, write_stream, _session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session

    def _normalize_validation_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(arguments)
        if name in {"inspect_fcstd", "export_stl", "measure_geometry"}:
            handle = normalized.get("handle", "last")
            if handle == "last" and self.last_validation_handle:
                normalized["handle"] = self.last_validation_handle
        return normalized

    def _run_async(self, coroutine):
        try:
            return asyncio.run(coroutine)
        except RuntimeError as exc:
            if str(exc).startswith("asyncio.run() cannot be called"):
                raise
            raise MCPToolBridgeError(
                f"Unable to communicate with CADBench MCP server at {self.server_url}. "
                "Start CADBench with `uv run uvicorn asgi:app --reload --port 8000`."
            ) from exc
        except Exception as exc:
            raise MCPToolBridgeError(
                f"Unable to communicate with CADBench MCP server at {self.server_url}. "
                "Start CADBench with `uv run uvicorn asgi:app --reload --port 8000`."
            ) from exc


def generate_code_with_mcp_assistance(
    user_prompt: str,
    model_name: str,
    artifact_dir: Path,
) -> MCPAssistedResult:
    context_bundle, context_trace = freecad_context.build_context_bundle_with_trace(user_prompt)
    prompt = MCP_ASSISTED_PROMPT_TEMPLATE.format(
        context_bundle=context_bundle,
        user_prompt=user_prompt,
    )
    completion = _run_mcp_prompt(prompt, model_name, artifact_dir)
    return MCPAssistedResult(
        script=completion.content,
        tool_trace=[*context_trace, *completion.tool_trace],
        context_bundle=context_bundle,
    )


def repair_code_with_mcp_assistance(
    user_prompt: str,
    model_name: str,
    script: str,
    error_details: str,
    artifact_dir: Path,
) -> MCPAssistedResult:
    context_bundle, context_trace = freecad_context.build_context_bundle_with_trace(
        user_prompt,
        error_details=error_details,
    )
    prompt = MCP_REPAIR_PROMPT_TEMPLATE.format(
        context_bundle=context_bundle,
        user_prompt=user_prompt,
        script=script,
        error_details=error_details,
    )
    completion = _run_mcp_prompt(prompt, model_name, artifact_dir)
    return MCPAssistedResult(
        script=completion.content,
        tool_trace=[*context_trace, *completion.tool_trace],
        context_bundle=context_bundle,
    )


def _run_mcp_prompt(
    prompt: str,
    model_name: str,
    artifact_dir: Path,
) -> openrouter_client.ToolCompletionResult:
    bridge = MCPToolBridge()
    try:
        tools = bridge.list_openrouter_tools()
        return openrouter_client.generate_text_with_openrouter_tools(
            prompt,
            model_name,
            tools,
            bridge.call_tool,
            max_tool_rounds=3,
            system_prompt=CADBENCH_SYSTEM_PROMPT,
        )
    except MCPToolBridgeError:
        raise
    except RuntimeError as exc:
        if openrouter_client.is_openrouter_rate_limit_error(exc):
            raise
        if not _is_openrouter_tool_calling_unsupported(exc):
            raise
        content = openrouter_client.generate_code_with_openrouter(
            prompt,
            model_name,
            system_prompt=CADBENCH_SYSTEM_PROMPT,
        )
        return openrouter_client.ToolCompletionResult(
            content=content,
            tool_trace=[
                {
                    "name": "openrouter_tool_calling_fallback",
                    "result": {
                        "success": False,
                        "error": str(exc),
                        "fallback": "Generated with retrieved FreeCAD context only.",
                    },
                }
            ],
        )
    finally:
        bridge.close()


def _mcp_tool_to_openrouter_tool(tool: Any) -> dict[str, Any]:
    parameters = tool.inputSchema if isinstance(tool.inputSchema, dict) else {}
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": parameters,
        },
    }


def _mcp_call_result_to_dict(result: Any) -> dict[str, Any]:
    if getattr(result, "isError", False):
        return {"success": False, "error": _mcp_content_text(result)}

    structured_content = getattr(result, "structuredContent", None)
    if isinstance(structured_content, dict) and structured_content:
        return structured_content

    text = _mcp_content_text(result)
    if text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"success": True, "content": text}
        if isinstance(parsed, dict):
            return parsed
        return {"success": True, "content": parsed}
    return {"success": True}


def _mcp_content_text(result: Any) -> str:
    text_parts = []
    for content in getattr(result, "content", []) or []:
        text = getattr(content, "text", None)
        if isinstance(text, str):
            text_parts.append(text)
    return "\n".join(text_parts).strip()


def _is_openrouter_tool_calling_unsupported(error: Exception | str) -> bool:
    message = str(error).lower()
    return "tool" in message and "not supported" in message
