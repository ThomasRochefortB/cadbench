"""
Contains prompts used by the CAD generation system.
"""

PROMPT_TEMPLATE = """
You are an expert FreeCAD developer. Write a standalone FreeCAD Python script that fulfils the following user request.
The script MUST:
1. Import FreeCAD and needed modules.
2. Build the geometry corresponding to the user description.
3. Save the resulting document to a file called /data/output.FCStd.
Do NOT add explanations or comments outside the python code. Only output valid python code.
User request: {user_prompt}
""" 