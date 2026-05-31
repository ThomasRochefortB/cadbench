import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from config import FREECAD_DOCKER_IMAGE


@dataclass(frozen=True)
class ContextEntry:
    id: str
    title: str
    category: str
    text: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class ExampleEntry:
    id: str
    title: str
    topic: str
    code: str
    notes: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class FreeCADDocEntry:
    id: str
    title: str
    category: str
    source_url: str
    source_title: str
    symbols: tuple[str, ...]
    keywords: tuple[str, ...]
    summary: str
    usage: str
    example: str
    headless_safe: bool = True
    version_notes: tuple[str, ...] = ()


CONTEXT_ENTRIES = [
    ContextEntry(
        id="headless-basics",
        title="Headless document setup",
        category="docs",
        text=(
            "Use `import FreeCAD as App` and `import Part`. Create one document with "
            '`doc = App.newDocument("CADModel")`. Do not import FreeCADGui or call GUI-only '
            "document activation methods. Add final visible solids with `Part.show(shape)`, "
            "then call `doc.recompute()` and `doc.saveAs('/data/output.FCStd')`."
        ),
        keywords=("headless", "document", "save", "freecadgui", "recompute", "fcstd"),
    ),
    ContextEntry(
        id="part-primitives",
        title="Part primitive constructors",
        category="docs",
        text=(
            "Reliable primitive constructors include `Part.makeBox(length, width, height)`, "
            "`Part.makeCylinder(radius, height)`, `Part.makeSphere(radius)`, "
            "`Part.makeCone(radius1, radius2, height)`, and `Part.makeTorus(radius1, radius2)`. "
            "Move shapes by assigning `shape.Placement = App.Placement(App.Vector(x, y, z), App.Rotation(...))`."
        ),
        keywords=("box", "cube", "cylinder", "sphere", "cone", "torus", "primitive"),
    ),
    ContextEntry(
        id="booleans",
        title="Boolean operations",
        category="docs",
        text=(
            "Use `base.cut(tool)` for subtraction, `a.fuse(b)` for union, and `a.common(b)` for "
            "intersection. Call `removeSplitter()` after booleans when it is useful, but guard it "
            "with try/except because some generated topology can fail."
        ),
        keywords=("boolean", "cut", "hole", "subtract", "fuse", "union", "intersection", "common"),
    ),
    ContextEntry(
        id="placements",
        title="Placements and rotations",
        category="docs",
        text=(
            "`App.Vector(x, y, z)` stores translation. `App.Rotation(App.Vector(ax, ay, az), degrees)` "
            "creates an axis-angle rotation. `App.Placement(vector, rotation)` combines them. "
            "A cylinder's height is along the local Z axis before rotation."
        ),
        keywords=("placement", "rotation", "vector", "translate", "axis", "angle", "position"),
    ),
    ContextEntry(
        id="fragile-operations",
        title="Fragile operations",
        category="docs",
        text=(
            "Fillets, chamfers, sweeps, lofts, shelling, and thickness operations are useful but fragile. "
            "Keep them optional: wrap them in try/except and fall back to the unmodified solid when they fail. "
            "Avoid selecting edges by hard-coded indexes unless the shape is very simple."
        ),
        keywords=("fillet", "chamfer", "sweep", "loft", "shell", "thickness", "edge", "fragile"),
    ),
    ContextEntry(
        id="coordinate-system",
        title="Coordinate system conventions",
        category="docs",
        text=(
            "Keep geometry near the origin and use millimeters unless the user asks otherwise. "
            "Use named variables for dimensions. Centered holes are often easiest by placing the cutter "
            "through the middle of the base solid and subtracting it."
        ),
        keywords=("center", "origin", "dimension", "millimeter", "hole", "cutter"),
    ),
]


FREECAD_DOC_CORPUS_VERSION = "freecad-docs-wiki-local-2026-05"
FREECAD_WIKI_API_URL = "https://wiki.freecad.org/api.php"
FREECAD_WIKI_PAGE_URL_PREFIX = "https://wiki.freecad.org/"
FREECAD_WIKI_TIMEOUT_SECONDS = 4.0
DEFAULT_CONTEXT_DOC_QUERY = "headless document save Part primitives booleans placements fillets"
DEFAULT_API_DOC_QUERY = (
    "App.newDocument Part.show doc.saveAs Part.makeBox Part.makeCylinder "
    "Part.makeSphere Shape.cut Shape.fuse App.Placement"
)
DEFAULT_EXAMPLE_TOPIC = "booleans placements fillets"


FREECAD_DOC_ENTRIES = [
    FreeCADDocEntry(
        id="scripting-basics-create-objects",
        title="FreeCAD scripting basics: create document objects",
        category="python-api",
        source_url="https://wiki.freecad.org/FreeCAD_Scripting_Basics",
        source_title="FreeCAD Scripting Basics",
        symbols=("App.newDocument", "FreeCAD.ActiveDocument.addObject", "Part::Feature", "Part.show"),
        keywords=("document", "object", "part", "feature", "show", "shape", "script"),
        summary=(
            "FreeCAD scripts create a document, add document objects such as Part::Feature, assign a Shape, "
            "and recompute. Part.show(shape) is a shortcut for adding Part geometry to the active document."
        ),
        usage=(
            "doc = App.newDocument('CADModel'); obj = doc.addObject('Part::Feature', 'Body'); "
            "obj.Shape = shape; doc.recompute()"
        ),
        example=(
            "import FreeCAD as App\n"
            "import Part\n"
            "doc = App.newDocument('CADModel')\n"
            "box = Part.makeBox(2, 2, 2)\n"
            "Part.show(box)\n"
            "doc.recompute()"
        ),
        version_notes=("Uses conservative APIs expected to work in FreeCAD 0.20.x headless execution.",),
    ),
    FreeCADDocEntry(
        id="part-scripting-toposhapes",
        title="Part scripting: TopoShapes and geometric primitives",
        category="part-api",
        source_url="https://wiki.freecad.org/Part_scripting",
        source_title="Part scripting",
        symbols=("Part.LineSegment", "Part.Circle", "toShape", "TopoShape", "Part::Feature"),
        keywords=("part", "toposhape", "line", "circle", "wire", "face", "solid", "primitive"),
        summary=(
            "Part scripting uses geometric primitives to build TopoShapes. Shapes, not raw geometric curves, "
            "are assigned to Part::Feature objects or combined into solids."
        ),
        usage="shape = geometric_primitive.toShape(); obj = doc.addObject('Part::Feature', name); obj.Shape = shape",
        example=(
            "line = Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0))\n"
            "obj = doc.addObject('Part::Feature', 'Line')\n"
            "obj.Shape = line.toShape()"
        ),
        version_notes=("Prefer solid Part primitives for CADBench unless the prompt specifically asks for curves.",),
    ),
    FreeCADDocEntry(
        id="part-primitives",
        title="Part primitives: boxes, cylinders, spheres, cones, and tori",
        category="part-api",
        source_url="https://wiki.freecad.org/Part_Primitives",
        source_title="Part Primitives",
        symbols=("Part.makeBox", "Part.makeCylinder", "Part.makeSphere", "Part.makeCone", "Part.makeTorus"),
        keywords=("box", "cube", "cylinder", "sphere", "cone", "torus", "primitive", "solid"),
        summary=(
            "The Part workbench exposes primitive solid constructors that are suitable for headless script "
            "generation and boolean modeling."
        ),
        usage=(
            "Part.makeBox(length, width, height); Part.makeCylinder(radius, height); "
            "Part.makeSphere(radius); Part.makeCone(radius1, radius2, height); Part.makeTorus(radius1, radius2)"
        ),
        example=(
            "base = Part.makeBox(length, width, height)\n"
            "hole = Part.makeCylinder(hole_radius, height + 4)\n"
            "sphere = Part.makeSphere(radius)"
        ),
        version_notes=("Primitive constructors are stable across FreeCAD 0.20.x and later.",),
    ),
    FreeCADDocEntry(
        id="part-booleans",
        title="Part booleans: cut, fuse, and common",
        category="part-api",
        source_url="https://wiki.freecad.org/Part_Boolean",
        source_title="Part Boolean",
        symbols=("Shape.cut", "Shape.fuse", "Shape.common", "Part Cut", "Part Fuse", "Part Common"),
        keywords=("boolean", "cut", "subtract", "difference", "fuse", "union", "common", "intersection", "hole"),
        summary=(
            "Part boolean operations combine TopoShapes by subtraction, union, or intersection. They are the "
            "most reliable way to model holes and joined solids in CADBench scripts."
        ),
        usage="result = base.cut(tool); result = first.fuse(second); result = first.common(second)",
        example=(
            "cutter = Part.makeCylinder(radius, height + 4)\n"
            "cutter.Placement = App.Placement(App.Vector(x, y, -2), App.Rotation())\n"
            "body = base.cut(cutter)"
        ),
        version_notes=("Boolean topology can fail on invalid or barely touching shapes; oversize cutters slightly.",),
    ),
    FreeCADDocEntry(
        id="placement",
        title="Placement, vectors, and rotations",
        category="app-api",
        source_url="https://wiki.freecad.org/Placement",
        source_title="Placement",
        symbols=("App.Placement", "App.Vector", "App.Rotation", "FreeCAD.Placement"),
        keywords=("placement", "vector", "rotation", "translate", "axis", "angle", "position", "orientation"),
        summary=(
            "A Placement combines translation and rotation. Primitive cylinders are created along local Z before "
            "placement, so horizontal holes need a 90 degree rotation."
        ),
        usage="shape.Placement = App.Placement(App.Vector(x, y, z), App.Rotation(App.Vector(ax, ay, az), degrees))",
        example=(
            "cutter.Placement = App.Placement(\n"
            "    App.Vector(-2, width / 2, height / 2),\n"
            "    App.Rotation(App.Vector(0, 1, 0), 90),\n"
            ")"
        ),
        version_notes=("Axis-angle App.Rotation usage is conservative for FreeCAD 0.20.x.",),
    ),
    FreeCADDocEntry(
        id="part-fillets-chamfers",
        title="Part fillets and chamfers",
        category="part-api",
        source_url="https://wiki.freecad.org/Part_Fillet",
        source_title="Part Fillet",
        symbols=("Shape.makeFillet", "Shape.makeChamfer", "Part Fillet", "Part Chamfer"),
        keywords=("fillet", "chamfer", "round", "bevel", "edge", "decorative"),
        summary=(
            "Fillets and chamfers are useful finishing operations but are sensitive to topology and edge choice. "
            "Generated scripts should treat them as optional."
        ),
        usage="body = body.makeFillet(radius, edges) or body = body.makeChamfer(distance, edges)",
        example=(
            "try:\n"
            "    body = body.makeFillet(1.5, body.Edges)\n"
            "except Exception:\n"
            "    pass"
        ),
        version_notes=("Guard fillets/chamfers in FreeCAD 0.20.x because OpenCASCADE failures are common.",),
    ),
    FreeCADDocEntry(
        id="mesh-export",
        title="Mesh export for Part objects",
        category="mesh-api",
        source_url="https://wiki.freecad.org/Mesh_Scripting",
        source_title="Mesh Scripting",
        symbols=("Mesh.export", "Mesh::Feature"),
        keywords=("mesh", "stl", "export", "preview", "objects"),
        summary=(
            "Mesh export can write document objects to STL for previews. CADBench appends its own guarded STL "
            "export after generation, so model scripts usually only need to save FCStd."
        ),
        usage="Mesh.export(objects, '/data/output.stl')",
        example=(
            "import Mesh\n"
            "doc.recompute()\n"
            "Mesh.export([obj for obj in doc.Objects if hasattr(obj, 'Shape')], '/data/output.stl')"
        ),
        version_notes=("CADBench handles STL export automatically after the generated script runs.",),
    ),
    FreeCADDocEntry(
        id="document-save",
        title="Document save in headless scripts",
        category="app-api",
        source_url="https://wiki.freecad.org/FreeCAD_Scripting_Basics",
        source_title="FreeCAD Scripting Basics",
        symbols=("doc.saveAs", "App.newDocument", "doc.recompute"),
        keywords=("save", "fcstd", "document", "recompute", "output", "headless"),
        summary=(
            "Generated CADBench scripts must recompute and save the active document. The expected artifact is "
            "the FreeCAD native FCStd file."
        ),
        usage='doc.recompute(); doc.saveAs("/data/output.FCStd")',
        example=(
            "doc = App.newDocument('CADModel')\n"
            "Part.show(body)\n"
            "doc.recompute()\n"
            "doc.saveAs('/data/output.FCStd')"
        ),
        version_notes=("CADBench rewrites output paths per model but final scripts should target /data/output.FCStd.",),
    ),
]


EXAMPLES = [
    ExampleEntry(
        id="box-with-centered-hole",
        title="Box with centered through hole",
        topic="booleans",
        code="""import FreeCAD as App
import Part

doc = App.newDocument("CADModel")
length = 40
width = 30
height = 12
hole_radius = 5

base = Part.makeBox(length, width, height)
hole = Part.makeCylinder(hole_radius, height + 4)
hole.Placement = App.Placement(
    App.Vector(length / 2, width / 2, -2),
    App.Rotation(App.Vector(0, 0, 1), 0),
)
body = base.cut(hole)
Part.show(body)
doc.recompute()
doc.saveAs("/data/output.FCStd")""",
        notes="Make cutters longer than the part so the cut passes fully through.",
        keywords=("box", "hole", "cylinder", "cut", "centered", "through"),
    ),
    ExampleEntry(
        id="rounded-optional",
        title="Optional fillet with fallback",
        topic="fillets",
        code="""body = base.cut(hole)
try:
    body = body.makeFillet(1.5, body.Edges)
except Exception:
    pass
Part.show(body)""",
        notes="Generated fillets should never be required for the script to succeed.",
        keywords=("fillet", "rounded", "edge", "optional", "fallback"),
    ),
    ExampleEntry(
        id="rotated-cylinder-cut",
        title="Horizontal cylinder cut",
        topic="placements",
        code="""cutter = Part.makeCylinder(radius, length + 4)
cutter.Placement = App.Placement(
    App.Vector(-2, width / 2, height / 2),
    App.Rotation(App.Vector(0, 1, 0), 90),
)
body = base.cut(cutter)""",
        notes="Rotate cylinders when the hole axis should be horizontal.",
        keywords=("horizontal", "cylinder", "cut", "rotation", "placement", "axis"),
    ),
]


ERROR_FIXES = [
    {
        "patterns": ("FreeCADGui", "No module named 'FreeCADGui'"),
        "fix": "Remove FreeCADGui imports and GUI-only calls. Use FreeCAD/App and Part APIs only.",
    },
    {
        "patterns": ("has no attribute", "AttributeError"),
        "fix": "Replace invented API calls with documented Part functions or shape methods such as cut, fuse, common, and makeFillet.",
    },
    {
        "patterns": ("expected output.FCStd file was not created", "save"),
        "fix": 'Create a document, add visible objects with Part.show, recompute, and call `doc.saveAs("/data/output.FCStd")`.',
    },
    {
        "patterns": ("BRep_API", "makeFillet", "TopoDS"),
        "fix": "Treat fillets/chamfers as optional and fall back to the unfilleted body on any exception.",
    },
    {
        "patterns": ("No active document", "ActiveDocument"),
        "fix": 'Create and keep an explicit document variable with `doc = App.newDocument("CADModel")`.',
    },
]


def freecad_context_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "search_docs",
                "description": (
                    "Search concise, version-pinned FreeCAD scripting guidance. Query with short FreeCAD technique "
                    "keywords such as booleans, placements, document save, fillets, or primitives; do not pass the "
                    "user's raw object description."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "FreeCAD technique keywords, not the raw user modeling prompt.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of matching snippets.",
                            "default": 3,
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_freecad_api_docs",
                "description": (
                    "Search FreeCAD wiki documentation and Python API patterns, using the local version-aware "
                    "index by default and optionally querying the live FreeCAD wiki API. Results include source "
                    "URLs and headless CADBench compatibility notes. Query with API symbols or implementation "
                    "concepts, not the raw object being modeled."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "FreeCAD API topic, symbol group, or implementation phrase such as "
                                "Part.makeCylinder App.Placement Shape.fuse."
                            ),
                        },
                        "symbol": {
                            "type": "string",
                            "description": "Optional exact or partial API symbol, for example Part.makeBox or Shape.cut.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of documentation matches.",
                            "default": 4,
                        },
                        "headless_only": {
                            "type": "boolean",
                            "description": "Only return entries that are safe for headless FreeCAD scripts.",
                            "default": True,
                        },
                        "live_wiki": {
                            "type": "boolean",
                            "description": "Also query the live FreeCAD wiki MediaWiki API with a short timeout.",
                            "default": False,
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_examples",
                "description": (
                    "Retrieve short FreeCAD Python examples for a modeling technique. Use topics like booleans, "
                    "placements, fillets, or document save; do not pass the user's raw object description."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Example technique topic such as booleans, placements, or fillets.",
                        },
                        "limit": {"type": "integer", "description": "Maximum examples to return.", "default": 2},
                    },
                    "required": ["topic"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "known_error_fix",
                "description": "Map a FreeCAD execution error to known repair advice.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "error_message": {"type": "string", "description": "FreeCAD traceback or stderr text."}
                    },
                    "required": ["error_message"],
                },
            },
        },
    ]


def execute_freecad_context_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "search_docs":
        return search_docs(str(arguments.get("query", "")), int(arguments.get("limit", 3)))
    if name == "search_freecad_api_docs":
        return search_freecad_api_docs(
            str(arguments.get("query", "")),
            str(arguments.get("symbol", "")),
            int(arguments.get("limit", 4)),
            bool(arguments.get("headless_only", True)),
            bool(arguments.get("live_wiki", False)),
        )
    if name == "get_examples":
        return get_examples(str(arguments.get("topic", "")), int(arguments.get("limit", 2)))
    if name == "known_error_fix":
        return known_error_fix(str(arguments.get("error_message", "")))
    return {"success": False, "error": f"Unknown FreeCAD context tool: {name}"}


def search_docs(query: str, limit: int = 3) -> dict[str, Any]:
    limit = _bounded_limit(limit, default=3, maximum=6)
    matches = sorted(
        CONTEXT_ENTRIES,
        key=lambda entry: _score_entry(query, entry.keywords, entry.text),
        reverse=True,
    )
    results = [
        {
            "id": entry.id,
            "title": entry.title,
            "category": entry.category,
            "content": entry.text,
        }
        for entry in matches
        if _score_entry(query, entry.keywords, entry.text) > 0
    ][:limit]
    if not results:
        results = [
            {
                "id": entry.id,
                "title": entry.title,
                "category": entry.category,
                "content": entry.text,
            }
            for entry in CONTEXT_ENTRIES[:limit]
        ]
    return {"success": True, "query": query, "results": results}


def search_freecad_api_docs(
    query: str,
    symbol: str = "",
    limit: int = 4,
    headless_only: bool = True,
    live_wiki: bool = False,
) -> dict[str, Any]:
    limit = _bounded_limit(limit, default=4, maximum=8)
    query_text = " ".join(part for part in [query, symbol] if part).strip()
    candidates = [entry for entry in FREECAD_DOC_ENTRIES if entry.headless_safe or not headless_only]
    matches = sorted(
        candidates,
        key=lambda entry: _score_doc_entry(query_text, symbol, entry),
        reverse=True,
    )
    results = [
        _doc_entry_result(entry, query_text, symbol)
        for entry in matches
        if _score_doc_entry(query_text, symbol, entry) > 0
    ][:limit]

    if not results:
        results = [_doc_entry_result(entry, query_text, symbol) for entry in candidates[:limit]]

    result = {
        "success": True,
        "query": query,
        "symbol": symbol,
        "runtime_docker_image": FREECAD_DOCKER_IMAGE,
        "corpus_version": FREECAD_DOC_CORPUS_VERSION,
        "version_policy": (
            "Documentation is sourced from FreeCAD wiki pages and kept in a local index. Prefer entries marked "
            "conservative for the configured Docker runtime."
        ),
        "results": results,
    }
    if live_wiki:
        result["live_wiki"] = _search_live_freecad_wiki(query_text or query or symbol, limit)
    return result


def get_examples(topic: str, limit: int = 2) -> dict[str, Any]:
    limit = _bounded_limit(limit, default=2, maximum=4)
    matches = sorted(
        EXAMPLES,
        key=lambda entry: _score_entry(topic, entry.keywords, f"{entry.title} {entry.topic} {entry.notes}"),
        reverse=True,
    )
    results = [
        {
            "id": entry.id,
            "title": entry.title,
            "topic": entry.topic,
            "code": entry.code,
            "notes": entry.notes,
        }
        for entry in matches
        if _score_entry(topic, entry.keywords, f"{entry.title} {entry.topic} {entry.notes}") > 0
    ][:limit]
    if not results:
        results = [
            {
                "id": entry.id,
                "title": entry.title,
                "topic": entry.topic,
                "code": entry.code,
                "notes": entry.notes,
            }
            for entry in EXAMPLES[:limit]
        ]
    return {"success": True, "topic": topic, "results": results}


def known_error_fix(error_message: str) -> dict[str, Any]:
    lowered = error_message.lower()
    matches = []
    for entry in ERROR_FIXES:
        if any(pattern.lower() in lowered for pattern in entry["patterns"]):
            matches.append(entry["fix"])
    if not matches:
        matches.append(
            "Simplify the geometry, keep all API calls headless-safe, show final solids, recompute, and save to /data/output.FCStd."
        )
    return {"success": True, "fixes": matches}


def build_context_bundle_with_trace(user_prompt: str, error_details: str | None = None) -> tuple[str, list[dict]]:
    docs_result = search_docs(DEFAULT_CONTEXT_DOC_QUERY, limit=4)
    api_docs_result = search_freecad_api_docs(DEFAULT_API_DOC_QUERY, limit=4)
    examples_result = get_examples(DEFAULT_EXAMPLE_TOPIC, limit=2)
    sections = docs_result["results"]
    api_sections = api_docs_result["results"]
    examples = examples_result["results"]
    trace = [
        {
            "name": "search_docs",
            "source": "cadbench_preload",
            "arguments": {"query": DEFAULT_CONTEXT_DOC_QUERY, "limit": 4},
            "result": docs_result,
        },
        {
            "name": "search_freecad_api_docs",
            "source": "cadbench_preload",
            "arguments": {"query": DEFAULT_API_DOC_QUERY, "limit": 4},
            "result": api_docs_result,
        },
        {
            "name": "get_examples",
            "source": "cadbench_preload",
            "arguments": {"topic": DEFAULT_EXAMPLE_TOPIC, "limit": 2},
            "result": examples_result,
        },
    ]

    fixes = []
    if error_details:
        fixes_result = known_error_fix(error_details)
        fixes = fixes_result["fixes"]
        trace.append(
            {
                "name": "known_error_fix",
                "source": "cadbench_preload",
                "arguments": {"error_message": error_details},
                "result": fixes_result,
            }
        )

    lines = ["Curated FreeCAD context:"]
    for section in sections:
        lines.append(f"- {section['title']}: {section['content']}")
    for section in api_sections:
        lines.append(
            f"- API doc ({section['title']}): {section['summary']} Usage: {section['usage']} "
            f"Source: {section['source_url']}"
        )
    for example in examples:
        lines.append(f"- Example ({example['title']}):\n{example['code']}")
    for fix in fixes:
        lines.append(f"- Repair hint: {fix}")
    return "\n\n".join(lines), trace


def _score_entry(query: str, keywords: tuple[str, ...], text: str) -> int:
    query_terms = set(_tokens(query))
    if not query_terms:
        return 0
    keyword_score = sum(3 for keyword in keywords if keyword.lower() in query_terms)
    text_tokens = set(_tokens(text))
    text_score = len(query_terms & text_tokens)
    return keyword_score + text_score


def _score_doc_entry(query: str, symbol: str, entry: FreeCADDocEntry) -> int:
    score = _score_entry(
        query,
        entry.keywords,
        " ".join(
            [
                entry.title,
                entry.summary,
                entry.usage,
                entry.example,
                " ".join(entry.symbols),
            ]
        ),
    )
    normalized_symbol = _normalize_symbol(symbol)
    normalized_symbols = [_normalize_symbol(candidate) for candidate in entry.symbols]
    if normalized_symbol:
        for candidate in normalized_symbols:
            if normalized_symbol == candidate:
                score += 100
            elif normalized_symbol in candidate or candidate in normalized_symbol:
                score += 50
    return score


def _doc_entry_result(entry: FreeCADDocEntry, query: str, symbol: str) -> dict[str, Any]:
    return {
        "id": entry.id,
        "title": entry.title,
        "category": entry.category,
        "source_url": entry.source_url,
        "source_title": entry.source_title,
        "symbols": list(entry.symbols),
        "matched_symbol": _matched_symbol(symbol, entry.symbols),
        "summary": entry.summary,
        "usage": entry.usage,
        "example": entry.example,
        "headless_safe": entry.headless_safe,
        "version_notes": list(entry.version_notes),
        "score": _score_doc_entry(query, symbol, entry),
    }


def _search_live_freecad_wiki(query: str, limit: int) -> dict[str, Any]:
    if not query.strip():
        return {"success": False, "error": "Live wiki search requires a non-empty query.", "results": []}

    try:
        search_response = httpx.get(
            FREECAD_WIKI_API_URL,
            params={
                "action": "query",
                "format": "json",
                "list": "search",
                "srsearch": query,
                "srlimit": min(limit, 5),
            },
            timeout=FREECAD_WIKI_TIMEOUT_SECONDS,
        )
        search_response.raise_for_status()
        search_data = search_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "success": False,
            "error": f"Unable to query live FreeCAD wiki API: {exc}",
            "results": [],
            "fallback": "Used local FreeCAD documentation index.",
        }

    search_items = search_data.get("query", {}).get("search", [])
    if not search_items:
        return {"success": True, "results": [], "source": FREECAD_WIKI_API_URL}

    page_ids = [str(item["pageid"]) for item in search_items if "pageid" in item]
    extracts_by_page_id = _fetch_live_wiki_extracts(page_ids)
    results = []
    for item in search_items:
        page_id = str(item.get("pageid", ""))
        title = str(item.get("title", ""))
        results.append(
            {
                "page_id": page_id,
                "title": title,
                "source_url": _wiki_page_url(title),
                "snippet": _strip_html(str(item.get("snippet", ""))),
                "extract": extracts_by_page_id.get(page_id, ""),
            }
        )
    return {"success": True, "source": FREECAD_WIKI_API_URL, "results": results}


def _fetch_live_wiki_extracts(page_ids: list[str]) -> dict[str, str]:
    if not page_ids:
        return {}
    try:
        response = httpx.get(
            FREECAD_WIKI_API_URL,
            params={
                "action": "query",
                "format": "json",
                "prop": "extracts",
                "exintro": 1,
                "explaintext": 1,
                "pageids": "|".join(page_ids),
            },
            timeout=FREECAD_WIKI_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return {}
    pages = data.get("query", {}).get("pages", {})
    return {str(page_id): str(page.get("extract", "")) for page_id, page in pages.items()}


def _wiki_page_url(title: str) -> str:
    return f"{FREECAD_WIKI_PAGE_URL_PREFIX}{quote(title.replace(' ', '_'))}"


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value)


def _matched_symbol(symbol: str, candidates: tuple[str, ...]) -> str | None:
    normalized = _normalize_symbol(symbol)
    if not normalized:
        return None
    for candidate in candidates:
        if normalized == _normalize_symbol(candidate) or normalized in _normalize_symbol(candidate):
            return candidate
    return None


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_]+", value.lower())


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().lower().replace("()", "")


def _bounded_limit(value: int, default: int, maximum: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, maximum))
