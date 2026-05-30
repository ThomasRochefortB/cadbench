import json
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import freecad_runner
import script_normalizer


MAX_TOOL_SCRIPT_CHARS = 24_000
METRICS_MARKER = "__CADBENCH_METRICS__"


@dataclass
class ValidationArtifact:
    fcstd_path: Path | None = None
    stl_path: Path | None = None
    metrics: dict[str, Any] | None = None
    error_info: str | None = None


@dataclass
class FreeCADValidationToolServer:
    artifact_dir: Path
    artifacts: dict[str, ValidationArtifact] = field(default_factory=dict)
    last_handle: str | None = None

    def tool_specs(self) -> list[dict[str, Any]]:
        return freecad_validation_tool_specs()

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "run_freecad_script":
            return self.run_freecad_script(str(arguments.get("script", "")))
        if name == "inspect_fcstd":
            return self.inspect_fcstd(str(arguments.get("handle", "last")))
        if name == "export_stl":
            return self.export_stl(str(arguments.get("handle", "last")))
        if name == "measure_geometry":
            return self.measure_geometry(str(arguments.get("handle", "last")))
        return {"success": False, "error": f"Unknown FreeCAD validation tool: {name}"}

    def run_freecad_script(self, script: str) -> dict[str, Any]:
        if len(script) > MAX_TOOL_SCRIPT_CHARS:
            return {
                "success": False,
                "error": f"Script is too large for validation tool ({len(script)} chars).",
            }

        handle = f"run_{uuid.uuid4().hex[:10]}"
        run_dir = self.artifact_dir / handle
        prepared_script = script_normalizer.prepare_freecad_script(script, "_tool")
        execution = freecad_runner.try_execute_freecad_script(prepared_script, "_tool", run_dir)
        artifact = ValidationArtifact(
            fcstd_path=execution.fcstd_path,
            stl_path=execution.stl_path,
            error_info=execution.error_info,
        )
        if execution.fcstd_path:
            artifact.metrics = inspect_fcstd_file(execution.fcstd_path)
        self.artifacts[handle] = artifact
        self.last_handle = handle

        return {
            "success": execution.fcstd_path is not None,
            "handle": handle,
            "error_info": execution.error_info,
            "metrics": artifact.metrics,
            "stl_exported": execution.stl_path is not None,
        }

    def inspect_fcstd(self, handle: str = "last") -> dict[str, Any]:
        artifact = self._artifact_for_handle(handle)
        if not artifact:
            return {"success": False, "error": f"No validation artifact found for handle: {handle}"}
        if not artifact.fcstd_path:
            return {
                "success": False,
                "error": "The selected validation run did not produce an FCStd file.",
                "error_info": artifact.error_info,
            }
        if artifact.metrics is None:
            artifact.metrics = inspect_fcstd_file(artifact.fcstd_path)
        return {"success": bool(artifact.metrics and artifact.metrics.get("success")), "metrics": artifact.metrics}

    def export_stl(self, handle: str = "last") -> dict[str, Any]:
        artifact = self._artifact_for_handle(handle)
        if not artifact:
            return {"success": False, "error": f"No validation artifact found for handle: {handle}"}
        if artifact.stl_path and artifact.stl_path.exists():
            return {"success": True, "already_exported": True, "path": str(artifact.stl_path)}
        return {
            "success": False,
            "error": "No STL was exported for this run. Re-run the script or inspect FreeCAD export errors.",
            "error_info": artifact.error_info,
        }

    def measure_geometry(self, handle: str = "last") -> dict[str, Any]:
        inspection = self.inspect_fcstd(handle)
        if not inspection.get("success"):
            return inspection
        metrics = inspection["metrics"]
        return {
            "success": True,
            "object_count": metrics.get("object_count", 0),
            "solid_count": metrics.get("solid_count", 0),
            "face_count": metrics.get("face_count", 0),
            "edge_count": metrics.get("edge_count", 0),
            "volume": metrics.get("volume", 0),
            "bbox": metrics.get("bbox"),
            "warnings": metrics.get("warnings", []),
        }

    def _artifact_for_handle(self, handle: str) -> ValidationArtifact | None:
        if handle == "last":
            handle = self.last_handle or ""
        return self.artifacts.get(handle)


def freecad_validation_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "run_freecad_script",
                "description": "Run a complete candidate FreeCAD Python script in the same headless Docker sandbox CADBench uses.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "script": {
                            "type": "string",
                            "description": "Complete FreeCAD Python script to validate.",
                        }
                    },
                    "required": ["script"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "inspect_fcstd",
                "description": "Inspect the FCStd document from a previous validation run and return object/topology metrics.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "handle": {
                            "type": "string",
                            "description": "Validation handle returned by run_freecad_script, or 'last'.",
                            "default": "last",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "export_stl",
                "description": "Report STL export status for a previous validation run.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "handle": {
                            "type": "string",
                            "description": "Validation handle returned by run_freecad_script, or 'last'.",
                            "default": "last",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "measure_geometry",
                "description": "Return compact solid, bounding-box, face, edge, and volume metrics for a validation run.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "handle": {
                            "type": "string",
                            "description": "Validation handle returned by run_freecad_script, or 'last'.",
                            "default": "last",
                        }
                    },
                },
            },
        },
    ]


def inspect_fcstd_file(fcstd_path: Path) -> dict[str, Any]:
    if not fcstd_path.exists() or fcstd_path.stat().st_size == 0:
        return {"success": False, "error": "FCStd file does not exist or is empty."}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        shutil.copy(str(fcstd_path), str(tmpdir_path / "model.FCStd"))
        (tmpdir_path / "inspect_fcstd.py").write_text(_inspection_script())

        try:
            process = subprocess.run(
                freecad_runner.make_docker_command(tmpdir_path, "inspect_fcstd.py"),
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return {"success": False, "error": f"Unable to inspect FCStd file: {exc}"}

    metrics = _parse_metrics(process.stdout)
    if metrics:
        return metrics
    return {
        "success": False,
        "error": "FreeCAD inspection did not return metrics.",
        "stderr": process.stderr.strip(),
    }


def _parse_metrics(stdout: str) -> dict[str, Any] | None:
    for line in stdout.splitlines():
        if line.startswith(METRICS_MARKER):
            try:
                return json.loads(line[len(METRICS_MARKER) :])
            except json.JSONDecodeError:
                return {
                    "success": False,
                    "error": "FreeCAD inspection returned invalid JSON metrics.",
                }
    return None


def _inspection_script() -> str:
    return f"""
import json
import FreeCAD

doc = FreeCAD.open("/data/model.FCStd")
metrics = {{
    "success": True,
    "object_count": len(doc.Objects),
    "shape_object_count": 0,
    "solid_count": 0,
    "face_count": 0,
    "edge_count": 0,
    "volume": 0.0,
    "labels": [],
    "bbox": None,
    "warnings": [],
}}

mins = [None, None, None]
maxs = [None, None, None]

def include_bound_box(bound_box):
    values = [
        bound_box.XMin,
        bound_box.YMin,
        bound_box.ZMin,
        bound_box.XMax,
        bound_box.YMax,
        bound_box.ZMax,
    ]
    if any(value is None for value in values):
        return
    for index, value in enumerate(values[:3]):
        mins[index] = value if mins[index] is None else min(mins[index], value)
    for index, value in enumerate(values[3:]):
        maxs[index] = value if maxs[index] is None else max(maxs[index], value)

for obj in doc.Objects:
    label = getattr(obj, "Label", getattr(obj, "Name", "object"))
    metrics["labels"].append(label)
    shape = getattr(obj, "Shape", None)
    if shape is None:
        continue
    try:
        if shape.isNull():
            metrics["warnings"].append(f"{{label}} has a null shape")
            continue
        metrics["shape_object_count"] += 1
        metrics["solid_count"] += len(shape.Solids)
        metrics["face_count"] += len(shape.Faces)
        metrics["edge_count"] += len(shape.Edges)
        metrics["volume"] += float(shape.Volume)
        include_bound_box(shape.BoundBox)
    except Exception as exc:
        metrics["warnings"].append(f"{{label}} could not be measured: {{exc}}")

if mins[0] is not None:
    metrics["bbox"] = {{
        "x_min": mins[0],
        "y_min": mins[1],
        "z_min": mins[2],
        "x_max": maxs[0],
        "y_max": maxs[1],
        "z_max": maxs[2],
        "x_length": maxs[0] - mins[0],
        "y_length": maxs[1] - mins[1],
        "z_length": maxs[2] - mins[2],
    }}

if metrics["solid_count"] == 0:
    metrics["warnings"].append("No solids were found in the document")
if metrics["volume"] <= 0:
    metrics["warnings"].append("Measured volume is zero or negative")

print("{METRICS_MARKER}" + json.dumps(metrics))
"""
