import io
import re
import tokenize


def strip_markdown_code_fence(script: str) -> str:
    script = script.strip()
    if script.startswith("```") and "```" in script[3:]:
        first_line_end = script.find("\n")
        if first_line_end == -1:
            return ""
        script = script[first_line_end + 1 :]
        if "```" in script:
            script = script[: script.rindex("```")]
    return script.strip()


def prepare_freecad_script(script: str, file_suffix: str) -> str:
    """Normalize model output so it runs in headless FreeCAD and uses our artifact names."""
    script = strip_markdown_code_fence(script)
    script_lines = script.splitlines()

    for index, line in enumerate(script_lines):
        script_lines[index] = _normalize_save_call(line, file_suffix)

    cleaned_lines = []
    for line in script_lines:
        if _line_mentions_token(line, "FreeCADGui") or ".setActiveDocument(" in line:
            cleaned_lines.append(f"# [removed] {line}")
        elif ".makeFillet(" in line and re.match(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=", line):
            cleaned_lines.extend(_make_non_fatal_assignment(line))
        else:
            cleaned_lines.append(line)

    script = "\n".join(cleaned_lines)
    return ensure_save_footer(script, file_suffix)


def _normalize_save_call(line: str, file_suffix: str) -> str:
    normalized = line.replace("doc.save(", "doc.saveAs(")
    normalized = normalized.replace("Doc.save(", "Doc.saveAs(")
    normalized = normalized.replace("App.ActiveDocument.save(", "App.ActiveDocument.saveAs(")
    normalized = normalized.replace("FreeCAD.ActiveDocument.save(", "FreeCAD.ActiveDocument.saveAs(")

    if any(pattern in line for pattern in ["saveAs", "save(", "SaveAs", "Save("]) and any(
        path in line
        for path in [
            "/data/output.FCStd",
            "/data/output",
            "output.FCStd",
            '"output.FCStd"',
            "'output.FCStd'",
        ]
    ):
        normalized = normalized.replace("output.FCStd", f"output{file_suffix}.FCStd")
        normalized = normalized.replace('"output"', f'"output{file_suffix}"')
        normalized = normalized.replace("'output'", f"'output{file_suffix}'")

    return normalized


def _line_mentions_token(line: str, token_text: str) -> bool:
    try:
        tokens = tokenize.generate_tokens(io.StringIO(line).readline)
        return any(token.string == token_text for token in tokens)
    except tokenize.TokenError:
        return token_text in line


def _make_non_fatal_assignment(line: str) -> list[str]:
    indent = line[: len(line) - len(line.lstrip())]
    return [
        f"{indent}try:",
        f"{indent}    {line.strip()}",
        f"{indent}except Exception:",
        f"{indent}    pass",
    ]


def ensure_save_footer(script: str, file_suffix: str) -> str:
    output_path = f"/data/output{file_suffix}.FCStd"
    if output_path in script:
        return script

    lines = script.rstrip().splitlines()
    if not any("doc =" in line and ".newDocument(" in line for line in lines):
        lines.insert(0, 'doc = App.newDocument("CADModel")')
    if not any("import FreeCAD as App" in line or "import FreeCAD" in line for line in lines):
        lines.insert(0, "import FreeCAD as App")

    lines.extend(
        [
            "",
            "# [added] Ensure CADBench receives a saved FreeCAD document.",
            "doc.recompute()",
            f'doc.saveAs("{output_path}")',
        ]
    )
    return "\n".join(lines)
