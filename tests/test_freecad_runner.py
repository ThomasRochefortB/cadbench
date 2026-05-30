import subprocess
from pathlib import Path

import freecad_runner


def test_try_execute_freecad_script_exports_stl_in_same_docker_run(monkeypatch, tmp_path):
    docker_runs = []

    def fake_run(command, check=False, capture_output=False, text=False, timeout=None):
        if command == ["docker", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        if command[:2] == ["docker", "run"]:
            docker_runs.append(command)
            data_mount = command[command.index("-v") + 1]
            tmpdir_path = Path(data_mount.split(":", 1)[0])
            script = (tmpdir_path / "gen.py").read_text()

            assert "Export STL preview in the same FreeCAD run" in script
            assert "_cadbench_mesh.export" in script
            assert "/data/output_model1.stl" in script

            (tmpdir_path / "output_model1.FCStd").write_bytes(b"fcstd")
            (tmpdir_path / "output_model1.stl").write_bytes(b"stl")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(freecad_runner.subprocess, "run", fake_run)

    result = freecad_runner.try_execute_freecad_script(
        'doc.saveAs("/data/output_model1.FCStd")',
        "_model1",
        tmp_path,
    )

    assert len(docker_runs) == 1
    assert result.fcstd_path == tmp_path / "output_model1.FCStd"
    assert result.stl_path == tmp_path / "output_model1.stl"
    assert result.error_info is None
