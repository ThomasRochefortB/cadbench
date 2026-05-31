import base64
import json
import math
import shutil
import struct
import subprocess
import tempfile
import uuid
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import freecad_runner
import script_normalizer


MAX_TOOL_SCRIPT_CHARS = 24_000
METRICS_MARKER = "__CADBENCH_METRICS__"
SHAPE_HEALTH_MARKER = "__CADBENCH_SHAPE_HEALTH__"
DEFAULT_RENDER_IMAGE_SIZE = 512
MAX_RENDER_IMAGE_SIZE = 1024
MIN_RENDER_IMAGE_SIZE = 128


@dataclass
class ValidationArtifact:
    fcstd_path: Path | None = None
    stl_path: Path | None = None
    metrics: dict[str, Any] | None = None
    shape_health: dict[str, Any] | None = None
    mesh_quality: dict[str, Any] | None = None
    render_views: dict[str, Any] | None = None
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
        if name == "render_model_views":
            return self.render_model_views(
                str(arguments.get("handle", "last")),
                int(arguments.get("image_size", DEFAULT_RENDER_IMAGE_SIZE)),
                bool(arguments.get("include_data_urls", True)),
            )
        if name == "shape_health_check":
            return self.shape_health_check(str(arguments.get("handle", "last")))
        if name == "mesh_quality_report":
            return self.mesh_quality_report(str(arguments.get("handle", "last")))
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
        stl_path, error = self._ensure_stl_for_artifact(artifact)
        if stl_path:
            return {"success": True, "already_exported": False, "path": str(stl_path)}
        return {
            "success": False,
            "error": error or "No STL was exported for this run. Re-run the script or inspect FreeCAD export errors.",
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

    def render_model_views(
        self,
        handle: str = "last",
        image_size: int = DEFAULT_RENDER_IMAGE_SIZE,
        include_data_urls: bool = True,
    ) -> dict[str, Any]:
        artifact = self._artifact_for_handle(handle)
        if not artifact:
            return {"success": False, "error": f"No validation artifact found for handle: {handle}"}
        stl_path, error = self._ensure_stl_for_artifact(artifact)
        if not stl_path:
            return {"success": False, "error": error or "No STL is available to render.", "error_info": artifact.error_info}

        try:
            triangles = load_stl_triangles(stl_path)
            views = render_stl_views(
                triangles,
                stl_path.parent / "rendered_views",
                image_size=image_size,
                include_data_urls=include_data_urls,
            )
        except Exception as exc:
            return {"success": False, "error": f"Unable to render STL views: {exc}"}

        artifact.render_views = views
        return {
            "success": True,
            "source": str(stl_path),
            "views": views["views"],
            "bbox": views["bbox"],
            "triangle_count": views["triangle_count"],
            "warnings": views.get("warnings", []),
        }

    def shape_health_check(self, handle: str = "last") -> dict[str, Any]:
        artifact = self._artifact_for_handle(handle)
        if not artifact:
            return {"success": False, "error": f"No validation artifact found for handle: {handle}"}
        if not artifact.fcstd_path:
            return {
                "success": False,
                "error": "The selected validation run did not produce an FCStd file.",
                "error_info": artifact.error_info,
            }
        if artifact.shape_health is None:
            artifact.shape_health = shape_health_check_file(artifact.fcstd_path)
        return artifact.shape_health

    def mesh_quality_report(self, handle: str = "last") -> dict[str, Any]:
        artifact = self._artifact_for_handle(handle)
        if not artifact:
            return {"success": False, "error": f"No validation artifact found for handle: {handle}"}
        stl_path, error = self._ensure_stl_for_artifact(artifact)
        if not stl_path:
            return {"success": False, "error": error or "No STL is available to analyze.", "error_info": artifact.error_info}
        if artifact.mesh_quality is None:
            try:
                artifact.mesh_quality = mesh_quality_report_file(stl_path)
            except Exception as exc:
                artifact.mesh_quality = {"success": False, "error": f"Unable to analyze STL mesh: {exc}"}
        return artifact.mesh_quality

    def _artifact_for_handle(self, handle: str) -> ValidationArtifact | None:
        if handle == "last":
            handle = self.last_handle or ""
        return self.artifacts.get(handle)

    def _ensure_stl_for_artifact(self, artifact: ValidationArtifact) -> tuple[Path | None, str | None]:
        if artifact.stl_path and artifact.stl_path.exists() and artifact.stl_path.stat().st_size > 0:
            return artifact.stl_path, None
        if not artifact.fcstd_path:
            return None, "The selected validation run did not produce an STL or FCStd file."
        stl_path, error = export_stl_from_fcstd_file(artifact.fcstd_path, artifact.fcstd_path.parent)
        if stl_path:
            artifact.stl_path = stl_path
            return stl_path, None
        return None, error


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
        {
            "type": "function",
            "function": {
                "name": "render_model_views",
                "description": (
                    "Render front, top, side, and isometric PNG views from the validation STL. "
                    "This tool returns image data URLs and should only be exposed to vision-capable models."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "handle": {
                            "type": "string",
                            "description": "Validation handle returned by run_freecad_script, or 'last'.",
                            "default": "last",
                        },
                        "image_size": {
                            "type": "integer",
                            "description": "Square PNG size in pixels. Values are clamped between 128 and 1024.",
                            "default": DEFAULT_RENDER_IMAGE_SIZE,
                        },
                        "include_data_urls": {
                            "type": "boolean",
                            "description": "Include base64 data URLs for the rendered PNGs.",
                            "default": True,
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "shape_health_check",
                "description": (
                    "Run deeper FreeCAD/OpenCascade shape validity checks including null shapes, Shape.isValid(), "
                    "Shape.check(), non-solid shells, open wires, and tiny sliver faces."
                ),
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
                "name": "mesh_quality_report",
                "description": (
                    "Analyze the exported STL for watertightness, manifoldness, degenerate triangles, "
                    "component count, triangle count, normals, and bounding-box sanity."
                ),
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


def shape_health_check_file(fcstd_path: Path) -> dict[str, Any]:
    if not fcstd_path.exists() or fcstd_path.stat().st_size == 0:
        return {"success": False, "error": "FCStd file does not exist or is empty."}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        shutil.copy(str(fcstd_path), str(tmpdir_path / "model.FCStd"))
        (tmpdir_path / "shape_health.py").write_text(_shape_health_script())

        try:
            process = subprocess.run(
                freecad_runner.make_docker_command(tmpdir_path, "shape_health.py"),
                check=False,
                capture_output=True,
                text=True,
                timeout=45,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return {"success": False, "error": f"Unable to run FreeCAD shape health check: {exc}"}

    report = _parse_marker_json(process.stdout, SHAPE_HEALTH_MARKER)
    if report:
        return report
    return {
        "success": False,
        "error": "FreeCAD shape health check did not return a report.",
        "stderr": process.stderr.strip(),
    }


def export_stl_from_fcstd_file(fcstd_path: Path, output_dir: Path) -> tuple[Path | None, str | None]:
    if not fcstd_path.exists() or fcstd_path.stat().st_size == 0:
        return None, "FCStd file does not exist or is empty."

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        shutil.copy(str(fcstd_path), str(tmpdir_path / "model.FCStd"))
        (tmpdir_path / "export_stl.py").write_text(_export_stl_script())

        try:
            process = subprocess.run(
                freecad_runner.make_docker_command(tmpdir_path, "export_stl.py"),
                check=False,
                capture_output=True,
                text=True,
                timeout=45,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return None, f"Unable to export STL from FCStd: {exc}"

        exported = tmpdir_path / "model.stl"
        if process.returncode == 0 and exported.exists() and exported.stat().st_size > 0:
            output_dir.mkdir(parents=True, exist_ok=True)
            target = output_dir / "model_exported.stl"
            shutil.move(str(exported), str(target))
            return target, None

    details = "\n".join(line.strip() for line in process.stderr.splitlines() if line.strip())
    return None, details or "FreeCAD could not export an STL from the FCStd file."


def load_stl_triangles(stl_path: Path) -> list[dict[str, Any]]:
    data = stl_path.read_bytes()
    if len(data) < 15:
        raise ValueError("STL file is empty or too small.")

    triangles = _load_binary_stl(data)
    if not triangles:
        triangles = _load_ascii_stl(data.decode("utf-8", errors="ignore"))
    if not triangles:
        raise ValueError("STL file did not contain triangles.")
    return triangles


def mesh_quality_report_file(stl_path: Path) -> dict[str, Any]:
    triangles = load_stl_triangles(stl_path)
    bbox = _triangles_bbox(triangles)
    diagonal = _bbox_diagonal(bbox)
    tolerance = max(diagonal * 1e-7, 1e-7)
    vertex_ids: dict[tuple[int, int, int], int] = {}
    vertices: list[tuple[float, float, float]] = []
    edge_counts: dict[tuple[int, int], int] = {}
    edge_first_triangle: dict[tuple[int, int], int] = {}
    parent = list(range(len(triangles)))
    degenerate_triangles = 0
    missing_normals = 0
    inverted_normals = 0
    signed_volume = 0.0

    def vertex_id(vertex: tuple[float, float, float]) -> int:
        key = tuple(int(round(coord / tolerance)) for coord in vertex)
        if key not in vertex_ids:
            vertex_ids[key] = len(vertices)
            vertices.append(vertex)
        return vertex_ids[key]

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for triangle_index, triangle in enumerate(triangles):
        verts = [tuple(vertex) for vertex in triangle["vertices"]]
        computed_normal = _triangle_normal(verts)
        area = _triangle_area(verts)
        if area <= max(diagonal * diagonal * 1e-12, 1e-12):
            degenerate_triangles += 1

        provided_normal = tuple(triangle.get("normal") or (0.0, 0.0, 0.0))
        if _length(provided_normal) <= 1e-12:
            missing_normals += 1
        elif _dot(_normalize(provided_normal), computed_normal) < -0.5:
            inverted_normals += 1

        signed_volume += _dot(verts[0], _cross(verts[1], verts[2])) / 6.0
        ids = [vertex_id(vertex) for vertex in verts]
        for start, end in ((ids[0], ids[1]), (ids[1], ids[2]), (ids[2], ids[0])):
            edge = tuple(sorted((start, end)))
            edge_counts[edge] = edge_counts.get(edge, 0) + 1
            if edge in edge_first_triangle:
                union(triangle_index, edge_first_triangle[edge])
            else:
                edge_first_triangle[edge] = triangle_index

    component_counts: dict[int, int] = {}
    for triangle_index in range(len(triangles)):
        root = find(triangle_index)
        component_counts[root] = component_counts.get(root, 0) + 1

    boundary_edges = sum(1 for count in edge_counts.values() if count == 1)
    non_manifold_edges = sum(1 for count in edge_counts.values() if count > 2)
    zero_length_axes = [axis for axis, length in _bbox_lengths(bbox).items() if length <= tolerance]
    watertight = boundary_edges == 0 and non_manifold_edges == 0
    warnings = []
    if degenerate_triangles:
        warnings.append(f"{degenerate_triangles} degenerate triangle(s) detected")
    if boundary_edges:
        warnings.append(f"{boundary_edges} boundary edge(s) indicate the mesh is not watertight")
    if non_manifold_edges:
        warnings.append(f"{non_manifold_edges} non-manifold edge(s) detected")
    if len(component_counts) > 1:
        warnings.append(f"{len(component_counts)} disconnected mesh component(s) detected")
    if zero_length_axes:
        warnings.append(f"Bounding box has near-zero extent on axis/axes: {', '.join(zero_length_axes)}")
    if watertight and signed_volume < 0:
        warnings.append("Triangle winding appears inverted for a closed mesh")

    return {
        "success": True,
        "source": str(stl_path),
        "triangle_count": len(triangles),
        "vertex_count": len(vertices),
        "edge_count": len(edge_counts),
        "watertight": watertight,
        "boundary_edge_count": boundary_edges,
        "non_manifold_edge_count": non_manifold_edges,
        "degenerate_triangle_count": degenerate_triangles,
        "component_count": len(component_counts),
        "component_triangle_counts": sorted(component_counts.values(), reverse=True),
        "missing_normal_count": missing_normals,
        "inverted_normal_count": inverted_normals,
        "signed_volume": signed_volume,
        "bbox": bbox,
        "warnings": warnings,
    }


def render_stl_views(
    triangles: list[dict[str, Any]],
    output_dir: Path,
    image_size: int = DEFAULT_RENDER_IMAGE_SIZE,
    include_data_urls: bool = True,
) -> dict[str, Any]:
    image_size = max(MIN_RENDER_IMAGE_SIZE, min(MAX_RENDER_IMAGE_SIZE, image_size))
    bbox = _triangles_bbox(triangles)
    views = {}
    warnings = []
    output_dir.mkdir(parents=True, exist_ok=True)

    for name, camera in _render_cameras().items():
        path = output_dir / f"{name}.png"
        stats = _render_triangles_to_png(triangles, camera, path, image_size)
        view = {
            "path": str(path),
            "width": image_size,
            "height": image_size,
            "colored_pixel_count": stats["colored_pixel_count"],
        }
        if stats["colored_pixel_count"] == 0:
            warnings.append(f"{name} render appears blank")
        if include_data_urls:
            view["data_url"] = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
        views[name] = view

    return {
        "views": views,
        "bbox": bbox,
        "triangle_count": len(triangles),
        "warnings": warnings,
    }


def _load_binary_stl(data: bytes) -> list[dict[str, Any]]:
    if len(data) < 84:
        return []
    triangle_count = struct.unpack("<I", data[80:84])[0]
    expected_size = 84 + triangle_count * 50
    if expected_size != len(data):
        return []

    triangles = []
    offset = 84
    for _index in range(triangle_count):
        values = struct.unpack("<12fH", data[offset : offset + 50])
        normal = (values[0], values[1], values[2])
        vertices = [
            (values[3], values[4], values[5]),
            (values[6], values[7], values[8]),
            (values[9], values[10], values[11]),
        ]
        triangles.append({"normal": normal, "vertices": vertices})
        offset += 50
    return triangles


def _load_ascii_stl(text: str) -> list[dict[str, Any]]:
    triangles = []
    current_normal = (0.0, 0.0, 0.0)
    current_vertices = []
    for raw_line in text.splitlines():
        parts = raw_line.strip().split()
        if not parts:
            continue
        if parts[:2] == ["facet", "normal"] and len(parts) >= 5:
            current_normal = _float_triplet(parts[2:5])
            current_vertices = []
        elif parts[0] == "vertex" and len(parts) >= 4:
            current_vertices.append(_float_triplet(parts[1:4]))
            if len(current_vertices) == 3:
                triangles.append({"normal": current_normal, "vertices": current_vertices})
                current_vertices = []
    return triangles


def _float_triplet(values: list[str]) -> tuple[float, float, float]:
    return (float(values[0]), float(values[1]), float(values[2]))


def _triangles_bbox(triangles: list[dict[str, Any]]) -> dict[str, float]:
    xs = []
    ys = []
    zs = []
    for triangle in triangles:
        for x, y, z in triangle["vertices"]:
            xs.append(x)
            ys.append(y)
            zs.append(z)
    if not xs:
        return {
            "x_min": 0.0,
            "y_min": 0.0,
            "z_min": 0.0,
            "x_max": 0.0,
            "y_max": 0.0,
            "z_max": 0.0,
            "x_length": 0.0,
            "y_length": 0.0,
            "z_length": 0.0,
        }
    return {
        "x_min": min(xs),
        "y_min": min(ys),
        "z_min": min(zs),
        "x_max": max(xs),
        "y_max": max(ys),
        "z_max": max(zs),
        "x_length": max(xs) - min(xs),
        "y_length": max(ys) - min(ys),
        "z_length": max(zs) - min(zs),
    }


def _bbox_lengths(bbox: dict[str, float]) -> dict[str, float]:
    return {
        "x": bbox["x_length"],
        "y": bbox["y_length"],
        "z": bbox["z_length"],
    }


def _bbox_diagonal(bbox: dict[str, float]) -> float:
    lengths = _bbox_lengths(bbox)
    return math.sqrt(lengths["x"] ** 2 + lengths["y"] ** 2 + lengths["z"] ** 2)


def _render_cameras() -> dict[str, dict[str, tuple[float, float, float]]]:
    iso_direction = _normalize((1.0, -1.0, 0.8))
    iso_right = _normalize(_cross((0.0, 0.0, 1.0), iso_direction))
    iso_up = _normalize(_cross(iso_direction, iso_right))
    return {
        "front": {
            "right": (1.0, 0.0, 0.0),
            "up": (0.0, 0.0, 1.0),
            "view": (0.0, -1.0, 0.0),
        },
        "top": {
            "right": (1.0, 0.0, 0.0),
            "up": (0.0, 1.0, 0.0),
            "view": (0.0, 0.0, 1.0),
        },
        "side": {
            "right": (0.0, 1.0, 0.0),
            "up": (0.0, 0.0, 1.0),
            "view": (1.0, 0.0, 0.0),
        },
        "isometric": {
            "right": iso_right,
            "up": iso_up,
            "view": iso_direction,
        },
    }


def _render_triangles_to_png(
    triangles: list[dict[str, Any]],
    camera: dict[str, tuple[float, float, float]],
    path: Path,
    image_size: int,
) -> dict[str, int]:
    projected_vertices = []
    for triangle in triangles:
        for vertex in triangle["vertices"]:
            projected_vertices.append(_project_vertex(vertex, camera))

    min_u = min(vertex[0] for vertex in projected_vertices)
    max_u = max(vertex[0] for vertex in projected_vertices)
    min_v = min(vertex[1] for vertex in projected_vertices)
    max_v = max(vertex[1] for vertex in projected_vertices)
    span_u = max(max_u - min_u, 1e-9)
    span_v = max(max_v - min_v, 1e-9)
    margin = max(10, int(image_size * 0.08))
    scale = min((image_size - 2 * margin) / span_u, (image_size - 2 * margin) / span_v)
    pixels = bytearray([255] * image_size * image_size * 3)
    z_buffer = [-math.inf] * (image_size * image_size)
    colored_pixels = 0

    for triangle in triangles:
        verts = [tuple(vertex) for vertex in triangle["vertices"]]
        points = []
        for vertex in verts:
            u, v, depth = _project_vertex(vertex, camera)
            x = margin + (u - min_u) * scale
            y = image_size - margin - (v - min_v) * scale
            points.append((x, y, depth))
        color = _triangle_color(verts, camera)
        colored_pixels += _rasterize_triangle(points, color, pixels, z_buffer, image_size)

    _write_png_rgb(path, image_size, image_size, pixels)
    return {"colored_pixel_count": colored_pixels}


def _project_vertex(
    vertex: tuple[float, float, float],
    camera: dict[str, tuple[float, float, float]],
) -> tuple[float, float, float]:
    return (_dot(vertex, camera["right"]), _dot(vertex, camera["up"]), _dot(vertex, camera["view"]))


def _triangle_color(
    vertices: list[tuple[float, float, float]],
    camera: dict[str, tuple[float, float, float]],
) -> tuple[int, int, int]:
    normal = _triangle_normal(vertices)
    light = _normalize((0.35, -0.45, 0.82))
    brightness = 0.62 + 0.30 * max(0.0, _dot(normal, light)) + 0.12 * abs(_dot(normal, camera["view"]))
    base = (114, 143, 166)
    return tuple(max(35, min(235, int(channel * brightness))) for channel in base)


def _rasterize_triangle(
    points: list[tuple[float, float, float]],
    color: tuple[int, int, int],
    pixels: bytearray,
    z_buffer: list[float],
    image_size: int,
) -> int:
    (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = points
    min_x = max(0, int(math.floor(min(x0, x1, x2))))
    max_x = min(image_size - 1, int(math.ceil(max(x0, x1, x2))))
    min_y = max(0, int(math.floor(min(y0, y1, y2))))
    max_y = min(image_size - 1, int(math.ceil(max(y0, y1, y2))))
    denominator = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(denominator) <= 1e-12:
        return 0

    painted = 0
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            px = x + 0.5
            py = y + 0.5
            a = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / denominator
            b = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / denominator
            c = 1.0 - a - b
            if a < -1e-6 or b < -1e-6 or c < -1e-6:
                continue
            depth = a * z0 + b * z1 + c * z2
            pixel_index = y * image_size + x
            if depth <= z_buffer[pixel_index]:
                continue
            z_buffer[pixel_index] = depth
            byte_index = pixel_index * 3
            if pixels[byte_index : byte_index + 3] == b"\xff\xff\xff":
                painted += 1
            pixels[byte_index : byte_index + 3] = bytes(color)
    return painted


def _write_png_rgb(path: Path, width: int, height: int, pixels: bytearray) -> None:
    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", checksum)

    rows = []
    row_width = width * 3
    for y in range(height):
        start = y * row_width
        rows.append(b"\x00" + bytes(pixels[start : start + row_width]))
    payload = b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(b"".join(rows), level=6)),
            chunk(b"IEND", b""),
        ]
    )
    path.write_bytes(payload)


def _triangle_normal(vertices: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    first, second, third = vertices
    return _normalize(_cross(_subtract(second, first), _subtract(third, first)))


def _triangle_area(vertices: list[tuple[float, float, float]]) -> float:
    first, second, third = vertices
    return 0.5 * _length(_cross(_subtract(second, first), _subtract(third, first)))


def _subtract(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _cross(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _length(vector: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(vector, vector))


def _normalize(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = _length(vector)
    if length <= 1e-12:
        return (0.0, 0.0, 0.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _parse_metrics(stdout: str) -> dict[str, Any] | None:
    return _parse_marker_json(stdout, METRICS_MARKER)


def _parse_marker_json(stdout: str, marker: str) -> dict[str, Any] | None:
    for line in stdout.splitlines():
        if line.startswith(marker):
            try:
                return json.loads(line[len(marker) :])
            except json.JSONDecodeError:
                return {
                    "success": False,
                    "error": "FreeCAD inspection returned invalid JSON.",
                }
    return None


def _export_stl_script() -> str:
    return """
import FreeCAD
import Mesh

doc = FreeCAD.open("/data/model.FCStd")
doc.recompute()
objects = []
for obj in doc.Objects:
    try:
        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull():
            objects.append(obj)
    except Exception:
        pass

if not objects:
    raise RuntimeError("No shape objects were available for STL export")

Mesh.export(objects, "/data/model.stl")
"""


def _shape_health_script() -> str:
    return f"""
import json
import FreeCAD

doc = FreeCAD.open("/data/model.FCStd")
doc.recompute()

report = {{
    "success": True,
    "valid": True,
    "object_count": len(doc.Objects),
    "shape_object_count": 0,
    "invalid_object_count": 0,
    "warning_count": 0,
    "error_count": 0,
    "checks": [],
    "warnings": [],
}}

def compact(value):
    text = str(value)
    return text if len(text) <= 500 else text[:500] + "...[truncated]"

def add_warning(target, message):
    target["warnings"].append(message)
    report["warnings"].append(message)
    report["warning_count"] += 1

def add_error(target, message):
    target["errors"].append(message)
    report["error_count"] += 1
    report["valid"] = False

if not doc.Objects:
    report["valid"] = False
    report["warnings"].append("Document has no objects")
    report["warning_count"] += 1

for obj in doc.Objects:
    label = getattr(obj, "Label", getattr(obj, "Name", "object"))
    item = {{
        "label": label,
        "has_shape": False,
        "is_null": None,
        "is_valid": None,
        "solid_count": 0,
        "shell_count": 0,
        "wire_count": 0,
        "face_count": 0,
        "edge_count": 0,
        "open_wire_count": 0,
        "tiny_face_count": 0,
        "tiny_edge_count": 0,
        "shape_check": None,
        "bop_check": None,
        "warnings": [],
        "errors": [],
    }}
    report["checks"].append(item)
    shape = getattr(obj, "Shape", None)
    if shape is None:
        add_warning(item, f"{{label}} has no Shape attribute")
        continue

    item["has_shape"] = True
    report["shape_object_count"] += 1
    try:
        item["is_null"] = bool(shape.isNull())
    except Exception as exc:
        add_error(item, f"{{label}} null-shape check failed: {{compact(exc)}}")
        continue

    if item["is_null"]:
        report["invalid_object_count"] += 1
        add_error(item, f"{{label}} has a null shape")
        continue

    try:
        item["is_valid"] = bool(shape.isValid())
        if not item["is_valid"]:
            report["invalid_object_count"] += 1
            add_error(item, f"{{label}} failed Shape.isValid()")
    except Exception as exc:
        add_warning(item, f"{{label}} Shape.isValid() raised: {{compact(exc)}}")

    for check_name, args in (("shape_check", ()), ("bop_check", (True,))):
        try:
            result = shape.check(*args)
            item[check_name] = compact(result) if result else ""
            if result:
                add_error(item, f"{{label}} {{check_name}} reported: {{compact(result)}}")
        except TypeError:
            item[check_name] = "unsupported"
        except Exception as exc:
            add_error(item, f"{{label}} {{check_name}} raised: {{compact(exc)}}")

    try:
        solids = list(shape.Solids)
        shells = list(shape.Shells)
        wires = list(shape.Wires)
        faces = list(shape.Faces)
        edges = list(shape.Edges)
        item["solid_count"] = len(solids)
        item["shell_count"] = len(shells)
        item["wire_count"] = len(wires)
        item["face_count"] = len(faces)
        item["edge_count"] = len(edges)
        if not solids and (shells or faces or wires):
            add_warning(item, f"{{label}} has faces/shells/wires but no solids")

        for wire in wires:
            try:
                if not wire.isClosed():
                    item["open_wire_count"] += 1
            except Exception:
                pass
        if item["open_wire_count"]:
            add_warning(item, f"{{label}} has {{item['open_wire_count']}} open wire(s)")

        total_area = 0.0
        for face in faces:
            try:
                total_area += float(face.Area)
            except Exception:
                pass
        face_threshold = max(total_area * 1e-8, 1e-8)
        for face in faces:
            try:
                if float(face.Area) <= face_threshold:
                    item["tiny_face_count"] += 1
            except Exception:
                pass
        if item["tiny_face_count"]:
            add_warning(item, f"{{label}} has {{item['tiny_face_count']}} tiny/sliver face(s)")

        try:
            diagonal = max(float(shape.BoundBox.DiagonalLength), 1.0)
        except Exception:
            diagonal = 1.0
        edge_threshold = max(diagonal * 1e-8, 1e-8)
        for edge in edges:
            try:
                if float(edge.Length) <= edge_threshold:
                    item["tiny_edge_count"] += 1
            except Exception:
                pass
        if item["tiny_edge_count"]:
            add_warning(item, f"{{label}} has {{item['tiny_edge_count']}} tiny edge(s)")
    except Exception as exc:
        add_error(item, f"{{label}} topology inspection failed: {{compact(exc)}}")

if report["shape_object_count"] == 0:
    report["valid"] = False
    report["warnings"].append("No shape-bearing objects were found")
    report["warning_count"] += 1

print("{SHAPE_HEALTH_MARKER}" + json.dumps(report))
"""


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
