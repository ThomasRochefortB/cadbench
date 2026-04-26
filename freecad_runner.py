import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from config import ARTIFACTS_DIR, FREECAD_DOCKER_IMAGE


@dataclass
class FreeCADExecutionResult:
    fcstd_path: Path | None
    stl_path: Path | None = None
    error_info: str | None = None


def make_docker_command(tmpdir_path: Path, script_name: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--memory",
        "1g",
        "--cpus",
        "2",
        "--pids-limit",
        "256",
        "--security-opt",
        "no-new-privileges",
        "-e",
        f"PUID={os.getuid()}",
        "-e",
        f"PGID={os.getgid()}",
        "-v",
        f"{str(tmpdir_path.resolve())}:/data",
        FREECAD_DOCKER_IMAGE,
        "freecadcmd",
        f"/data/{script_name}",
    ]


def try_execute_freecad_script(
    script: str, file_suffix: str = "", artifact_dir: Path | None = None
) -> FreeCADExecutionResult:
    """Run FreeCAD in headless Docker and return generated paths plus diagnostics."""
    error_info = None
    artifact_dir = artifact_dir or ARTIFACTS_DIR / uuid.uuid4().hex

    try:
        subprocess.run(["docker", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Docker command not found or Docker is not running. Skipping FreeCAD execution.")
        return FreeCADExecutionResult(None, error_info="Docker command not found or Docker is not running")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        script_path = tmpdir_path / "gen.py"
        script_path.write_text(script)

        try:
            process = _run_freecad_script(tmpdir_path, "gen.py", timeout=60)
            error_info = _extract_error_info(process.stderr)
        except subprocess.TimeoutExpired:
            print("FreeCAD Docker execution timed out.")
            return FreeCADExecutionResult(None, error_info="FreeCAD Docker execution timed out")
        except Exception as exc:
            print(f"An error occurred while trying to run FreeCAD in Docker: {exc}")
            return FreeCADExecutionResult(None, error_info=f"Error running FreeCAD in Docker: {str(exc)}")

        if process.returncode != 0:
            docker_failure_message = f"FreeCAD Docker execution failed with return code {process.returncode}"
            docker_details = "\n".join(line.strip() for line in process.stderr.splitlines() if line.strip())
            error_info = "\n".join(filter(None, [error_info, docker_failure_message, docker_details]))

        out_file = _resolve_fcstd_output(tmpdir_path, file_suffix)
        if out_file.exists() and out_file.stat().st_size > 0:
            stl_final_path = _export_stl(tmpdir_path, file_suffix, artifact_dir)
            final_path = artifact_dir / f"output{file_suffix}.FCStd"
            return _move_fcstd(out_file, final_path, stl_final_path, error_info)

        error_info = _missing_output_error(tmpdir_path, process, script, error_info)
        return FreeCADExecutionResult(None, error_info=error_info)


def _run_freecad_script(tmpdir_path: Path, script_name: str, timeout: int) -> subprocess.CompletedProcess:
    docker_command = make_docker_command(tmpdir_path, script_name)
    print(f"Executing FreeCAD script in Docker: {' '.join(docker_command)}")
    process = subprocess.run(docker_command, check=False, capture_output=True, text=True, timeout=timeout)
    print("FreeCAD Docker execution stdout:\n" + process.stdout)
    if process.stderr:
        print("FreeCAD Docker execution stderr:\n" + process.stderr)
    return process


def _extract_error_info(stderr: str) -> str | None:
    error_lines = []
    for line in stderr.splitlines():
        if any(err in line for err in ["Exception", "Error:", "has no attribute", "Traceback", "FileNotFoundError"]):
            error_lines.append(line.strip())
    return "\n".join(error_lines) if error_lines else None


def _resolve_fcstd_output(tmpdir_path: Path, file_suffix: str) -> Path:
    out_file = tmpdir_path / f"output{file_suffix}.FCStd"
    if not out_file.exists() or out_file.stat().st_size == 0:
        generic_out_file = tmpdir_path / "output.FCStd"
        if generic_out_file.exists() and generic_out_file.stat().st_size > 0:
            print("Model-specific file not found, but generic output.FCStd exists. Using that instead.")
            shutil.copy(generic_out_file, out_file)
    return out_file


def _export_stl(tmpdir_path: Path, file_suffix: str, artifact_dir: Path) -> Path | None:
    stl_out_file = tmpdir_path / f"output{file_suffix}.stl"
    stl_script = (
        f"import FreeCAD\n"
        f"doc = FreeCAD.open('/data/output{file_suffix}.FCStd')\n"
        f"import Mesh\n"
        f"Mesh.export(doc.Objects, '/data/output{file_suffix}.stl')\n"
    )
    (tmpdir_path / "export_stl.py").write_text(stl_script)

    try:
        _run_freecad_script(tmpdir_path, "export_stl.py", timeout=30)
        if stl_out_file.exists() and stl_out_file.stat().st_size > 0:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            stl_final_path = artifact_dir / f"output{file_suffix}.stl"
            shutil.move(str(stl_out_file), str(stl_final_path))
            print(f"Successfully moved STL to {stl_final_path}")
            return stl_final_path
    except Exception as exc:
        print(f"Error exporting STL: {exc}")
    return None


def _move_fcstd(
    out_file: Path, final_path: Path, stl_final_path: Path | None, error_info: str | None
) -> FreeCADExecutionResult:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(out_file), str(final_path))
        print(f"Successfully moved {out_file} to {final_path}")
    except Exception as exc:
        print(f"Error moving FreeCAD output file: {exc}")
        try:
            shutil.copy(str(out_file), str(final_path))
            print(f"Successfully copied {out_file} to {final_path} (fallback).")
            out_file.unlink(missing_ok=True)
        except Exception as copy_exc:
            print(f"Error copying FreeCAD output file (fallback): {copy_exc}")
            return FreeCADExecutionResult(None, error_info=f"Error copying FreeCAD output file: {str(copy_exc)}")
    return FreeCADExecutionResult(final_path, stl_final_path, error_info)


def _missing_output_error(
    tmpdir_path: Path, process: subprocess.CompletedProcess, script: str, error_info: str | None
) -> str:
    print(f"Output file {tmpdir_path / 'output.FCStd'} not found or is empty after FreeCAD execution.")
    try:
        print("Directory listing of temp folder after FreeCAD run:")
        for path in tmpdir_path.iterdir():
            try:
                print(f"  {path.name} (size: {path.stat().st_size} bytes)")
            except Exception:
                print(f"  {path.name} (unable to stat)")
    except Exception as exc:
        print(f"Unable to list temp directory for diagnostics: {exc}")

    if process.returncode == 0:
        message = "FreeCAD process exited cleanly, but the expected output.FCStd file was not created by the script."
    else:
        message = f"FreeCAD process exited with code {process.returncode}."
    print(message)
    print("Script content was:\n", script)
    return "\n".join(filter(None, [error_info, message]))
