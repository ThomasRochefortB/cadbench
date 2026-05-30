import os

import app as cadbench
import freecad_context
import freecad_validation


def assisted_result(script: str, tool_trace: list[dict] | None = None) -> cadbench.mcp_assistant.MCPAssistedResult:
    return cadbench.mcp_assistant.MCPAssistedResult(script=script, tool_trace=tool_trace or [])


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

    assert captured["url"] == cadbench.OPENROUTER_USER_MODELS_URL
    assert captured["headers"] == {"Authorization": "Bearer sk-or-test"}
    assert models == [
        {
            "id": "provider/account-visible:free",
            "name": "Account Visible",
            "provider": "provider",
            "context_length": None,
            "free": True,
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
    assert models[1]["pricing"] == {"prompt": "0.000001", "completion": "0.000002"}


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

    def fake_execute(*_args, **_kwargs):
        nonlocal execute_called
        execute_called = True
        return cadbench.FreeCADExecutionResult(None)

    monkeypatch.setattr(cadbench, "try_execute_freecad_script", fake_execute)

    result = cadbench.build_model_result("prompt", cadbench.DEFAULT_MODEL, "_model1", tmp_path)

    assert execute_called is False
    assert result["script"] == "import FreeCAD as App\npts.append(App.Vector(r_"
    assert result["error"].startswith("Generated script was not valid Python:")
    assert "Generated script failed Python syntax validation" in result["stages"]


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
    def reject_tools(*_args, **_kwargs):
        raise RuntimeError("OpenRouter error 400: tools are not supported by this model")

    monkeypatch.setattr(cadbench.openrouter_client, "generate_text_with_openrouter_tools", reject_tools)
    monkeypatch.setattr(
        cadbench.openrouter_client,
        "generate_code_with_openrouter",
        lambda prompt, model_name: "print('context fallback')",
    )

    result = cadbench.mcp_assistant.generate_code_with_mcp_assistance(
        "make a bracket",
        cadbench.DEFAULT_MODEL,
        tmp_path,
    )

    assert result.script == "print('context fallback')"
    assert result.tool_trace[0]["name"] == "tool_calling_fallback"
    assert "context only" in result.tool_trace[0]["result"]["fallback"]


def test_mcp_assistance_does_not_fallback_after_rate_limit(monkeypatch, tmp_path):
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
    api = freecad_context.lookup_api("Part.makeCylinder")
    fixes = freecad_context.known_error_fix("AttributeError: Shape has no attribute makeHole")

    assert docs["success"]
    assert any("Boolean" in result["title"] or "primitive" in result["title"].lower() for result in docs["results"])
    assert api["success"]
    assert api["signature"] == "Part.makeCylinder(radius, height)"
    assert "documented Part functions" in fixes["fixes"][0]


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
