# CADBench

<p align="center">
  <img src="static/logo.png" alt="CADBench Logo" width="200"/>
</p>

Source code for the cadbench LLM benchmark

## Getting Started

1.  **Set up the environment and install dependencies:**

    First, ensure you have `uv` installed (see [uv's installation guide](https://github.com/astral-sh/uv#installation)).

    Then, create a virtual environment using Python 3.12+ and install dependencies:

    ```bash
    # Create and activate a virtual environment (e.g., with Python 3.12)
    uv venv .venv --python 3.12
    source .venv/bin/activate

    # Install dependencies from pyproject.toml and sync with uv.lock
    uv sync
    ```

    If you modify `pyproject.toml` (e.g., add a new dependency), run:
    ```bash
    uv lock
    uv sync
    ```
    to update `uv.lock` and your environment.

2.  **Set your OpenAI API Key (optional):**

    If you want to use OpenAI for script generation, create a `.env` file in the project root:

    ```env
    OPENAI_API_KEY="sk-..."
    ```

    If not set, the application will return a predefined stub script.

3.  **Run the Flask application:**

    ```bash
    python app.py
    ```

4.  Open your browser to [http://localhost:8000](http://localhost:8000) to use CADBench.

**FreeCAD Integration:**

If FreeCAD is installed and `freecadcmd` (or `FreeCADCmd`) is available on your system's PATH, the generated Python script will be executed by the backend. The resulting `.FCStd` file will then be available for download from the web interface. Otherwise, only the script itself will be displayed.

## Installing FreeCAD

For CADBench to execute the generated Python scripts and produce `.FCStd` files, FreeCAD (specifically `freecadcmd` or `FreeCADCmd`) must be accessible to the `app.py` environment.

The recommended and supported method for running FreeCAD with CADBench is via a Docker container.

*   **Docker (Recommended):**
    Running FreeCAD in a Docker container provides a consistent and isolated environment. You will need to ensure that the `app.py` script (or the environment it runs in) can interact with this Docker container to execute `freecadcmd`.

    1.  **Pull a FreeCAD Docker Image:**
        You can use official or community-provided FreeCAD Docker images. The recommended image which includes `freecadcmd` is `linuxserver/freecad:0.20.2`.
        ```bash
        docker pull linuxserver/freecad:0.20.2
        ```
        For other options, refer to the [FreeCAD Compile on Docker documentation](https://wiki.freecad.org/Compile_on_Docker) and Docker Hub.

    2.  **Running `freecadcmd` via Docker:**
        The `try_execute_freecad_script` function in `app.py` currently uses `shutil.which` to find `freecadcmd` directly on the system PATH. To use Docker, this function would need to be modified to execute `freecadcmd` *inside* a Docker container. This typically involves:
        *   Mounting the temporary directory (containing `gen.py` and for `output.FCStd`) into the Docker container.
        *   Running a `docker run ... <freecad-image> freecadcmd /path/to/gen.py` command.
        *   Retrieving `output.FCStd` from the mounted volume.

    **Note:** The current version of `app.py` does **not** have built-in support for executing `freecadcmd` via Docker. This would be a necessary modification to strictly enforce Docker-only FreeCAD usage for script execution.

Alternatively, if you are developing locally and have `freecadcmd` on your PATH (from a direct install), `app.py` will attempt to use it. However, for reproducible or deployed environments, a Docker-based approach for FreeCAD is advised for future development.
