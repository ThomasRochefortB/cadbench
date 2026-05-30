import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import httpx

import config
import freecad_runner
import mcp_assistant
import model_catalog
import openrouter_client
import script_normalizer
from config import ARTIFACTS_DIR, ARTIFACT_TTL_SECONDS, DEFAULT_MODEL

app = Flask(__name__, static_url_path="", static_folder="static")
CORS(app)

load_dotenv()

OPENROUTER_USER_MODELS_URL = config.OPENROUTER_USER_MODELS_URL
FreeCADExecutionResult = freecad_runner.FreeCADExecutionResult
_HTTPX_RESPONSE_TYPE = httpx.Response
GENERATION_MODE_MCP_ASSISTED = "mcp"


def make_docker_command(tmpdir_path: Path, script_name: str) -> list[str]:
    return freecad_runner.make_docker_command(tmpdir_path, script_name)


def try_execute_freecad_script(
    script: str, file_suffix: str = "", artifact_dir: Path | None = None
) -> FreeCADExecutionResult:
    return freecad_runner.try_execute_freecad_script(script, file_suffix, artifact_dir)


def strip_markdown_code_fence(script: str) -> str:
    return script_normalizer.strip_markdown_code_fence(script)


def prepare_freecad_script(script: str, file_suffix: str) -> str:
    return script_normalizer.prepare_freecad_script(script, file_suffix)


def extract_openrouter_output_text(response_json: dict) -> str:
    return openrouter_client.extract_openrouter_output_text(response_json)


def format_openrouter_http_error(response: httpx.Response) -> str:
    return openrouter_client.format_openrouter_http_error(response)


def openrouter_max_tokens() -> int:
    return openrouter_client.openrouter_max_tokens()


def generate_code_with_openrouter(prompt: str, model_name: str) -> str:
    return openrouter_client.generate_code_with_openrouter(prompt, model_name)


def generate_text_with_openrouter_tools(*args, **kwargs) -> openrouter_client.ToolCompletionResult:
    return openrouter_client.generate_text_with_openrouter_tools(*args, **kwargs)


def generate_code_with_mcp_assistance(
    user_prompt: str, model_name: str, artifact_dir: Path
) -> mcp_assistant.MCPAssistedResult:
    return mcp_assistant.generate_code_with_mcp_assistance(user_prompt, model_name, artifact_dir)


def repair_code_with_mcp_assistance(
    user_prompt: str, model_name: str, script: str, error_details: str, artifact_dir: Path
) -> mcp_assistant.MCPAssistedResult:
    return mcp_assistant.repair_code_with_mcp_assistance(
        user_prompt,
        model_name,
        script,
        error_details,
        artifact_dir,
    )


def is_free_openrouter_model(model: dict) -> bool:
    return model_catalog.is_free_openrouter_model(model)


def is_supported_model_id(model_name: str) -> bool:
    return model_catalog.is_supported_model_id(model_name)


def display_name_from_model_id(model_id: str) -> str:
    return model_catalog.display_name_from_model_id(model_id)


def fallback_model_info() -> list[dict]:
    return model_catalog.fallback_model_info()


def fetch_openrouter_free_models() -> list[dict]:
    return model_catalog.fetch_openrouter_free_models()


def fetch_openrouter_models(free_only: bool = True) -> list[dict]:
    return model_catalog.fetch_openrouter_models(free_only=free_only)


def get_available_model_info(free_only: bool = True) -> list[dict]:
    return model_catalog.get_available_model_info(free_only=free_only)


def cleanup_old_artifacts(now: float | None = None) -> None:
    """Remove old generated files so the local static directory does not grow forever."""
    now = now or time.time()
    if not ARTIFACTS_DIR.exists():
        return

    for child in ARTIFACTS_DIR.iterdir():
        try:
            if child.is_dir() and now - child.stat().st_mtime > ARTIFACT_TTL_SECONDS:
                shutil.rmtree(child)
        except OSError as exc:
            print(f"Unable to remove old artifact directory {child}: {exc}")


def artifact_url(path: Path) -> str:
    try:
        return "/" + path.relative_to(Path("static")).as_posix()
    except ValueError:
        return "/" + path.as_posix()


def build_model_result(
    user_prompt: str,
    model_name: str,
    file_suffix: str,
    artifact_dir: Path,
) -> dict:
    model_result = {"model": model_name, "mode": GENERATION_MODE_MCP_ASSISTED, "stages": []}

    try:
        model_result["stages"].append("Generated initial script")
        generation_result = generate_code_with_mcp_assistance(
            user_prompt,
            model_name,
            artifact_dir / f"mcp{file_suffix}_initial",
        )
        script = generation_result.script
        tool_trace = generation_result.tool_trace
        if tool_trace:
            model_result["tool_trace"] = tool_trace
            model_result["stages"].append("Used FreeCAD context/validation tools")
        try:
            script = prepare_freecad_script(script, file_suffix)
        except ValueError as exc:
            model_result["script"] = script
            model_result["error"] = f"Generated script was not valid Python: {exc}"
            model_result["stages"].append("Generated script failed Python syntax validation")
            return model_result
        execution = try_execute_freecad_script(script, file_suffix, artifact_dir)

        if should_repair_execution(execution):
            model_result["stages"].append("Initial FreeCAD run failed; requested repair")
            try:
                repair_result = repair_code_with_mcp_assistance(
                    user_prompt,
                    model_name,
                    script,
                    execution.error_info or "",
                    artifact_dir / f"mcp{file_suffix}_repair",
                )
                repaired_script = repair_result.script
                repair_tool_trace = repair_result.tool_trace
                if repair_tool_trace:
                    model_result["repair_tool_trace"] = repair_tool_trace
                    model_result["stages"].append("Used FreeCAD repair tools")
                try:
                    repaired_script = prepare_freecad_script(repaired_script, file_suffix)
                except ValueError as exc:
                    model_result["repair_attempted"] = True
                    model_result["repair_script"] = repaired_script
                    model_result["repair_error_details"] = f"Repaired script was not valid Python: {exc}"
                    model_result["stages"].append("Repair failed Python syntax validation")
                    model_result["script"] = script
                    add_execution_result(model_result, execution)
                    return model_result
                repaired_execution = try_execute_freecad_script(repaired_script, file_suffix, artifact_dir)
                if repaired_execution.fcstd_path:
                    model_result["repaired"] = True
                    model_result["original_script"] = script
                    script = repaired_script
                    execution = repaired_execution
                    model_result["stages"].append("Repair succeeded")
                else:
                    model_result["repair_attempted"] = True
                    model_result["repair_script"] = repaired_script
                    model_result["repair_error_details"] = repaired_execution.error_info
                    model_result["stages"].append("Repair did not produce a model")
            except Exception as repair_exc:
                model_result["repair_attempted"] = True
                model_result["repair_error_details"] = f"Repair request failed: {repair_exc}"
                model_result["stages"].append("Repair request failed")

        model_result["script"] = script
        add_execution_result(model_result, execution)

    except Exception as exc:
        model_result["script"] = f"# Error: {str(exc)}"
        error_message = str(exc)
        if openrouter_client.is_openrouter_rate_limit_error(error_message):
            model_result["error"] = openrouter_client.openrouter_rate_limit_guidance(error_message)
        else:
            model_result["error"] = f"Failed to generate script: {error_message}"

    return model_result


def should_repair_execution(execution: FreeCADExecutionResult) -> bool:
    if execution.fcstd_path or not execution.error_info:
        return False
    return not any(blocker in execution.error_info for blocker in ["Docker command not found", "Docker daemon"])


def add_execution_result(model_result: dict, execution: FreeCADExecutionResult) -> None:
    if execution.fcstd_path:
        model_result["fcstd_url"] = artifact_url(execution.fcstd_path)
    if execution.stl_path:
        model_result["stl_url"] = artifact_url(execution.stl_path)

    stderr_output = execution.error_info or ""
    if execution.fcstd_path:
        return

    if "expected output.FCStd file was not created" in stderr_output:
        model_result["error"] = "The script ran but did not save an FCStd model"
    elif "has no attribute" in stderr_output:
        model_result["error"] = "The LLM generated code with an invalid FreeCAD function"
    elif "Exception" in stderr_output:
        model_result["error"] = "The FreeCAD script had execution errors"
    else:
        model_result["error"] = "FreeCAD script failed to generate a model"

    if execution.error_info:
        model_result["error_details"] = execution.error_info


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True)
    user_prompt = data.get("prompt", "").strip()
    if not user_prompt:
        return jsonify({"error": "Prompt is required"}), 400

    cleanup_old_artifacts()

    models = [data.get("model1", DEFAULT_MODEL)]
    if data.get("model2"):
        models.append(data.get("model2", DEFAULT_MODEL))
    models = [model if is_supported_model_id(model) else DEFAULT_MODEL for model in models]

    request_artifact_dir = ARTIFACTS_DIR / uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=len(models)) as executor:
        futures = [
            executor.submit(
                build_model_result,
                user_prompt,
                model_name,
                f"_model{index}",
                request_artifact_dir,
            )
            for index, model_name in enumerate(models, start=1)
        ]
        results = [future.result() for future in futures]

    return jsonify({"mode": GENERATION_MODE_MCP_ASSISTED, "results": results})


@app.route("/api/models", methods=["GET"])
def get_models():
    """Return live OpenRouter models, free-only by default."""
    free_only = request.args.get("free_only", "true").lower() not in {"0", "false", "no"}
    return jsonify(get_available_model_info(free_only=free_only))


@app.route("/")
def serve_index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    app.run(debug=True, port=8000)
