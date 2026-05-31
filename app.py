import json
from queue import Queue
import shutil
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from flask_cors import CORS
import httpx

import freecad_runner
import mcp_assistant
import model_catalog
import openrouter_client
import script_normalizer
from config import ARTIFACTS_DIR, ARTIFACT_TTL_SECONDS, DEFAULT_MODEL

app = Flask(__name__, static_url_path="", static_folder="static")
CORS(app)

load_dotenv()

FreeCADExecutionResult = freecad_runner.FreeCADExecutionResult
GENERATION_MODE_MCP_ASSISTED = "mcp"
ProgressCallback = Callable[[dict], None]


def make_docker_command(tmpdir_path: Path, script_name: str) -> list[str]:
    return freecad_runner.make_docker_command(tmpdir_path, script_name)


def try_execute_freecad_script(
    script: str, file_suffix: str = "", artifact_dir: Path | None = None
) -> FreeCADExecutionResult:
    return freecad_runner.try_execute_freecad_script(script, file_suffix, artifact_dir)


def prepare_freecad_script(script: str, file_suffix: str) -> str:
    return script_normalizer.prepare_freecad_script(script, file_suffix)


def extract_openrouter_output_text(response_json: dict) -> str:
    return openrouter_client.extract_openrouter_output_text(response_json)


def format_openrouter_http_error(response: httpx.Response) -> str:
    return openrouter_client.format_openrouter_http_error(response)


def generate_code_with_openrouter(prompt: str, model_name: str) -> str:
    return openrouter_client.generate_code_with_openrouter(prompt, model_name)


def generate_text_with_openrouter_tools(*args, **kwargs) -> openrouter_client.ToolCompletionResult:
    return openrouter_client.generate_text_with_openrouter_tools(*args, **kwargs)


def generate_code_with_mcp_assistance(
    user_prompt: str,
    model_name: str,
    artifact_dir: Path,
    vision_capable: bool = False,
    on_tool_call: mcp_assistant.ToolTraceCallback | None = None,
) -> mcp_assistant.MCPAssistedResult:
    return mcp_assistant.generate_code_with_mcp_assistance(
        user_prompt,
        model_name,
        artifact_dir,
        vision_capable=vision_capable,
        on_tool_call=on_tool_call,
    )


def repair_code_with_mcp_assistance(
    user_prompt: str,
    model_name: str,
    script: str,
    error_details: str,
    artifact_dir: Path,
    vision_capable: bool = False,
    on_tool_call: mcp_assistant.ToolTraceCallback | None = None,
) -> mcp_assistant.MCPAssistedResult:
    return mcp_assistant.repair_code_with_mcp_assistance(
        user_prompt,
        model_name,
        script,
        error_details,
        artifact_dir,
        vision_capable=vision_capable,
        on_tool_call=on_tool_call,
    )


def is_free_openrouter_model(model: dict) -> bool:
    return model_catalog.is_free_openrouter_model(model)


def is_supported_model_id(model_name: str) -> bool:
    return model_catalog.is_supported_model_id(model_name)


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


def record_stage(model_result: dict, stage: str, progress_callback: ProgressCallback | None = None) -> None:
    model_result["stages"].append(stage)
    if progress_callback:
        progress_callback({"type": "stage", "stage": stage})


def build_model_result(
    user_prompt: str,
    model_name: str,
    file_suffix: str,
    artifact_dir: Path,
    vision_capable: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> dict:
    model_result = {"model": model_name, "mode": GENERATION_MODE_MCP_ASSISTED, "stages": []}

    try:
        initial_tool_callback = _tool_progress_callback(progress_callback, "Initial MCP")
        generation_result = request_mcp_generation(
            user_prompt,
            model_name,
            artifact_dir / f"mcp{file_suffix}_initial",
            vision_capable,
            initial_tool_callback,
        )
        script = generation_result.script
        tool_trace = generation_result.tool_trace
        if tool_trace:
            model_result["tool_trace"] = tool_trace
            record_stage(model_result, "Used FreeCAD context/validation tools", progress_callback)
        record_stage(model_result, "Generated initial script", progress_callback)
        script = prepare_or_repair_generated_script(
            model_result,
            user_prompt,
            model_name,
            script,
            file_suffix,
            artifact_dir,
            vision_capable=vision_capable,
            progress_callback=progress_callback,
        )
        if script is None:
            return model_result
        execution = try_execute_freecad_script(script, file_suffix, artifact_dir)

        if should_repair_execution(execution):
            record_stage(model_result, "Initial FreeCAD run failed; requested repair", progress_callback)
            try:
                repair_tool_callback = _tool_progress_callback(progress_callback, "Repair MCP")
                repair_result = request_mcp_repair(
                    user_prompt,
                    model_name,
                    script,
                    execution.error_info or "",
                    artifact_dir / f"mcp{file_suffix}_repair",
                    vision_capable,
                    repair_tool_callback,
                )
                repaired_script = repair_result.script
                repair_tool_trace = repair_result.tool_trace
                if repair_tool_trace:
                    model_result["repair_tool_trace"] = repair_tool_trace
                    record_stage(model_result, "Used FreeCAD repair tools", progress_callback)
                try:
                    repaired_script = prepare_freecad_script(repaired_script, file_suffix)
                except ValueError as exc:
                    model_result["repair_attempted"] = True
                    model_result["repair_script"] = repaired_script
                    model_result["repair_error_details"] = f"Repaired script was not valid Python: {exc}"
                    record_stage(model_result, "Repair failed Python syntax validation", progress_callback)
                    model_result["script"] = script
                    add_execution_result(model_result, execution)
                    return model_result
                repaired_execution = try_execute_freecad_script(repaired_script, file_suffix, artifact_dir)
                if repaired_execution.fcstd_path:
                    model_result["repaired"] = True
                    model_result["original_script"] = script
                    script = repaired_script
                    execution = repaired_execution
                    record_stage(model_result, "Repair succeeded", progress_callback)
                else:
                    model_result["repair_attempted"] = True
                    model_result["repair_script"] = repaired_script
                    model_result["repair_error_details"] = repaired_execution.error_info
                    record_stage(model_result, "Repair did not produce a model", progress_callback)
            except Exception as repair_exc:
                model_result["repair_attempted"] = True
                model_result["repair_error_details"] = f"Repair request failed: {repair_exc}"
                record_stage(model_result, "Repair request failed", progress_callback)

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


def request_mcp_generation(
    user_prompt: str,
    model_name: str,
    artifact_dir: Path,
    vision_capable: bool,
    on_tool_call: mcp_assistant.ToolTraceCallback | None,
) -> mcp_assistant.MCPAssistedResult:
    kwargs = {}
    if vision_capable:
        kwargs["vision_capable"] = True
    if on_tool_call:
        kwargs["on_tool_call"] = on_tool_call
    return generate_code_with_mcp_assistance(user_prompt, model_name, artifact_dir, **kwargs)


def request_mcp_repair(
    user_prompt: str,
    model_name: str,
    script: str,
    error_details: str,
    artifact_dir: Path,
    vision_capable: bool,
    on_tool_call: mcp_assistant.ToolTraceCallback | None,
) -> mcp_assistant.MCPAssistedResult:
    kwargs = {}
    if vision_capable:
        kwargs["vision_capable"] = True
    if on_tool_call:
        kwargs["on_tool_call"] = on_tool_call
    return repair_code_with_mcp_assistance(
        user_prompt,
        model_name,
        script,
        error_details,
        artifact_dir,
        **kwargs,
    )


def _tool_progress_callback(
    progress_callback: ProgressCallback | None,
    phase: str,
) -> mcp_assistant.ToolTraceCallback | None:
    if not progress_callback:
        return None

    def report_tool_call(trace_entry: dict) -> None:
        progress_callback({"type": "tool_call", "phase": phase, "call": trace_entry})

    return report_tool_call


def prepare_or_repair_generated_script(
    model_result: dict,
    user_prompt: str,
    model_name: str,
    script: str,
    file_suffix: str,
    artifact_dir: Path,
    vision_capable: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> str | None:
    try:
        return prepare_freecad_script(script, file_suffix)
    except ValueError as exc:
        syntax_error_details = f"Generated script was not valid Python: {exc}"
        record_stage(
            model_result,
            "Generated script failed Python syntax validation; requested repair",
            progress_callback,
        )
        try:
            syntax_repair_tool_callback = _tool_progress_callback(progress_callback, "Syntax repair MCP")
            repair_result = request_mcp_repair(
                user_prompt,
                model_name,
                script,
                syntax_error_details,
                artifact_dir / f"mcp{file_suffix}_syntax_repair",
                vision_capable,
                syntax_repair_tool_callback,
            )
            repaired_script = repair_result.script
            repair_tool_trace = repair_result.tool_trace
            if repair_tool_trace:
                model_result["repair_tool_trace"] = repair_tool_trace
                record_stage(model_result, "Used FreeCAD syntax repair tools", progress_callback)
            try:
                prepared_repaired_script = prepare_freecad_script(repaired_script, file_suffix)
            except ValueError as repair_exc:
                model_result["repair_attempted"] = True
                model_result["script"] = script
                model_result["repair_script"] = repaired_script
                model_result["error"] = syntax_error_details
                model_result["repair_error_details"] = f"Syntax repair was not valid Python: {repair_exc}"
                record_stage(model_result, "Syntax repair failed Python validation", progress_callback)
                return None

            model_result["repaired"] = True
            model_result["repair_attempted"] = True
            model_result["original_script"] = script
            record_stage(model_result, "Syntax repair succeeded", progress_callback)
            return prepared_repaired_script
        except Exception as repair_exc:
            model_result["repair_attempted"] = True
            model_result["script"] = script
            model_result["error"] = syntax_error_details
            model_result["repair_error_details"] = f"Syntax repair request failed: {repair_exc}"
            record_stage(model_result, "Syntax repair request failed", progress_callback)
            return None


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


def generation_models_from_request_data(data: dict) -> list[tuple[str, bool]]:
    requested_models = [data.get("model1", DEFAULT_MODEL)]
    if data.get("model2"):
        requested_models.append(data.get("model2", DEFAULT_MODEL))
    models = []
    for requested_model in requested_models:
        model_name = requested_model if is_supported_model_id(requested_model) else DEFAULT_MODEL
        models.append((model_name, model_catalog.cached_model_is_vision_capable(model_name)))
    return models


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True)
    user_prompt = data.get("prompt", "").strip()
    if not user_prompt:
        return jsonify({"error": "Prompt is required"}), 400

    cleanup_old_artifacts()
    models = generation_models_from_request_data(data)

    request_artifact_dir = ARTIFACTS_DIR / uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=len(models)) as executor:
        futures = [
            executor.submit(
                build_model_result,
                user_prompt,
                model_name,
                f"_model{index}",
                request_artifact_dir,
                vision_capable,
            )
            for index, (model_name, vision_capable) in enumerate(models, start=1)
        ]
        results = [future.result() for future in futures]

    return jsonify({"mode": GENERATION_MODE_MCP_ASSISTED, "results": results})


@app.route("/api/generate/stream", methods=["POST"])
def generate_stream():
    data = request.get_json(force=True)
    user_prompt = data.get("prompt", "").strip()
    if not user_prompt:
        return jsonify({"error": "Prompt is required"}), 400

    cleanup_old_artifacts()
    models = generation_models_from_request_data(data)
    request_artifact_dir = ARTIFACTS_DIR / uuid.uuid4().hex

    return Response(
        stream_with_context(_stream_generation_events(user_prompt, models, request_artifact_dir)),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _stream_generation_events(
    user_prompt: str,
    models: list[tuple[str, bool]],
    request_artifact_dir: Path,
):
    events: Queue[dict] = Queue()
    results: list[dict | None] = [None] * len(models)
    executor = ThreadPoolExecutor(max_workers=len(models))

    def emit(event: dict) -> None:
        events.put(event)

    def run_model(index: int, model_name: str, vision_capable: bool) -> None:
        def progress(progress_event: dict) -> None:
            emit({"index": index, **progress_event})

        try:
            result = build_model_result(
                user_prompt,
                model_name,
                f"_model{index}",
                request_artifact_dir,
                vision_capable,
                progress,
            )
        except Exception as exc:
            result = {
                "model": model_name,
                "mode": GENERATION_MODE_MCP_ASSISTED,
                "stages": [],
                "script": f"# Error: {exc}",
                "error": f"Failed to generate script: {exc}",
            }
        results[index - 1] = result
        emit({"type": "result", "index": index, "result": result})

    try:
        for index, (model_name, vision_capable) in enumerate(models, start=1):
            executor.submit(run_model, index, model_name, vision_capable)

        yield _ndjson({"type": "start", "mode": GENERATION_MODE_MCP_ASSISTED, "model_count": len(models)})
        remaining = len(models)
        while remaining:
            event = events.get()
            if event.get("type") == "result":
                remaining -= 1
            yield _ndjson(event)
        yield _ndjson({"type": "done", "mode": GENERATION_MODE_MCP_ASSISTED, "results": results})
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _ndjson(event: dict) -> str:
    return json.dumps(event, ensure_ascii=True) + "\n"


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
