import json
import os
import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings

from config import ARTIFACTS_DIR
import freecad_context
import freecad_validation
from prompts import MCP_ASSISTED_PROMPT_TEMPLATE, MCP_REPAIR_PROMPT_TEMPLATE


MCP_SERVER_NAME = "CADBench FreeCAD Tools"
DEFAULT_MCP_VALIDATION_DIR = ARTIFACTS_DIR / "mcp_validation"


def create_cadbench_mcp_server(
    artifact_root: Path | None = None,
    streamable_http_path: str = "/mcp",
) -> FastMCP:
    artifact_root = artifact_root or DEFAULT_MCP_VALIDATION_DIR
    validation_artifacts: dict[str, freecad_validation.ValidationArtifact] = {}
    validation_servers: dict[str, freecad_validation.FreeCADValidationToolServer] = {}

    mcp = FastMCP(
        MCP_SERVER_NAME,
        instructions=(
            "CADBench FreeCAD context and validation server. Use documentation tools with FreeCAD API concepts, "
            "then validate complete candidate scripts before returning final code."
        ),
        json_response=True,
        streamable_http_path=streamable_http_path,
        transport_security=TransportSecuritySettings(
            allowed_hosts=_allowed_hosts(),
        ),
    )

    def validation_server_for_context(ctx: Context) -> freecad_validation.FreeCADValidationToolServer:
        client_key = _safe_client_key(ctx)
        if client_key not in validation_servers:
            validation_servers[client_key] = freecad_validation.FreeCADValidationToolServer(
                artifact_root / client_key,
                artifacts=validation_artifacts,
            )
        return validation_servers[client_key]

    @mcp.tool(
        description=(
            "Search concise, version-pinned FreeCAD scripting guidance. Query with short FreeCAD technique "
            "keywords such as booleans, placements, document save, fillets, or primitives; do not pass the user's "
            "raw object description."
        )
    )
    def search_docs(query: str, limit: int = 3) -> dict[str, Any]:
        return freecad_context.search_docs(query, limit)

    @mcp.tool(
        description=(
            "Search FreeCAD wiki documentation and Python API patterns. Query with API symbols or implementation "
            "concepts, not the raw object being modeled."
        )
    )
    def search_freecad_api_docs(
        query: str,
        symbol: str = "",
        limit: int = 4,
        headless_only: bool = True,
        live_wiki: bool = False,
    ) -> dict[str, Any]:
        return freecad_context.search_freecad_api_docs(query, symbol, limit, headless_only, live_wiki)

    @mcp.tool(
        description=(
            "Retrieve short FreeCAD Python examples for a modeling technique such as booleans, placements, "
            "fillets, or document save; do not pass the user's raw object description."
        )
    )
    def get_examples(topic: str, limit: int = 2) -> dict[str, Any]:
        return freecad_context.get_examples(topic, limit)

    @mcp.tool(description="Map a FreeCAD execution error to known repair advice.")
    def known_error_fix(error_message: str) -> dict[str, Any]:
        return freecad_context.known_error_fix(error_message)

    @mcp.tool(
        description="Run a complete candidate FreeCAD Python script in the same headless Docker sandbox CADBench uses."
    )
    def run_freecad_script(script: str, ctx: Context) -> dict[str, Any]:
        return validation_server_for_context(ctx).run_freecad_script(script)

    @mcp.tool(description="Inspect the FCStd document from a previous validation run and return object/topology metrics.")
    def inspect_fcstd(ctx: Context, handle: str = "last") -> dict[str, Any]:
        return validation_server_for_context(ctx).inspect_fcstd(handle)

    @mcp.tool(description="Report STL export status for a previous validation run.")
    def export_stl(ctx: Context, handle: str = "last") -> dict[str, Any]:
        return validation_server_for_context(ctx).export_stl(handle)

    @mcp.tool(description="Return compact solid, bounding-box, face, edge, and volume metrics for a validation run.")
    def measure_geometry(ctx: Context, handle: str = "last") -> dict[str, Any]:
        return validation_server_for_context(ctx).measure_geometry(handle)

    @mcp.tool(
        description=(
            "Render front, top, side, and isometric PNG views from the validation STL. "
            "Only expose this tool to vision-capable OpenRouter models."
        )
    )
    def render_model_views(
        ctx: Context,
        handle: str = "last",
        image_size: int = freecad_validation.DEFAULT_RENDER_IMAGE_SIZE,
        include_data_urls: bool = True,
    ) -> dict[str, Any]:
        return validation_server_for_context(ctx).render_model_views(handle, image_size, include_data_urls)

    @mcp.tool(
        description=(
            "Run deeper FreeCAD/OpenCascade shape validity checks including Shape.isValid(), Shape.check(), "
            "non-solid shells, open wires, and tiny sliver faces."
        )
    )
    def shape_health_check(ctx: Context, handle: str = "last") -> dict[str, Any]:
        return validation_server_for_context(ctx).shape_health_check(handle)

    @mcp.tool(
        description=(
            "Analyze the exported STL for watertightness, manifoldness, degenerate triangles, components, "
            "normals, and bounding-box sanity."
        )
    )
    def mesh_quality_report(ctx: Context, handle: str = "last") -> dict[str, Any]:
        return validation_server_for_context(ctx).mesh_quality_report(handle)

    @mcp.resource(
        "cadbench://freecad/context",
        name="Curated FreeCAD Context",
        description="CADBench's concise headless FreeCAD scripting guidance.",
        mime_type="application/json",
    )
    def curated_context_resource() -> str:
        return json.dumps([entry.__dict__ for entry in freecad_context.CONTEXT_ENTRIES], ensure_ascii=True)

    @mcp.resource(
        "cadbench://freecad/api-docs",
        name="Curated FreeCAD API Docs",
        description="Version-aware FreeCAD API documentation snippets and source URLs.",
        mime_type="application/json",
    )
    def api_docs_resource() -> str:
        return json.dumps([entry.__dict__ for entry in freecad_context.FREECAD_DOC_ENTRIES], ensure_ascii=True)

    @mcp.resource(
        "cadbench://freecad/examples",
        name="Curated FreeCAD Examples",
        description="Short FreeCAD Python examples used by CADBench generation.",
        mime_type="application/json",
    )
    def examples_resource() -> str:
        return json.dumps([entry.__dict__ for entry in freecad_context.EXAMPLES], ensure_ascii=True)

    @mcp.prompt(
        name="cadbench_generate_script",
        description="Create a CADBench FreeCAD generation prompt for a user modeling request.",
    )
    def cadbench_generate_script(user_prompt: str, context_bundle: str = "") -> str:
        return MCP_ASSISTED_PROMPT_TEMPLATE.format(
            context_bundle=context_bundle,
            user_prompt=user_prompt,
        )

    @mcp.prompt(
        name="cadbench_repair_script",
        description="Create a CADBench FreeCAD repair prompt for a failed script.",
    )
    def cadbench_repair_script(
        user_prompt: str,
        script: str,
        error_details: str,
        context_bundle: str = "",
    ) -> str:
        return MCP_REPAIR_PROMPT_TEMPLATE.format(
            context_bundle=context_bundle,
            user_prompt=user_prompt,
            script=script,
            error_details=error_details,
        )

    return mcp


def _safe_client_key(ctx: Context) -> str:
    raw_key = str(ctx.client_id or ctx.request_id or "direct")
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_key).strip("._")
    return safe_key or "direct"


def _allowed_hosts() -> list[str]:
    configured_hosts = os.getenv("CADBENCH_MCP_ALLOWED_HOSTS")
    if configured_hosts:
        return [host.strip() for host in configured_hosts.split(",") if host.strip()]
    return [
        "127.0.0.1",
        "127.0.0.1:8000",
        "127.0.0.1:8001",
        "localhost",
        "localhost:8000",
        "localhost:8001",
        "testserver",
    ]


cadbench_mcp = create_cadbench_mcp_server(streamable_http_path="/")


if __name__ == "__main__":
    create_cadbench_mcp_server(streamable_http_path="/mcp").run(transport="streamable-http")
