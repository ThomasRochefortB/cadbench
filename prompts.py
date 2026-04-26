"""
Contains prompts used by the CAD generation system.
"""

ENHANCED_PROMPT_TEMPLATE = """
You are an expert FreeCAD developer. Return one standalone FreeCAD Python script for this CAD request.

Hard requirements:
- Output only valid Python code. Do not use Markdown fences.
- Use FreeCAD's headless-safe API. Do not import FreeCADGui.
- Create a document with `doc = App.newDocument("CADModel")`.
- Build the requested geometry with `Part` primitives and boolean operations.
- Add final solids to the document with `Part.show(...)`.
- Call `doc.recompute()` before saving.
- Save exactly to `/data/output.FCStd` with `doc.saveAs("/data/output.FCStd")`.
- Keep the script concise. Prefer simple robust geometry over decorative detail.
- Avoid fragile optional operations unless guarded by try/except, especially fillets, thickness, sweeps, and complex shells.
- Define dimensions as variables near the top.

Useful pattern:
import FreeCAD as App
import Part
import math

doc = App.newDocument("CADModel")

# create shapes...
# shape = Part.makeBox(20, 20, 20)
# Part.show(shape)

doc.recompute()
doc.saveAs("/data/output.FCStd")

User request: {user_prompt}
"""

REPAIR_PROMPT_TEMPLATE = """
You are repairing a FreeCAD Python script that failed in headless Docker execution.

Return only a complete corrected Python script. Do not use Markdown fences.

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
"""
