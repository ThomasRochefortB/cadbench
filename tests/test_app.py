import json
import os

import app as cadbench
import config
import freecad_context
import freecad_validation
from prompts import CADBENCH_SYSTEM_PROMPT


def assisted_result(script: str, tool_trace: list[dict] | None = None) -> cadbench.mcp_assistant.MCPAssistedResult:
    return cadbench.mcp_assistant.MCPAssistedResult(script=script, tool_trace=tool_trace or [])


def stub_mcp_tool_bridge(monkeypatch, tools: list[dict] | None = None):
    class StubMCPToolBridge:
        def list_openrouter_tools(self):
            return tools or [
                {
                    "type": "function",
                    "function": {
                        "name": "search_docs",
                        "description": "Search docs",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                    },
                }
            ]

        def call_tool(self, name, arguments):
            return {"success": True, "name": name, "arguments": arguments}

        def close(self):
            return None

    monkeypatch.setattr(cadbench.mcp_assistant, "MCPToolBridge", StubMCPToolBridge)


def test_prepare_freecad_script_strips_fences_rewrites_output_and_removes_gui_import():
    script = """```python
import FreeCADGui
App.setActiveDocument("20mmCube")
doc.save("/data/output.FCStd")
```"""

    prepared = cadbench.prepare_freecad_script(script, "_model1")

    assert "```" not in prepared
    assert "# [removed] import FreeCADGui" in prepared
    assert '# [removed] App.setActiveDocument("20mmCube")' in prepared
    assert 'doc.saveAs("/data/output_model1.FCStd")' in prepared


def test_prepare_freecad_script_extracts_fenced_code_after_prose():
    script = """Perfect! Here is the corrected script:

```python
import FreeCAD as App
import Part

doc = App.newDocument("CADModel")
box = Part.makeBox(1, 1, 1)
Part.show(box)
doc.saveAs("/data/output.FCStd")
```
"""

    prepared = cadbench.prepare_freecad_script(script, "_model1")

    assert "Perfect!" not in prepared
    assert "```" not in prepared
    assert prepared.startswith("import FreeCAD as App")
    assert 'doc.saveAs("/data/output_model1.FCStd")' in prepared


def test_prepare_freecad_script_trims_prose_glued_to_import():
    script = (
        "For road bike gears, I'll create a chainring profile.import FreeCAD as App\n"
        "import Part\n"
        'doc = App.newDocument("CADModel")\n'
        "box = Part.makeBox(1, 1, 1)\n"
        "Part.show(box)\n"
        'doc.saveAs("/data/output.FCStd")'
    )

    prepared = cadbench.prepare_freecad_script(script, "_model1")

    assert "For road bike gears" not in prepared
    assert prepared.startswith("import FreeCAD as App")
    assert 'doc.saveAs("/data/output_model1.FCStd")' in prepared


def test_prepare_freecad_script_rejects_truncated_python_before_freecad_runs():
    script = """import FreeCAD as App
import Part

doc = App.newDocument("CADModel")
pts = []
pts.append(App.Vector(r_
"""

    try:
        cadbench.prepare_freecad_script(script, "_model1")
    except ValueError as exc:
        assert "Generated FreeCAD script is not valid Python" in str(exc)
        assert "line" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_prepare_freecad_script_makes_generated_fillets_non_fatal():
    script = 'body = body.makeFillet(2.0, edges_to_round)\ndoc.saveAs("/data/output.FCStd")'

    prepared = cadbench.prepare_freecad_script(script, "_model1")

    assert (
        "try:\n"
        "    body = body.makeFillet(2.0, edges_to_round)\n"
        "except Exception:\n"
        "    pass"
    ) in prepared
    assert 'doc.saveAs("/data/output_model1.FCStd")' in prepared


def test_prepare_freecad_script_adds_missing_save_footer():
    script = "import FreeCAD as App\nimport Part\nbox = Part.makeBox(1, 1, 1)\nPart.show(box)"

    prepared = cadbench.prepare_freecad_script(script, "_model1")

    assert 'doc = App.newDocument("CADModel")' in prepared
    assert 'doc.saveAs("/data/output_model1.FCStd")' in prepared


def test_make_docker_command_uses_basic_container_limits(tmp_path):
    command = cadbench.make_docker_command(tmp_path, "gen.py")

    assert "--network" in command
    assert "none" in command
    assert "--memory" in command
    assert "--cpus" in command
    assert "--pids-limit" in command
    assert "--security-opt" in command
    assert "no-new-privileges" in command


def test_generate_rejects_empty_prompt():
    client = cadbench.app.test_client()

    response = client.post("/api/generate", json={"prompt": "   "})

    assert response.status_code == 400
    assert response.get_json() == {"error": "Prompt is required"}


def test_models_endpoint_lists_current_default_and_free_openrouter_models(monkeypatch):
    monkeypatch.setattr(
        cadbench.model_catalog,
        "fetch_openrouter_models",
        lambda free_only=True: cadbench.fallback_model_info(),
    )
    client = cadbench.app.test_client()

    response = client.get("/api/models")

    assert response.status_code == 200
    model_ids = {model["id"] for model in response.get_json()}
    assert cadbench.DEFAULT_MODEL in model_ids
    assert all(model_id.endswith(":free") for model_id in model_ids)


def test_models_endpoint_can_include_paid_openrouter_models(monkeypatch):
    captured = {}

    def fake_models(free_only=True):
        captured["free_only"] = free_only
        return [
            {
                "id": "provider/paid-model",
                "name": "Paid Model",
                "provider": "provider",
                "context_length": 8192,
                "free": False,
            }
        ]

    monkeypatch.setattr(cadbench.model_catalog, "fetch_openrouter_models", fake_models)
    client = cadbench.app.test_client()

    response = client.get("/api/models?free_only=false")

    assert response.status_code == 200
    assert captured["free_only"] is False
    assert response.get_json()[0]["id"] == "provider/paid-model"
    assert response.get_json()[0]["free"] is False


def test_extract_openrouter_output_text_from_chat_completion_shape():
    response_json = {
        "choices": [
            {
                "message": {"content": "print('hello')"},
            }
        ],
    }

    assert cadbench.extract_openrouter_output_text(response_json) == "print('hello')"


def test_extract_openrouter_output_text_reports_empty_finish_reason():
    response_json = {
        "choices": [
            {
                "finish_reason": "content_filter",
                "message": {"content": None},
            }
        ],
    }

    try:
        cadbench.extract_openrouter_output_text(response_json)
    except RuntimeError as exc:
        assert str(exc) == "OpenRouter response did not include text output (finish_reason: content_filter)"
    else:
        raise AssertionError("Expected RuntimeError")


def test_extract_openrouter_output_text_accepts_legacy_text_field():
    response_json = {"choices": [{"text": "print('legacy')", "finish_reason": "length"}]}

    assert cadbench.extract_openrouter_output_text(response_json) == "print('legacy')"


def test_generate_code_with_openrouter_uses_configurable_max_tokens(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        request = cadbench.httpx.Request("POST", url)
        return cadbench.httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "print('ok')"}}]},
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", "1234")
    monkeypatch.setattr(cadbench.httpx, "post", fake_post)

    cadbench.generate_code_with_openrouter("prompt", cadbench.DEFAULT_MODEL)

    assert captured["json"]["max_tokens"] == 1234


def test_generate_text_with_openrouter_tools_executes_requested_tool(monkeypatch):
    captured_payloads = []
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "search_docs",
                                    "arguments": '{"query": "box hole"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {"choices": [{"message": {"content": "print('done')"}}]},
    ]

    def fake_post(url, headers=None, json=None, timeout=None):
        captured_payloads.append(json)
        request = cadbench.httpx.Request("POST", url)
        return cadbench.httpx.Response(200, request=request, json=responses.pop(0))

    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_docs",
                "description": "Search docs",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ]

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(cadbench.httpx, "post", fake_post)

    result = cadbench.generate_text_with_openrouter_tools(
        "prompt",
        cadbench.DEFAULT_MODEL,
        tools,
        lambda name, args: {"success": True, "name": name, "query": args["query"]},
        max_tool_rounds=2,
    )

    assert result.content == "print('done')"
    assert result.tool_trace[0]["name"] == "search_docs"
    assert captured_payloads[0]["tools"] == tools
    assert captured_payloads[1]["tools"] == tools
    assert captured_payloads[1]["messages"][1]["tool_calls"][0]["id"] == "call_1"
    assert captured_payloads[1]["messages"][2]["role"] == "tool"


def test_generate_text_with_openrouter_tools_can_send_system_prompt(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        request = cadbench.httpx.Request("POST", url)
        return cadbench.httpx.Response(200, request=request, json={"choices": [{"message": {"content": "done"}}]})

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(cadbench.httpx, "post", fake_post)

    result = cadbench.generate_text_with_openrouter_tools(
        "user task",
        cadbench.DEFAULT_MODEL,
        [],
        lambda _name, _args: {"success": True},
        system_prompt="system rules",
    )

    assert result.content == "done"
    assert captured["json"]["messages"][:2] == [
        {"role": "system", "content": "system rules"},
        {"role": "user", "content": "user task"},
    ]


def test_generate_text_with_openrouter_tools_omits_tool_choice_for_final_answer(monkeypatch):
    captured_payloads = []
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "search_docs",
                                    "arguments": '{"query": "box hole"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {"choices": [{"message": {"content": "print('done')"}}]},
    ]

    def fake_post(url, headers=None, json=None, timeout=None):
        captured_payloads.append(json)
        request = cadbench.httpx.Request("POST", url)
        return cadbench.httpx.Response(200, request=request, json=responses.pop(0))

    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_docs",
                "description": "Search docs",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ]

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(cadbench.httpx, "post", fake_post)

    result = cadbench.generate_text_with_openrouter_tools(
        "prompt",
        cadbench.DEFAULT_MODEL,
        tools,
        lambda name, args: {"success": True, "name": name, "query": args["query"]},
        max_tool_rounds=1,
    )

    assert result.content == "print('done')"
    assert captured_payloads[0]["tool_choice"] == "auto"
    assert "tools" in captured_payloads[0]
    assert "tool_choice" not in captured_payloads[1]
    assert "tools" not in captured_payloads[1]
    assert "parallel_tool_calls" not in captured_payloads[1]


def test_generate_text_with_openrouter_tools_reports_missing_tool_call_details(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        request = cadbench.httpx.Request("POST", url)
        return cadbench.httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": None},
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(cadbench.httpx, "post", fake_post)

    try:
        cadbench.generate_text_with_openrouter_tools(
            "prompt",
            cadbench.DEFAULT_MODEL,
            [],
            lambda _name, _args: {"success": True},
        )
    except RuntimeError as exc:
        assert str(exc) == "OpenRouter returned tool_calls finish reason without tool call details"
    else:
        raise AssertionError("Expected RuntimeError")


def test_generate_text_with_openrouter_tools_sends_rendered_views_as_images(monkeypatch):
    captured_payloads = []
    image_data_url = "data:image/png;base64,AAAA"
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_render",
                                "type": "function",
                                "function": {
                                    "name": "render_model_views",
                                    "arguments": '{"handle": "last"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {"choices": [{"message": {"content": "print('done')"}}]},
    ]

    def fake_post(url, headers=None, json=None, timeout=None):
        captured_payloads.append(json)
        request = cadbench.httpx.Request("POST", url)
        return cadbench.httpx.Response(200, request=request, json=responses.pop(0))

    tools = [
        {
            "type": "function",
            "function": {
                "name": "render_model_views",
                "description": "Render views",
                "parameters": {"type": "object", "properties": {"handle": {"type": "string"}}},
            },
        }
    ]

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(cadbench.httpx, "post", fake_post)

    result = cadbench.generate_text_with_openrouter_tools(
        "prompt",
        cadbench.DEFAULT_MODEL,
        tools,
        lambda _name, _args: {
            "success": True,
            "views": {"front": {"path": "front.png", "data_url": image_data_url}},
        },
        max_tool_rounds=1,
    )

    assert result.content == "print('done')"
    followup_messages = captured_payloads[1]["messages"]
    assert "[image data URL omitted" in followup_messages[2]["content"]
    assert followup_messages[3]["role"] == "user"
    assert followup_messages[3]["content"][1] == {"type": "image_url", "image_url": {"url": image_data_url}}


def test_generate_text_with_openrouter_tools_reports_tool_calls_to_callback(monkeypatch):
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "measure_geometry",
                                    "arguments": '{"handle": "last"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {"choices": [{"message": {"content": "print('done')"}}]},
    ]
    callbacks = []

    def fake_post(url, headers=None, json=None, timeout=None):
        request = cadbench.httpx.Request("POST", url)
        return cadbench.httpx.Response(200, request=request, json=responses.pop(0))

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(cadbench.httpx, "post", fake_post)

    cadbench.generate_text_with_openrouter_tools(
        "prompt",
        cadbench.DEFAULT_MODEL,
        [{"type": "function", "function": {"name": "measure_geometry", "parameters": {"type": "object"}}}],
        lambda _name, _args: {"success": True, "solid_count": 1},
        max_tool_rounds=1,
        on_tool_call=callbacks.append,
    )

    assert callbacks == [
        {
            "name": "measure_geometry",
            "arguments": {"handle": "last"},
            "result": {"success": True, "solid_count": 1},
        }
    ]


def test_generate_text_with_openrouter_tools_can_remind_before_final_response(monkeypatch):
    import copy

    captured_payloads = []
    responses = [
        {"choices": [{"message": {"role": "assistant", "content": "draft script"}}]},
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_measure",
                                "type": "function",
                                "function": {
                                    "name": "measure_geometry",
                                    "arguments": '{"handle": "last"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {"choices": [{"message": {"content": "final script"}}]},
    ]

    def fake_post(url, headers=None, json=None, timeout=None):
        captured_payloads.append(copy.deepcopy(json))
        request = cadbench.httpx.Request("POST", url)
        return cadbench.httpx.Response(200, request=request, json=responses.pop(0))

    def final_response_policy(_content, tool_trace, _tools):
        if not tool_trace:
            return "Call a validation tool before returning final code."
        return None

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(cadbench.httpx, "post", fake_post)

    result = cadbench.generate_text_with_openrouter_tools(
        "prompt",
        cadbench.DEFAULT_MODEL,
        [{"type": "function", "function": {"name": "measure_geometry", "parameters": {"type": "object"}}}],
        lambda _name, _args: {"success": True, "solid_count": 1},
        max_tool_rounds=3,
        final_response_policy=final_response_policy,
    )

    assert result.content == "final script"
    assert captured_payloads[1]["messages"][-2] == {"role": "assistant", "content": "draft script"}
    assert captured_payloads[1]["messages"][-1] == {
        "role": "user",
        "content": "Call a validation tool before returning final code.",
    }
    assert result.tool_trace[0]["name"] == "measure_geometry"


def test_cadbench_final_response_policy_requires_render_for_vision_models():
    tools = [
        {"type": "function", "function": {"name": "run_freecad_script"}},
        {"type": "function", "function": {"name": "shape_health_check"}},
        {"type": "function", "function": {"name": "mesh_quality_report"}},
        {"type": "function", "function": {"name": "render_model_views"}},
    ]
    policy = cadbench.mcp_assistant.cadbench_final_response_policy(vision_capable=True)

    assert "run_freecad_script" in policy("script", [], tools)

    reminder = policy(
        "script",
        [
            {"name": "run_freecad_script", "result": {"success": True, "handle": "run_1"}},
            {"name": "shape_health_check", "result": {"success": True}},
            {"name": "mesh_quality_report", "result": {"success": True}},
        ],
        tools,
    )

    assert "render_model_views" in reminder

    assert (
        policy(
            "script",
            [
                {"name": "run_freecad_script", "result": {"success": True, "handle": "run_1"}},
                {"name": "shape_health_check", "result": {"success": True}},
                {"name": "mesh_quality_report", "result": {"success": True}},
                {"name": "render_model_views", "result": {"success": True}},
            ],
            tools,
        )
        is None
    )


def test_cadbench_final_response_policy_does_not_require_render_for_text_models():
    tools = [
        {"type": "function", "function": {"name": "run_freecad_script"}},
        {"type": "function", "function": {"name": "shape_health_check"}},
        {"type": "function", "function": {"name": "mesh_quality_report"}},
        {"type": "function", "function": {"name": "render_model_views"}},
    ]
    policy = cadbench.mcp_assistant.cadbench_final_response_policy(vision_capable=False)

    assert (
        policy(
            "script",
            [
                {"name": "run_freecad_script", "result": {"success": True, "handle": "run_1"}},
                {"name": "shape_health_check", "result": {"success": True}},
                {"name": "mesh_quality_report", "result": {"success": True}},
            ],
            tools,
        )
        is None
    )


def test_format_openrouter_http_error_uses_provider_message():
    response = cadbench.httpx.Response(
        404,
        json={
            "error": {
                "message": "No endpoints available matching your guardrail restrictions and data policy.",
            }
        },
    )

    assert cadbench.format_openrouter_http_error(response) == (
        "OpenRouter error 404: No endpoints available matching your guardrail restrictions and data policy."
    )


def test_format_openrouter_http_error_marks_rate_limits():
    response = cadbench.httpx.Response(
        429,
        json={"error": {"message": "Provider returned error"}},
    )

    assert cadbench.format_openrouter_http_error(response) == "OpenRouter rate limit: Provider returned error"


def test_openrouter_rate_limit_guidance_is_actionable():
    message = cadbench.openrouter_client.openrouter_rate_limit_guidance(
        "OpenRouter rate limit: Provider returned error"
    )

    assert "Choose a different model" in message
    assert "switch to a paid endpoint" in message


def test_fetch_openrouter_free_models_uses_user_models_when_key_is_set(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        request = cadbench.httpx.Request("GET", url)
        return cadbench.httpx.Response(
            200,
            request=request,
            json={
                "data": [
                    {
                        "id": "provider/account-visible:free",
                        "name": "Account Visible",
                        "pricing": {"prompt": "0", "completion": "0"},
                        "architecture": {"output_modalities": ["text"]},
                    },
                    {
                        "id": "provider/paid",
                        "name": "Paid",
                        "pricing": {"prompt": "1", "completion": "0"},
                        "architecture": {"output_modalities": ["text"]},
                    },
                ]
            },
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(cadbench.httpx, "get", fake_get)

    models = cadbench.fetch_openrouter_free_models()

    assert captured["url"] == config.OPENROUTER_USER_MODELS_URL
    assert captured["headers"] == {"Authorization": "Bearer sk-or-test"}
    assert models == [
        {
            "id": "provider/account-visible:free",
            "name": "Account Visible",
            "provider": "provider",
            "context_length": None,
            "free": True,
            "vision_capable": False,
            "output_modalities": ["text"],
            "pricing": {"prompt": "0", "completion": "0"},
        }
    ]


def test_fetch_openrouter_models_can_include_paid_models(monkeypatch):
    def fake_get(url, headers=None, params=None, timeout=None):
        request = cadbench.httpx.Request("GET", url)
        return cadbench.httpx.Response(
            200,
            request=request,
            json={
                "data": [
                    {
                        "id": "provider/free-model:free",
                        "name": "Free Model",
                        "pricing": {"prompt": "0", "completion": "0"},
                        "architecture": {"output_modalities": ["text"]},
                    },
                    {
                        "id": "provider/paid-model",
                        "name": "Paid Model",
                        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                        "architecture": {"output_modalities": ["text"]},
                    },
                ]
            },
        )

    monkeypatch.setattr(cadbench.httpx, "get", fake_get)

    models = cadbench.fetch_openrouter_models(free_only=False)

    assert [model["id"] for model in models] == ["provider/free-model:free", "provider/paid-model"]
    assert models[0]["free"] is True
    assert models[1]["free"] is False
    assert models[0]["vision_capable"] is False
    assert models[1]["vision_capable"] is False
    assert models[1]["pricing"] == {"prompt": "0.000001", "completion": "0.000002"}


def test_model_info_marks_openrouter_image_input_models_as_vision_capable():
    model = cadbench.model_catalog.model_info_from_openrouter_model(
        {
            "id": "provider/vision-model",
            "name": "Vision Model",
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
            "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
        }
    )

    assert model["vision_capable"] is True
    assert model["input_modalities"] == ["text", "image"]


def test_available_models_cache_vision_capability(monkeypatch):
    cadbench.model_catalog._MODEL_VISION_CAPABILITY_BY_ID.clear()
    monkeypatch.setattr(
        cadbench.model_catalog,
        "fetch_openrouter_models",
        lambda free_only=True: [
            {"id": "provider/vision-model", "name": "Vision Model", "vision_capable": True},
            {"id": "provider/text-model", "name": "Text Model", "vision_capable": False},
        ],
    )

    cadbench.get_available_model_info(free_only=False)

    assert cadbench.model_catalog.cached_model_is_vision_capable("provider/vision-model") is True
    assert cadbench.model_catalog.cached_model_is_vision_capable("provider/text-model") is False
    assert cadbench.model_catalog.cached_model_is_vision_capable("provider/unknown") is False


def test_mcp_tool_filter_hides_render_views_for_non_vision_models():
    tools = [
        {"type": "function", "function": {"name": "render_model_views"}},
        {"type": "function", "function": {"name": "shape_health_check"}},
        {"type": "function", "function": {"name": "mesh_quality_report"}},
    ]

    non_vision_tools = cadbench.mcp_assistant.filter_openrouter_tools_for_model(tools, vision_capable=False)
    vision_tools = cadbench.mcp_assistant.filter_openrouter_tools_for_model(tools, vision_capable=True)

    assert [tool["function"]["name"] for tool in non_vision_tools] == [
        "shape_health_check",
        "mesh_quality_report",
    ]
    assert [tool["function"]["name"] for tool in vision_tools] == [
        "render_model_views",
        "shape_health_check",
        "mesh_quality_report",
    ]


def test_is_free_openrouter_model_requires_free_text_output():
    free_model = {
        "id": "provider/model:free",
        "pricing": {"prompt": "0", "completion": "0"},
        "architecture": {"output_modalities": ["text"]},
    }
    paid_model = {
        "id": "provider/model",
        "pricing": {"prompt": "0.1", "completion": "0"},
        "architecture": {"output_modalities": ["text"]},
    }

    assert cadbench.is_free_openrouter_model(free_model)
    assert not cadbench.is_free_openrouter_model(paid_model)


def test_is_free_openrouter_model_filters_tiny_context_windows():
    tiny_model = {
        "id": "provider/model:free",
        "pricing": {"prompt": "0", "completion": "0"},
        "architecture": {"output_modalities": ["text"]},
        "context_length": 2048,
    }

    assert not cadbench.is_free_openrouter_model(tiny_model)


def test_get_available_model_info_merges_live_free_models_with_fallback(monkeypatch):
    monkeypatch.setattr(
        cadbench.model_catalog,
        "fetch_openrouter_models",
        lambda free_only=True: [{"id": "provider/live-model:free", "name": "Live Free Model"}],
    )

    model_ids = {model["id"] for model in cadbench.get_available_model_info()}

    assert "provider/live-model:free" in model_ids
    assert cadbench.DEFAULT_MODEL in model_ids


def test_generate_falls_back_invalid_models_and_uses_request_scoped_artifacts(monkeypatch):
    def fake_generate_code(user_prompt, model_name, artifact_dir):
        assert user_prompt == "make a cube"
        assert model_name == cadbench.DEFAULT_MODEL
        assert artifact_dir.name in {"mcp_model1_initial", "mcp_model2_initial"}
        return assisted_result('doc.save("/data/output.FCStd")')

    def fake_execute(script, file_suffix="", artifact_dir=None):
        assert "output_model" in script
        assert artifact_dir is not None
        return cadbench.FreeCADExecutionResult(
            artifact_dir / f"output{file_suffix}.FCStd",
            artifact_dir / f"output{file_suffix}.stl",
        )

    monkeypatch.setattr(cadbench, "generate_code_with_mcp_assistance", fake_generate_code)
    monkeypatch.setattr(cadbench, "try_execute_freecad_script", fake_execute)
    monkeypatch.setattr(cadbench, "cleanup_old_artifacts", lambda: None)

    client = cadbench.app.test_client()
    response = client.post(
        "/api/generate",
        json={"prompt": "make a cube", "model1": "missing", "model2": "also-missing"},
    )

    assert response.status_code == 200
    results = response.get_json()["results"]
    assert [result["model"] for result in results] == [cadbench.DEFAULT_MODEL, cadbench.DEFAULT_MODEL]
    assert results[0]["fcstd_url"].startswith("/generated/")
    assert results[0]["stl_url"].startswith("/generated/")
    assert results[0]["fcstd_url"] != results[1]["fcstd_url"]
    assert "/output_model1.FCStd" in results[0]["fcstd_url"]
    assert "/output_model2.FCStd" in results[1]["fcstd_url"]


def test_generate_defaults_to_one_model_when_second_model_is_omitted(monkeypatch):
    generated_models = []

    def fake_generate_code(user_prompt, model_name, artifact_dir):
        assert user_prompt == "make a cube"
        generated_models.append(model_name)
        assert artifact_dir.name == "mcp_model1_initial"
        return assisted_result('doc.save("/data/output.FCStd")')

    def fake_execute(script, file_suffix="", artifact_dir=None):
        assert file_suffix == "_model1"
        assert artifact_dir is not None
        return cadbench.FreeCADExecutionResult(
            artifact_dir / f"output{file_suffix}.FCStd",
            artifact_dir / f"output{file_suffix}.stl",
        )

    monkeypatch.setattr(cadbench, "generate_code_with_mcp_assistance", fake_generate_code)
    monkeypatch.setattr(cadbench, "try_execute_freecad_script", fake_execute)
    monkeypatch.setattr(cadbench, "cleanup_old_artifacts", lambda: None)

    client = cadbench.app.test_client()
    response = client.post(
        "/api/generate",
        json={"prompt": "make a cube", "model1": cadbench.DEFAULT_MODEL},
    )

    assert response.status_code == 200
    results = response.get_json()["results"]
    assert generated_models == [cadbench.DEFAULT_MODEL]
    assert [result["model"] for result in results] == [cadbench.DEFAULT_MODEL]
    assert "/output_model1.FCStd" in results[0]["fcstd_url"]


def test_generate_stream_emits_stage_tool_call_and_result(monkeypatch, tmp_path):
    def fake_build_model_result(
        user_prompt,
        model_name,
        file_suffix,
        artifact_dir,
        vision_capable=False,
        progress_callback=None,
    ):
        assert user_prompt == "make a cube"
        assert model_name == cadbench.DEFAULT_MODEL
        assert file_suffix == "_model1"
        assert artifact_dir is not None
        assert vision_capable is False
        progress_callback({"type": "stage", "stage": "Generated initial script"})
        progress_callback(
            {
                "type": "tool_call",
                "phase": "Initial MCP",
                "call": {
                    "name": "search_docs",
                    "arguments": {"query": "booleans"},
                    "result": {"success": True},
                },
            }
        )
        return {
            "model": model_name,
            "mode": "mcp",
            "stages": ["Generated initial script"],
            "script": "print('ok')",
            "fcstd_url": "/generated/test/output_model1.FCStd",
        }

    monkeypatch.setattr(cadbench, "build_model_result", fake_build_model_result)
    monkeypatch.setattr(cadbench, "cleanup_old_artifacts", lambda: None)

    client = cadbench.app.test_client()
    response = client.post("/api/generate/stream", json={"prompt": "make a cube"}, buffered=True)

    assert response.status_code == 200
    events = [json.loads(line) for line in response.data.decode().splitlines() if line.strip()]
    assert [event["type"] for event in events] == ["start", "stage", "tool_call", "result", "done"]
    assert events[1] == {"index": 1, "type": "stage", "stage": "Generated initial script"}
    assert events[2]["phase"] == "Initial MCP"
    assert events[2]["call"]["name"] == "search_docs"
    assert events[3]["result"]["script"] == "print('ok')"


def test_build_model_result_reports_freecad_execution_details(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cadbench,
        "generate_code_with_mcp_assistance",
        lambda _prompt, _model, _artifact_dir: assisted_result("print('ok')"),
    )
    monkeypatch.setattr(
        cadbench,
        "repair_code_with_mcp_assistance",
        lambda *_args: assisted_result("print('still broken')"),
    )
    monkeypatch.setattr(
        cadbench,
        "try_execute_freecad_script",
        lambda _script, _suffix, _artifact_dir: cadbench.FreeCADExecutionResult(
            None,
            error_info="Traceback\nException: failed",
        ),
    )

    result = cadbench.build_model_result("prompt", cadbench.DEFAULT_MODEL, "_model1", tmp_path)

    assert result["error"] == "The FreeCAD script had execution errors"
    assert result["error_details"] == "Traceback\nException: failed"


def test_build_model_result_reports_generated_python_syntax_errors_before_freecad(monkeypatch, tmp_path):
    execute_called = False

    monkeypatch.setattr(
        cadbench,
        "generate_code_with_mcp_assistance",
        lambda _prompt, _model, _artifact_dir: assisted_result("import FreeCAD as App\npts.append(App.Vector(r_"),
    )
    monkeypatch.setattr(
        cadbench,
        "repair_code_with_mcp_assistance",
        lambda *_args: assisted_result("Still not Python ×"),
    )

    def fake_execute(*_args, **_kwargs):
        nonlocal execute_called
        execute_called = True
        return cadbench.FreeCADExecutionResult(None)

    monkeypatch.setattr(cadbench, "try_execute_freecad_script", fake_execute)

    result = cadbench.build_model_result("prompt", cadbench.DEFAULT_MODEL, "_model1", tmp_path)

    assert execute_called is False
    assert result["script"] == "import FreeCAD as App\npts.append(App.Vector(r_"
    assert result["error"].startswith("Generated script was not valid Python:")
    assert result["repair_attempted"] is True
    assert result["repair_error_details"].startswith("Syntax repair was not valid Python:")
    assert "Syntax repair failed Python validation" in result["stages"]


def test_build_model_result_repairs_generated_python_syntax_errors(monkeypatch, tmp_path):
    captured_repair = {}

    monkeypatch.setattr(
        cadbench,
        "generate_code_with_mcp_assistance",
        lambda _prompt, _model, _artifact_dir: assisted_result(
            "The script ran successfully with dimensions around 90×92×23 units."
        ),
    )

    def fake_repair(user_prompt, model_name, script, error_details, artifact_dir):
        captured_repair["script"] = script
        captured_repair["error_details"] = error_details
        captured_repair["artifact_dir"] = artifact_dir
        return assisted_result('doc.saveAs("/data/output.FCStd")', [{"name": "known_error_fix"}])

    monkeypatch.setattr(cadbench, "repair_code_with_mcp_assistance", fake_repair)
    monkeypatch.setattr(
        cadbench,
        "try_execute_freecad_script",
        lambda _script, file_suffix, artifact_dir: cadbench.FreeCADExecutionResult(
            artifact_dir / f"output{file_suffix}.FCStd"
        ),
    )

    result = cadbench.build_model_result("prompt", cadbench.DEFAULT_MODEL, "_model1", tmp_path)

    assert result["repaired"] is True
    assert result["repair_attempted"] is True
    assert result["fcstd_url"].endswith("/output_model1.FCStd")
    assert result["original_script"] == "The script ran successfully with dimensions around 90×92×23 units."
    assert captured_repair["script"] == result["original_script"]
    assert captured_repair["artifact_dir"] == tmp_path / "mcp_model1_syntax_repair"
    assert "Generated script was not valid Python" in captured_repair["error_details"]
    assert "Syntax repair succeeded" in result["stages"]


def test_build_model_result_reports_missing_fcstd_save(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cadbench,
        "generate_code_with_mcp_assistance",
        lambda _prompt, _model, _artifact_dir: assisted_result("print('ok')"),
    )
    monkeypatch.setattr(
        cadbench,
        "repair_code_with_mcp_assistance",
        lambda *_args: assisted_result("print('still broken')"),
    )
    monkeypatch.setattr(
        cadbench,
        "try_execute_freecad_script",
        lambda _script, _suffix, _artifact_dir: cadbench.FreeCADExecutionResult(
            None,
            error_info="FreeCAD process exited cleanly, but the expected output.FCStd file was not created by the script.",
        ),
    )

    result = cadbench.build_model_result("prompt", cadbench.DEFAULT_MODEL, "_model1", tmp_path)

    assert result["error"] == "The script ran but did not save an FCStd model"
    assert "expected output.FCStd file was not created" in result["error_details"]
    assert result["repair_attempted"]


def test_build_model_result_repairs_failed_freecad_script(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr(
        cadbench,
        "generate_code_with_mcp_assistance",
        lambda _prompt, _model, _artifact_dir: assisted_result("broken()"),
    )
    monkeypatch.setattr(
        cadbench,
        "repair_code_with_mcp_assistance",
        lambda *_args: assisted_result('doc.saveAs("/data/output.FCStd")'),
    )

    def fake_execute(script, file_suffix="", artifact_dir=None):
        calls.append(script)
        if len(calls) == 1:
            return cadbench.FreeCADExecutionResult(None, error_info="Traceback\nException: failed")
        return cadbench.FreeCADExecutionResult(artifact_dir / f"output{file_suffix}.FCStd")

    monkeypatch.setattr(cadbench, "try_execute_freecad_script", fake_execute)

    result = cadbench.build_model_result("prompt", cadbench.DEFAULT_MODEL, "_model1", tmp_path)

    assert result["repaired"] is True
    assert result["fcstd_url"].endswith("/output_model1.FCStd")
    assert result["original_script"]


def test_build_model_result_reports_docker_start_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cadbench,
        "generate_code_with_mcp_assistance",
        lambda _prompt, _model, _artifact_dir: assisted_result("print('ok')"),
    )
    monkeypatch.setattr(
        cadbench,
        "try_execute_freecad_script",
        lambda _script, _suffix, _artifact_dir: cadbench.FreeCADExecutionResult(
            None,
            error_info=(
                "FreeCAD Docker execution failed with return code 125\n"
                "docker: Cannot connect to the Docker daemon"
            ),
        ),
    )

    result = cadbench.build_model_result("prompt", cadbench.DEFAULT_MODEL, "_model1", tmp_path)

    assert result["error"] == "FreeCAD script failed to generate a model"
    assert "Cannot connect to the Docker daemon" in result["error_details"]


def test_build_model_result_uses_assisted_generation(monkeypatch, tmp_path):
    def fake_assisted_generation(user_prompt, model_name, artifact_dir):
        assert user_prompt == "make a box with a hole"
        assert model_name == cadbench.DEFAULT_MODEL
        assert artifact_dir.name == "mcp_model1_initial"
        return assisted_result(
            'doc.saveAs("/data/output.FCStd")',
            [{"name": "search_docs", "result": {"success": True}}],
        )

    monkeypatch.setattr(cadbench, "generate_code_with_mcp_assistance", fake_assisted_generation)
    monkeypatch.setattr(
        cadbench,
        "try_execute_freecad_script",
        lambda _script, suffix, artifact_dir: cadbench.FreeCADExecutionResult(
            artifact_dir / f"output{suffix}.FCStd",
            artifact_dir / f"output{suffix}.stl",
        ),
    )

    result = cadbench.build_model_result(
        "make a box with a hole",
        cadbench.DEFAULT_MODEL,
        "_model1",
        tmp_path,
    )

    assert result["mode"] == "mcp"
    assert result["tool_trace"] == [{"name": "search_docs", "result": {"success": True}}]
    assert result["fcstd_url"].endswith("/output_model1.FCStd")


def test_mcp_assistance_falls_back_to_context_prompt_when_tools_are_rejected(monkeypatch, tmp_path):
    stub_mcp_tool_bridge(monkeypatch)

    def reject_tools(*_args, **_kwargs):
        raise RuntimeError("OpenRouter error 400: tools are not supported by this model")

    monkeypatch.setattr(cadbench.openrouter_client, "generate_text_with_openrouter_tools", reject_tools)
    monkeypatch.setattr(
        cadbench.openrouter_client,
        "generate_code_with_openrouter",
        lambda prompt, model_name, system_prompt=None: "print('context fallback')",
    )

    result = cadbench.mcp_assistant.generate_code_with_mcp_assistance(
        "make a bracket",
        cadbench.DEFAULT_MODEL,
        tmp_path,
    )

    assert result.script == "print('context fallback')"
    assert [entry["name"] for entry in result.tool_trace[:4]] == [
        "search_docs",
        "search_freecad_api_docs",
        "get_examples",
        "openrouter_tool_calling_fallback",
    ]
    assert "context only" in result.tool_trace[3]["result"]["fallback"]


def test_context_bundle_uses_freecad_queries_instead_of_raw_user_prompt():
    _bundle, trace = freecad_context.build_context_bundle_with_trace("A small cargo airplane.")

    assert trace[0]["arguments"]["query"] == freecad_context.DEFAULT_CONTEXT_DOC_QUERY
    assert trace[1]["arguments"]["query"] == freecad_context.DEFAULT_API_DOC_QUERY
    assert trace[2]["arguments"]["topic"] == freecad_context.DEFAULT_EXAMPLE_TOPIC
    assert all("A small cargo airplane" not in str(entry["arguments"]) for entry in trace)


def test_mcp_assistance_sends_cadbench_system_prompt(monkeypatch, tmp_path):
    stub_mcp_tool_bridge(monkeypatch)
    captured = {}

    def fake_generate(
        prompt,
        model_name,
        tools,
        execute_tool,
        max_tool_rounds=3,
        system_prompt=None,
        final_response_policy=None,
    ):
        captured["system_prompt"] = system_prompt
        captured["prompt"] = prompt
        captured["final_response_policy"] = final_response_policy
        return cadbench.openrouter_client.ToolCompletionResult("print('ok')")

    monkeypatch.setattr(cadbench.openrouter_client, "generate_text_with_openrouter_tools", fake_generate)

    result = cadbench.mcp_assistant.generate_code_with_mcp_assistance(
        "make a bracket",
        cadbench.DEFAULT_MODEL,
        tmp_path,
    )

    assert result.script == "print('ok')"
    assert captured["system_prompt"] == CADBENCH_SYSTEM_PROMPT
    assert "User request: make a bracket" in captured["prompt"]


def test_mcp_repair_trace_includes_known_error_context(monkeypatch, tmp_path):
    stub_mcp_tool_bridge(monkeypatch)
    monkeypatch.setattr(
        cadbench.openrouter_client,
        "generate_text_with_openrouter_tools",
        lambda *_args, **_kwargs: cadbench.openrouter_client.ToolCompletionResult("print('fixed')"),
    )

    result = cadbench.mcp_assistant.repair_code_with_mcp_assistance(
        "make a bracket",
        cadbench.DEFAULT_MODEL,
        "broken()",
        "AttributeError: object has no attribute makeGear",
        tmp_path,
    )

    assert result.script == "print('fixed')"
    assert [entry["name"] for entry in result.tool_trace[:4]] == [
        "search_docs",
        "search_freecad_api_docs",
        "get_examples",
        "known_error_fix",
    ]


def test_mcp_assistance_does_not_fallback_after_rate_limit(monkeypatch, tmp_path):
    stub_mcp_tool_bridge(monkeypatch)

    def reject_tools(*_args, **_kwargs):
        raise RuntimeError("OpenRouter rate limit: Provider returned error")

    monkeypatch.setattr(cadbench.openrouter_client, "generate_text_with_openrouter_tools", reject_tools)

    try:
        cadbench.mcp_assistant.generate_code_with_mcp_assistance(
            "make a bracket",
            cadbench.DEFAULT_MODEL,
            tmp_path,
        )
    except RuntimeError as exc:
        assert str(exc) == "OpenRouter rate limit: Provider returned error"
    else:
        raise AssertionError("Expected RuntimeError")


def test_context_tools_return_relevant_guidance():
    docs = freecad_context.search_docs("centered cylinder hole through a box", limit=2)
    api = freecad_context.search_freecad_api_docs(
        "cylinder primitive",
        symbol="Part.makeCylinder",
        limit=2,
    )
    fixes = freecad_context.known_error_fix("AttributeError: Shape has no attribute makeHole")

    assert docs["success"]
    assert any("Boolean" in result["title"] or "primitive" in result["title"].lower() for result in docs["results"])
    assert api["success"]
    assert api["runtime_docker_image"] == "linuxserver/freecad:0.20.2"
    assert api["results"][0]["matched_symbol"] == "Part.makeCylinder"
    assert "Part.makeCylinder(radius, height)" in api["results"][0]["usage"]
    assert api["results"][0]["source_url"].startswith("https://wiki.freecad.org/")
    assert "documented Part functions" in fixes["fixes"][0]


def test_freecad_api_docs_can_include_live_wiki_results(monkeypatch):
    responses = [
        {
            "query": {
                "search": [
                    {
                        "pageid": 123,
                        "title": "Part scripting",
                        "snippet": "Part <span class=\"searchmatch\">scripting</span> docs",
                    }
                ]
            }
        },
        {"query": {"pages": {"123": {"extract": "Part scripting introduction."}}}},
    ]

    def fake_get(url, params=None, timeout=None):
        assert url == freecad_context.FREECAD_WIKI_API_URL
        assert timeout == freecad_context.FREECAD_WIKI_TIMEOUT_SECONDS
        request = freecad_context.httpx.Request("GET", url)
        return freecad_context.httpx.Response(200, request=request, json=responses.pop(0))

    monkeypatch.setattr(freecad_context.httpx, "get", fake_get)

    result = freecad_context.search_freecad_api_docs("Part scripting", live_wiki=True)

    assert result["success"]
    assert result["live_wiki"]["success"]
    assert result["live_wiki"]["results"][0]["source_url"] == "https://wiki.freecad.org/Part_scripting"
    assert result["live_wiki"]["results"][0]["snippet"] == "Part scripting docs"


def test_validation_tool_server_tracks_runs_and_geometry(monkeypatch, tmp_path):
    fcstd_path = tmp_path / "output_tool.FCStd"
    stl_path = tmp_path / "output_tool.stl"
    fcstd_path.write_bytes(b"fcstd")
    stl_path.write_bytes(b"stl")

    def fake_execute(script, file_suffix="", artifact_dir=None):
        assert file_suffix == "_tool"
        assert "/data/output_tool.FCStd" in script
        return cadbench.FreeCADExecutionResult(fcstd_path, stl_path)

    monkeypatch.setattr(freecad_validation.freecad_runner, "try_execute_freecad_script", fake_execute)
    monkeypatch.setattr(
        freecad_validation,
        "inspect_fcstd_file",
        lambda path: {
            "success": True,
            "object_count": 1,
            "solid_count": 1,
            "face_count": 6,
            "edge_count": 12,
            "volume": 1000,
            "bbox": {"x_length": 10, "y_length": 10, "z_length": 10},
            "warnings": [],
        },
    )

    server = freecad_validation.FreeCADValidationToolServer(tmp_path)
    run_result = server.run_freecad_script('doc.saveAs("/data/output.FCStd")')
    measurement = server.measure_geometry("last")
    export = server.export_stl("last")

    assert run_result["success"] is True
    assert measurement["solid_count"] == 1
    assert export["already_exported"] is True


def test_validation_tool_server_renders_views_and_reports_mesh_quality(tmp_path):
    stl_path = tmp_path / "tetra.stl"
    _write_tetra_stl(stl_path)
    server = freecad_validation.FreeCADValidationToolServer(tmp_path)
    server.artifacts["run"] = freecad_validation.ValidationArtifact(stl_path=stl_path)
    server.last_handle = "run"

    render = server.render_model_views("last", image_size=128, include_data_urls=False)
    mesh = server.mesh_quality_report("last")

    assert render["success"] is True
    assert set(render["views"]) == {"front", "top", "side", "isometric"}
    assert render["triangle_count"] == 4
    for view in render["views"].values():
        assert view["width"] == 128
        assert view["colored_pixel_count"] > 0
        assert open(view["path"], "rb").read(8) == b"\x89PNG\r\n\x1a\n"
    assert mesh["success"] is True
    assert mesh["triangle_count"] == 4
    assert mesh["watertight"] is True
    assert mesh["component_count"] == 1


def test_validation_tool_server_runs_shape_health_check(monkeypatch, tmp_path):
    fcstd_path = tmp_path / "output_tool.FCStd"
    fcstd_path.write_bytes(b"fcstd")
    server = freecad_validation.FreeCADValidationToolServer(tmp_path)
    server.artifacts["run"] = freecad_validation.ValidationArtifact(fcstd_path=fcstd_path)
    server.last_handle = "run"

    monkeypatch.setattr(
        freecad_validation,
        "shape_health_check_file",
        lambda path: {
            "success": True,
            "valid": True,
            "source": str(path),
            "checks": [],
            "warnings": [],
        },
    )

    health = server.shape_health_check("last")

    assert health["success"] is True
    assert health["valid"] is True
    assert health["source"] == str(fcstd_path)


def test_cleanup_old_artifacts_removes_only_expired_directories(monkeypatch, tmp_path):
    old_dir = tmp_path / "old"
    fresh_dir = tmp_path / "fresh"
    old_dir.mkdir()
    fresh_dir.mkdir()
    old_time = 1_000
    fresh_time = old_time + cadbench.ARTIFACT_TTL_SECONDS + 1
    os.utime(old_dir, (old_time, old_time))
    os.utime(fresh_dir, (fresh_time, fresh_time))

    monkeypatch.setattr(cadbench, "ARTIFACTS_DIR", tmp_path)

    cadbench.cleanup_old_artifacts(now=fresh_time)

    assert not old_dir.exists()
    assert fresh_dir.exists()


def _write_tetra_stl(path):
    vertices = {
        "a": (0, 0, 0),
        "b": (1, 0, 0),
        "c": (0, 1, 0),
        "d": (0, 0, 1),
    }
    faces = [
        ("a", "c", "b"),
        ("a", "b", "d"),
        ("b", "c", "d"),
        ("c", "a", "d"),
    ]
    lines = ["solid tetra"]
    for face in faces:
        lines.extend(["  facet normal 0 0 0", "    outer loop"])
        for key in face:
            x, y, z = vertices[key]
            lines.append(f"      vertex {x} {y} {z}")
        lines.extend(["    endloop", "  endfacet"])
    lines.append("endsolid tetra")
    path.write_text("\n".join(lines))
