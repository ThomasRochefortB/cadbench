import os
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from config import (
    DEFAULT_OPENROUTER_MAX_TOKENS,
    GENERATION_REQUEST_TIMEOUT,
    OPENROUTER_APP_REFERER,
    OPENROUTER_APP_TITLE,
    OPENROUTER_CHAT_COMPLETIONS_URL,
)


ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]
ToolTraceCallback = Callable[[dict[str, Any]], None]
FinalResponsePolicy = Callable[[str, list[dict[str, Any]], list[dict[str, Any]]], str | None]


@dataclass
class ToolCompletionResult:
    content: str
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)


def generate_code_with_openrouter(prompt: str, model_name: str, system_prompt: str | None = None) -> str:
    response = create_openrouter_chat_completion(
        model_name,
        _initial_messages(prompt, system_prompt),
    )
    return extract_openrouter_output_text(response.json())


def generate_text_with_openrouter_tools(
    prompt: str,
    model_name: str,
    tools: list[dict[str, Any]],
    execute_tool: ToolExecutor,
    max_tool_rounds: int = 3,
    system_prompt: str | None = None,
    on_tool_call: ToolTraceCallback | None = None,
    final_response_policy: FinalResponsePolicy | None = None,
) -> ToolCompletionResult:
    messages = _initial_messages(prompt, system_prompt)
    tool_trace: list[dict[str, Any]] = []

    for _round_index in range(max_tool_rounds):
        response = create_openrouter_chat_completion(
            model_name,
            messages,
            tools=tools,
            tool_choice="auto",
            parallel_tool_calls=False,
        )
        choice = _first_choice(response.json())
        message = choice.get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            finish_reason = choice.get("finish_reason") or choice.get("native_finish_reason")
            if finish_reason == "tool_calls":
                raise RuntimeError("OpenRouter returned tool_calls finish reason without tool call details")
            content = extract_openrouter_output_text({"choices": [choice]})
            reminder = final_response_policy(content, tool_trace, tools) if final_response_policy else None
            if reminder and _round_index < max_tool_rounds - 1:
                messages.append({"role": message.get("role") or "assistant", "content": content})
                messages.append({"role": "user", "content": reminder})
                continue
            return ToolCompletionResult(content, tool_trace, messages)

        messages.append(_assistant_tool_message(message, tool_calls))
        for tool_call in tool_calls:
            tool_name, tool_args, tool_result = _execute_requested_tool(tool_call, execute_tool)
            trace_entry = {
                "name": tool_name,
                "arguments": _compact_tool_result(tool_args),
                "result": _compact_tool_result(tool_result),
            }
            tool_trace.append(trace_entry)
            if on_tool_call:
                on_tool_call(trace_entry)
            messages.extend(_tool_result_messages(tool_call, tool_name, tool_result))

    response = create_openrouter_chat_completion(
        model_name,
        messages,
    )
    return ToolCompletionResult(
        extract_openrouter_output_text(response.json()),
        tool_trace,
        messages,
    )


def create_openrouter_chat_completion(
    model_name: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    parallel_tool_calls: bool | None = None,
) -> httpx.Response:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "max_tokens": openrouter_max_tokens(),
    }
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if parallel_tool_calls is not None:
        payload["parallel_tool_calls"] = parallel_tool_calls

    response = httpx.post(
        OPENROUTER_CHAT_COMPLETIONS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": OPENROUTER_APP_REFERER,
            "X-OpenRouter-Title": OPENROUTER_APP_TITLE,
        },
        json=payload,
        timeout=GENERATION_REQUEST_TIMEOUT,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(format_openrouter_http_error(exc.response)) from exc
    return response


def openrouter_max_tokens() -> int:
    raw_value = os.getenv("OPENROUTER_MAX_TOKENS")
    if raw_value is None:
        return DEFAULT_OPENROUTER_MAX_TOKENS
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_OPENROUTER_MAX_TOKENS


def format_openrouter_http_error(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        message = error.get("message")
        if message:
            if response.status_code == 429:
                return f"OpenRouter rate limit: {message}"
            return f"OpenRouter error {response.status_code}: {message}"
    except ValueError:
        pass

    details = response.text.strip()
    if details:
        return f"OpenRouter error {response.status_code}: {details}"
    return f"OpenRouter error {response.status_code}"


def is_openrouter_rate_limit_error(error: Exception | str) -> bool:
    return str(error).startswith("OpenRouter rate limit:")


def openrouter_rate_limit_guidance(error: Exception | str) -> str:
    return (
        f"{error}. The selected OpenRouter provider is rate-limited right now. "
        "Choose a different model, switch to a paid endpoint, or wait and retry."
    )


def extract_openrouter_output_text(response_json: dict) -> str:
    choices = response_json.get("choices", [])
    if not choices:
        raise RuntimeError("OpenRouter response did not include choices")

    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    if isinstance(content, list):
        text_parts = [part.get("text", "") for part in content if part.get("type") == "text"]
        if any(text_parts):
            return "\n".join(text_parts).strip()

    text = choice.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()

    finish_reason = choice.get("finish_reason") or choice.get("native_finish_reason")
    if finish_reason:
        raise RuntimeError(f"OpenRouter response did not include text output (finish_reason: {finish_reason})")
    raise RuntimeError("OpenRouter response did not include text output")


def _first_choice(response_json: dict[str, Any]) -> dict[str, Any]:
    choices = response_json.get("choices", [])
    if not choices:
        raise RuntimeError("OpenRouter response did not include choices")
    return choices[0]


def _initial_messages(prompt: str, system_prompt: str | None = None) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": prompt})
    return messages


def _assistant_tool_message(message: dict[str, Any], tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "role": message.get("role") or "assistant",
        "content": message.get("content"),
        "tool_calls": tool_calls,
    }


def _execute_requested_tool(
    tool_call: dict[str, Any], execute_tool: ToolExecutor
) -> tuple[str | None, dict[str, Any], dict[str, Any]]:
    function_call = tool_call.get("function") or {}
    tool_name = function_call.get("name")
    raw_arguments = function_call.get("arguments") or "{}"
    try:
        tool_args = json.loads(raw_arguments)
        if not isinstance(tool_args, dict):
            tool_args = {"value": tool_args}
    except json.JSONDecodeError:
        tool_args = {"raw_arguments": raw_arguments}
        return tool_name, tool_args, {"success": False, "error": "Tool arguments were not valid JSON."}

    if not tool_name:
        return tool_name, tool_args, {"success": False, "error": "Tool call did not include a function name."}

    try:
        return tool_name, tool_args, execute_tool(tool_name, tool_args)
    except Exception as exc:
        return tool_name, tool_args, {"success": False, "error": str(exc)}


def _tool_result_messages(
    tool_call: dict[str, Any],
    tool_name: str | None,
    tool_result: dict[str, Any],
) -> list[dict[str, Any]]:
    image_data_urls = _extract_tool_image_data_urls(tool_result)
    messages = [
        {
            "role": "tool",
            "tool_call_id": tool_call.get("id"),
            "name": tool_name or "unknown_tool",
            "content": json.dumps(_strip_tool_image_data_urls(tool_result), ensure_ascii=True),
        }
    ]
    if image_data_urls:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"Rendered PNG views returned by {tool_name or 'the tool'}. "
                    "Inspect these images for blank output, orientation, missing features, and proportions."
                ),
            }
        ]
        content.extend({"type": "image_url", "image_url": {"url": data_url}} for data_url in image_data_urls)
        messages.append({"role": "user", "content": content})
    return messages


def _extract_tool_image_data_urls(value: Any) -> list[str]:
    urls = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "data_url" and isinstance(child, str) and child.startswith("data:image/"):
                urls.append(child)
            else:
                urls.extend(_extract_tool_image_data_urls(child))
    elif isinstance(value, list):
        for child in value:
            urls.extend(_extract_tool_image_data_urls(child))
    return urls


def _strip_tool_image_data_urls(value: Any) -> Any:
    if isinstance(value, dict):
        stripped = {}
        for key, child in value.items():
            if key == "data_url" and isinstance(child, str) and child.startswith("data:image/"):
                stripped[key] = f"[image data URL omitted: {len(child)} chars]"
            else:
                stripped[key] = _strip_tool_image_data_urls(child)
        return stripped
    if isinstance(value, list):
        return [_strip_tool_image_data_urls(child) for child in value]
    return value


def _compact_tool_result(value: Any, max_chars: int = 900) -> Any:
    rendered = json.dumps(value, ensure_ascii=True, sort_keys=True)
    if len(rendered) <= max_chars:
        return value
    compacted = {"truncated": True, "preview": rendered[:max_chars]}
    if isinstance(value, dict):
        for key in (
            "success",
            "handle",
            "valid",
            "solid_count",
            "face_count",
            "edge_count",
            "triangle_count",
            "warning_count",
        ):
            if key in value:
                compacted[key] = value[key]
    return compacted
