import asyncio
import contextlib

import httpx

import asgi
import cadbench_mcp_server
import freecad_validation
import mcp_assistant
from freecad_runner import FreeCADExecutionResult


EXPECTED_TOOLS = {
    "search_docs",
    "search_freecad_api_docs",
    "get_examples",
    "known_error_fix",
    "run_freecad_script",
    "inspect_fcstd",
    "export_stl",
    "measure_geometry",
}


def test_cadbench_mcp_server_lists_tools_resources_and_prompts():
    server = cadbench_mcp_server.create_cadbench_mcp_server(streamable_http_path="/")

    tools = asyncio.run(server.list_tools())
    resources = asyncio.run(server.list_resources())
    prompts = asyncio.run(server.list_prompts())

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert {str(resource.uri) for resource in resources} == {
        "cadbench://freecad/context",
        "cadbench://freecad/api-docs",
        "cadbench://freecad/examples",
    }
    assert {prompt.name for prompt in prompts} == {"cadbench_generate_script", "cadbench_repair_script"}


def test_cadbench_mcp_tool_delegates_to_context_logic():
    server = cadbench_mcp_server.create_cadbench_mcp_server(streamable_http_path="/")

    result = asyncio.run(server.call_tool("search_docs", {"query": "booleans", "limit": 1}))

    content, structured = result
    assert structured["success"] is True
    assert structured["results"][0]["id"] == "booleans"
    assert "Boolean operations" in content[0].text


def test_mcp_tool_bridge_discovers_and_calls_tools_through_asgi_mcp(monkeypatch, tmp_path):
    _stub_validation(monkeypatch, tmp_path)

    tools, result = asyncio.run(
        _with_asgi_bridge(lambda bridge: _discover_and_search(bridge))
    )

    assert {tool["function"]["name"] for tool in tools} == EXPECTED_TOOLS
    assert result["success"] is True
    assert result["results"][0]["id"] == "booleans"


def test_mcp_tool_bridge_keeps_last_validation_handle_per_bridge(monkeypatch, tmp_path):
    _stub_validation(monkeypatch, tmp_path)

    run_a, run_b, measure_a, measure_b, handle_a, handle_b = asyncio.run(
        _with_asgi_bridge(lambda bridge: _validate_with_two_bridges(bridge))
    )

    assert run_a["success"] is True
    assert run_b["success"] is True
    assert handle_a == run_a["handle"]
    assert handle_b == run_b["handle"]
    assert run_a["handle"] != run_b["handle"]
    assert measure_a["success"] is True
    assert measure_b["success"] is True


async def _with_asgi_bridge(operation):
    mcp_server = cadbench_mcp_server.create_cadbench_mcp_server(streamable_http_path="/")
    app = asgi.create_asgi_app(mcp_server)

    @contextlib.asynccontextmanager
    async def http_client_factory():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client

    async with app.router.lifespan_context(app):
        bridge = mcp_assistant.MCPToolBridge(
            server_url="http://testserver/mcp/",
            http_client_factory=http_client_factory,
        )
        return await operation(bridge)


async def _discover_and_search(bridge):
    tools = await bridge._list_openrouter_tools()
    result = await bridge._call_tool("search_docs", {"query": "booleans", "limit": 1})
    return tools, result


async def _validate_with_two_bridges(bridge_a):
    bridge_b = mcp_assistant.MCPToolBridge(
        server_url="http://testserver/mcp/",
        http_client_factory=bridge_a.http_client_factory,
    )
    run_a = await bridge_a._call_tool("run_freecad_script", {"script": 'doc.saveAs("/data/output.FCStd")'})
    bridge_a.last_validation_handle = run_a["handle"]
    run_b = await bridge_b._call_tool("run_freecad_script", {"script": 'doc.saveAs("/data/output.FCStd")'})
    bridge_b.last_validation_handle = run_b["handle"]
    measure_a = await bridge_a._call_tool(
        "measure_geometry",
        bridge_a._normalize_validation_arguments("measure_geometry", {"handle": "last"}),
    )
    measure_b = await bridge_b._call_tool(
        "measure_geometry",
        bridge_b._normalize_validation_arguments("measure_geometry", {"handle": "last"}),
    )
    return run_a, run_b, measure_a, measure_b, bridge_a.last_validation_handle, bridge_b.last_validation_handle


def _stub_validation(monkeypatch, tmp_path):
    def fake_execute(_script, file_suffix="", artifact_dir=None):
        fcstd_path = (artifact_dir or tmp_path) / f"output{file_suffix}.FCStd"
        stl_path = (artifact_dir or tmp_path) / f"output{file_suffix}.stl"
        return FreeCADExecutionResult(fcstd_path, stl_path)

    monkeypatch.setattr(freecad_validation.freecad_runner, "try_execute_freecad_script", fake_execute)
    monkeypatch.setattr(
        freecad_validation,
        "inspect_fcstd_file",
        lambda _path: {
            "success": True,
            "object_count": 1,
            "solid_count": 1,
            "face_count": 6,
            "edge_count": 12,
            "volume": 1,
            "bbox": {"x_length": 1, "y_length": 1, "z_length": 1},
            "warnings": [],
        },
    )
