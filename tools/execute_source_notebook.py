"""Execute with this project's Python, without installing a global Jupyter kernel."""
from pathlib import Path
import json
import sys
import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpecManager

ROOT = Path(__file__).resolve().parents[1]
directory = ROOT / ".jupyter" / "kernels" / "apl-news"
directory.mkdir(parents=True, exist_ok=True)
(directory / "kernel.json").write_text(json.dumps({"argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"], "display_name": "Python (APL news)", "language": "python"}))
path = ROOT / "EDA" / "описание источников.ipynb"
nb = nbformat.read(path, as_version=4)
km = KernelManager(kernel_name="apl-news", kernel_spec_manager=KernelSpecManager(kernel_dirs=[str(directory.parent)]))
NotebookClient(nb, km=km, timeout=180, resources={"metadata": {"path": str(ROOT / "EDA")}}).execute()
nbformat.write(nb, path)
print("Executed:", path)
