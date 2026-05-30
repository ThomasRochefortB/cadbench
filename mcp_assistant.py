from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import freecad_context
import freecad_validation
import openrouter_client
from prompts import MCP_ASSISTED_PROMPT_TEMPLATE, MCP_REPAIR_PROMPT_TEMPLATE


@dataclass
class MCPAssistedResult:
    script: str
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    context_bundle: str = ""


@dataclass
class CADMCPToolExecutor:
    artifact_dir: Path
    validation_server: freecad_validation.FreeCADValidationToolServer = field(init=False)

    def __post_init__(self) -> None:
        self.validation_server = freecad_validation.FreeCADValidationToolServer(self.artifact_dir)

    def tool_specs(self) -> list[dict[str, Any]]:
        return [
            *freecad_context.freecad_context_tool_specs(),
            *self.validation_server.tool_specs(),
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        context_names = {"search_docs", "lookup_api", "get_examples", "known_error_fix"}
        if name in context_names:
            return freecad_context.execute_freecad_context_tool(name, arguments)
        return self.validation_server.execute(name, arguments)


def generate_code_with_mcp_assistance(
    user_prompt: str,
    model_name: str,
    artifact_dir: Path,
) -> MCPAssistedResult:
    context_bundle = freecad_context.build_context_bundle(user_prompt)
    prompt = MCP_ASSISTED_PROMPT_TEMPLATE.format(
        context_bundle=context_bundle,
        user_prompt=user_prompt,
    )
    completion = _run_mcp_prompt(prompt, model_name, artifact_dir)
    return MCPAssistedResult(
        script=completion.content,
        tool_trace=completion.tool_trace,
        context_bundle=context_bundle,
    )


def repair_code_with_mcp_assistance(
    user_prompt: str,
    model_name: str,
    script: str,
    error_details: str,
    artifact_dir: Path,
) -> MCPAssistedResult:
    context_bundle = freecad_context.build_context_bundle(user_prompt, error_details=error_details)
    prompt = MCP_REPAIR_PROMPT_TEMPLATE.format(
        context_bundle=context_bundle,
        user_prompt=user_prompt,
        script=script,
        error_details=error_details,
    )
    completion = _run_mcp_prompt(prompt, model_name, artifact_dir)
    return MCPAssistedResult(
        script=completion.content,
        tool_trace=completion.tool_trace,
        context_bundle=context_bundle,
    )


def _run_mcp_prompt(
    prompt: str,
    model_name: str,
    artifact_dir: Path,
) -> openrouter_client.ToolCompletionResult:
    executor = CADMCPToolExecutor(artifact_dir)
    try:
        return openrouter_client.generate_text_with_openrouter_tools(
            prompt,
            model_name,
            executor.tool_specs(),
            executor.execute,
            max_tool_rounds=3,
        )
    except RuntimeError as exc:
        if openrouter_client.is_openrouter_rate_limit_error(exc):
            raise
        content = openrouter_client.generate_code_with_openrouter(prompt, model_name)
        return openrouter_client.ToolCompletionResult(
            content=content,
            tool_trace=[
                {
                    "name": "tool_calling_fallback",
                    "result": {
                        "success": False,
                        "error": str(exc),
                        "fallback": "Generated with retrieved FreeCAD context only.",
                    },
                }
            ],
        )
