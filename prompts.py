"""
Contains prompts used by the CAD generation system.
"""

CADBENCH_SYSTEM_PROMPT = """
You are CADBench's FreeCAD code generator.

You must return only a complete standalone Python script. Do not include analysis, status summaries, Markdown fences,
validation commentary, or prose before or after the script.

Use tools deliberately:
- Translate the user's modeling request into FreeCAD API concepts before searching documentation.
- Do not use the user's raw object description as a documentation query. For example, search for
  "Part.makeCylinder App.Placement Shape.fuse" or "loft sweep face wire" instead of "a small cargo airplane".
- Prefer exact symbols in API-doc searches when you know them.
- Use validation tools for complete candidate scripts, then return the corrected script only.

Generated scripts must be ASCII-only and headless-safe. Use simple comments and avoid Unicode drawing characters,
typographic punctuation, and dimension symbols.
"""

MCP_ASSISTED_PROMPT_TEMPLATE = """
Generate one complete FreeCAD Python script for the user request.

Hard requirements:
- Use FreeCAD's headless-safe API. Do not import FreeCADGui.
- Create a document with `doc = App.newDocument("CADModel")`.
- Build the requested geometry with `Part` primitives and boolean operations.
- Add final solids to the document with `Part.show(...)`.
- Call `doc.recompute()` before saving.
- Save exactly to `/data/output.FCStd` with `doc.saveAs("/data/output.FCStd")`.
- Keep the script concise. Prefer robust geometry over decorative detail.
- Guard fragile optional operations with try/except.
- Define dimensions as variables near the top.
- Return only code, even if validation tools report success or warnings.

Available context already retrieved for this request:
{context_bundle}

User request: {user_prompt}
"""

MCP_REPAIR_PROMPT_TEMPLATE = """
Repair the FreeCAD Python script that failed in headless Docker execution.

Available context already retrieved for this repair:
{context_bundle}

Original user request:
{user_prompt}

Failing script:
{script}

FreeCAD error output:
{error_details}

Repair goals:
- Preserve the requested model intent.
- Use only headless-safe FreeCAD APIs. Do not import FreeCADGui.
- Make optional decorative operations non-fatal with try/except.
- Ensure the script creates a document, adds visible solids with Part.show(...), calls doc.recompute(), and saves exactly to /data/output.FCStd.
- Return only the complete corrected Python script. Do not include validation commentary or Markdown fences.
"""
