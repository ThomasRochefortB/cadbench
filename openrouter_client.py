import os

import httpx

from config import (
    DEFAULT_OPENROUTER_MAX_TOKENS,
    GENERATION_REQUEST_TIMEOUT,
    OPENROUTER_APP_REFERER,
    OPENROUTER_APP_TITLE,
    OPENROUTER_CHAT_COMPLETIONS_URL,
)


def generate_code_with_openrouter(prompt: str, model_name: str) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    response = httpx.post(
        OPENROUTER_CHAT_COMPLETIONS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": OPENROUTER_APP_REFERER,
            "X-OpenRouter-Title": OPENROUTER_APP_TITLE,
        },
        json={
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": openrouter_max_tokens(),
        },
        timeout=GENERATION_REQUEST_TIMEOUT,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(format_openrouter_http_error(exc.response)) from exc
    return extract_openrouter_output_text(response.json())


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
