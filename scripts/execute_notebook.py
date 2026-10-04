"""Verifica el cuaderno con un kernel local, sin registrar nada en el usuario."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "jupyter"
kernel_directory = CACHE / "kernels" / "pilot"
kernel_directory.mkdir(parents=True, exist_ok=True)
(CACHE / "runtime").mkdir(exist_ok=True)
(kernel_directory / "kernel.json").write_text(json.dumps({
    "argv": [str(ROOT / ".venv/bin/python"), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
    "display_name": "Python (piloto semáforos)", "language": "python",
}))
os.environ["JUPYTER_PATH"] = str(CACHE)
os.environ["JUPYTER_RUNTIME_DIR"] = str(CACHE / "runtime")
os.environ["IPYTHONDIR"] = str(ROOT / ".cache" / "ipython")
os.environ["MPLCONFIGDIR"] = str(ROOT / ".cache" / "matplotlib")

import nbformat
from nbclient import NotebookClient

path = ROOT / "archivo.ipynb"
notebook = nbformat.read(path, as_version=4)
NotebookClient(notebook, timeout=120, kernel_name="pilot", resources={"metadata": {"path": str(ROOT)}}).execute()
nbformat.write(notebook, path)
print("Cuaderno ejecutado y verificado:", path)
