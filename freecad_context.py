import re
from dataclasses import dataclass
from typing import Any


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


API_REFERENCE = {
    "Part.makeBox": {
        "signature": "Part.makeBox(length, width, height)",
        "description": "Create a rectangular solid starting at the origin.",
        "example": "box = Part.makeBox(length, width, height)",
    },
    "Part.makeCylinder": {
        "signature": "Part.makeCylinder(radius, height)",
        "description": "Create a cylinder along the local Z axis.",
        "example": "cyl = Part.makeCylinder(radius, height)",
    },
    "Part.makeSphere": {
        "signature": "Part.makeSphere(radius)",
        "description": "Create a sphere centered at the origin.",
        "example": "sphere = Part.makeSphere(radius)",
    },
    "Shape.cut": {
        "signature": "result = base.cut(tool)",
        "description": "Subtract one shape from another.",
        "example": "body = base.cut(hole)",
    },
    "Shape.fuse": {
        "signature": "result = first.fuse(second)",
        "description": "Union two shapes into one shape.",
        "example": "body = left.fuse(right)",
    },
    "App.Placement": {
        "signature": "App.Placement(App.Vector(x, y, z), App.Rotation(axis, degrees))",
        "description": "Assign translation and rotation to a shape.",
        "example": "shape.Placement = App.Placement(App.Vector(0, 0, 10), App.Rotation(App.Vector(1, 0, 0), 90))",
    },
    "Part.show": {
        "signature": "Part.show(shape)",
        "description": "Add a shape as a visible object in the active document.",
        "example": "Part.show(body)",
    },
    "doc.saveAs": {
        "signature": "doc.saveAs('/data/output.FCStd')",
        "description": "Save the generated FreeCAD document to the required CADBench path.",
        "example": "doc.saveAs('/data/output.FCStd')",
    },
}


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
                "description": "Search concise, version-pinned FreeCAD scripting guidance for headless CAD generation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "FreeCAD topic or modeling need to search for."},
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
                "name": "lookup_api",
                "description": "Look up a known FreeCAD Part/App API symbol and its safe usage pattern.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "API symbol, for example Part.makeBox, Shape.cut, App.Placement.",
                        }
                    },
                    "required": ["symbol"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_examples",
                "description": "Retrieve short FreeCAD Python examples for a modeling topic.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "Example topic such as booleans or placements."},
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
    if name == "lookup_api":
        return lookup_api(str(arguments.get("symbol", "")))
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


def lookup_api(symbol: str) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    for candidate, details in API_REFERENCE.items():
        if _normalize_symbol(candidate) == normalized or normalized in _normalize_symbol(candidate):
            return {"success": True, "symbol": candidate, **details}
    return {
        "success": False,
        "symbol": symbol,
        "error": "No curated API entry found. Prefer documented Part primitives and shape boolean methods.",
    }


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


def build_context_bundle(user_prompt: str, error_details: str | None = None) -> str:
    sections = search_docs(user_prompt, limit=4)["results"]
    examples = get_examples(user_prompt, limit=2)["results"]
    fixes = known_error_fix(error_details or "")["fixes"] if error_details else []

    lines = ["Curated FreeCAD context:"]
    for section in sections:
        lines.append(f"- {section['title']}: {section['content']}")
    for example in examples:
        lines.append(f"- Example ({example['title']}):\n{example['code']}")
    for fix in fixes:
        lines.append(f"- Repair hint: {fix}")
    return "\n\n".join(lines)


def _score_entry(query: str, keywords: tuple[str, ...], text: str) -> int:
    query_terms = set(_tokens(query))
    if not query_terms:
        return 0
    keyword_score = sum(3 for keyword in keywords if keyword.lower() in query_terms)
    text_tokens = set(_tokens(text))
    text_score = len(query_terms & text_tokens)
    return keyword_score + text_score


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
