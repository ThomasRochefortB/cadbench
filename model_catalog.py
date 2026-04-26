import os

import httpx

from config import (
    DEFAULT_MODEL,
    MIN_RECOMMENDED_CONTEXT_LENGTH,
    MODEL_DISPLAY_NAMES,
    MODEL_REQUEST_TIMEOUT,
    OPENROUTER_MODELS_URL,
    OPENROUTER_USER_MODELS_URL,
)


def is_free_openrouter_model(model: dict) -> bool:
    pricing = model.get("pricing") or {}
    architecture = model.get("architecture") or {}
    output_modalities = architecture.get("output_modalities") or []
    context_length = model.get("context_length")
    return (
        model.get("id", "").endswith(":free")
        and pricing.get("prompt") == "0"
        and pricing.get("completion") == "0"
        and "text" in output_modalities
        and (context_length is None or context_length >= MIN_RECOMMENDED_CONTEXT_LENGTH)
    )


def is_supported_model_id(model_name: str) -> bool:
    return "/" in model_name and model_name.endswith(":free")


def display_name_from_model_id(model_id: str) -> str:
    if model_id in MODEL_DISPLAY_NAMES:
        return MODEL_DISPLAY_NAMES[model_id]
    return model_id.replace("/", ": ").replace("-", " ").title()


def provider_from_model_id(model_id: str) -> str:
    return model_id.split("/", 1)[0] if "/" in model_id else "unknown"


def model_info_from_openrouter_model(model: dict) -> dict:
    model_id = model["id"]
    info = {
        "id": model_id,
        "name": model.get("name") or display_name_from_model_id(model_id),
        "provider": provider_from_model_id(model_id),
        "context_length": model.get("context_length"),
        "free": True,
    }

    supported_parameters = model.get("supported_parameters")
    if supported_parameters:
        info["supported_parameters"] = supported_parameters

    top_provider = model.get("top_provider") or {}
    max_completion_tokens = top_provider.get("max_completion_tokens")
    if max_completion_tokens:
        info["max_completion_tokens"] = max_completion_tokens

    return info


def fetch_openrouter_free_models() -> list[dict]:
    api_key = os.getenv("OPENROUTER_API_KEY")
    url = OPENROUTER_USER_MODELS_URL if api_key else OPENROUTER_MODELS_URL
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None

    response = httpx.get(
        url,
        headers=headers,
        params={"output_modalities": "text"},
        timeout=MODEL_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return [
        model_info_from_openrouter_model(model)
        for model in response.json().get("data", [])
        if is_free_openrouter_model(model)
    ]


def fallback_model_info() -> list[dict]:
    return [
        {
            "id": model_id,
            "name": display_name,
            "provider": provider_from_model_id(model_id),
            "context_length": None,
            "free": True,
        }
        for model_id, display_name in MODEL_DISPLAY_NAMES.items()
    ]


def get_available_model_info() -> list[dict]:
    try:
        models_by_id = {model["id"]: model for model in fetch_openrouter_free_models()}
    except httpx.HTTPError as exc:
        print(f"Unable to fetch OpenRouter models: {exc}")
        models_by_id = {}

    if not models_by_id:
        return fallback_model_info()

    for model in fallback_model_info():
        models_by_id.setdefault(model["id"], model)

    models = list(models_by_id.values())
    models.sort(key=lambda model: (model["id"] != DEFAULT_MODEL, model["name"].lower()))
    return models
