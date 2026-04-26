import os

import app as cadbench


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
    monkeypatch.setattr(cadbench, "fetch_openrouter_free_models", lambda: cadbench.fallback_model_info())
    client = cadbench.app.test_client()

    response = client.get("/api/models")

    assert response.status_code == 200
    model_ids = {model["id"] for model in response.get_json()}
    assert cadbench.DEFAULT_MODEL in model_ids
    assert all(model_id.endswith(":free") for model_id in model_ids)


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
        }
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
        cadbench,
        "fetch_openrouter_free_models",
        lambda: [{"id": "provider/live-model:free", "name": "Live Free Model"}],
    )

    model_ids = {model["id"] for model in cadbench.get_available_model_info()}

    assert "provider/live-model:free" in model_ids
    assert cadbench.DEFAULT_MODEL in model_ids


def test_generate_falls_back_invalid_models_and_uses_request_scoped_artifacts(monkeypatch):
    def fake_generate_code(user_prompt, model_name):
        assert user_prompt == "make a cube"
        assert model_name == cadbench.DEFAULT_MODEL
        return 'doc.save("/data/output.FCStd")'

    def fake_execute(script, file_suffix="", artifact_dir=None):
        assert "output_model" in script
        assert artifact_dir is not None
        return cadbench.FreeCADExecutionResult(
            artifact_dir / f"output{file_suffix}.FCStd",
            artifact_dir / f"output{file_suffix}.stl",
        )

    monkeypatch.setattr(cadbench, "generate_code_with_llm", fake_generate_code)
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


def test_build_model_result_reports_freecad_execution_details(monkeypatch, tmp_path):
    monkeypatch.setattr(cadbench, "generate_code_with_llm", lambda _prompt, _model: "print('ok')")
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


def test_build_model_result_reports_missing_fcstd_save(monkeypatch, tmp_path):
    monkeypatch.setattr(cadbench, "generate_code_with_llm", lambda _prompt, _model: "print('ok')")
    monkeypatch.setattr(cadbench, "repair_code_with_llm", lambda *_args: "print('still broken')")
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

    monkeypatch.setattr(cadbench, "generate_code_with_llm", lambda _prompt, _model: "broken()")
    monkeypatch.setattr(cadbench, "repair_code_with_llm", lambda *_args: 'doc.saveAs("/data/output.FCStd")')

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
    monkeypatch.setattr(cadbench, "generate_code_with_llm", lambda _prompt, _model: "print('ok')")
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
