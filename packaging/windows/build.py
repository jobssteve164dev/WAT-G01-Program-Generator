"""Build the x64 Windows installer from the project's production assets."""
import hashlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNTIME = "https://www.python.org/ftp/python/3.13.9/python-3.13.9-embed-amd64.zip"
SHA256 = "91d828c2da3a029b41699e918674a0cb379c02cf20dab9c501306885f837402a"


def build(stage):
    runtime = stage / "runtime.zip"
    urllib.request.urlretrieve(RUNTIME, runtime)
    if hashlib.sha256(runtime.read_bytes()).hexdigest() != SHA256:
        raise ValueError("Embedded Python checksum mismatch")
    payload = stage / "payload"
    with zipfile.ZipFile(runtime) as archive:
        archive.extractall(payload)
    (payload / "python313._pth").write_text("python313.zip\n.\nLib/site-packages\n", encoding="ascii")
    metadata = tomllib.loads((ROOT / "backend/pyproject.toml").read_text())
    # pip evaluates OS-specific dependency markers on the build host. Click
    # and Uvicorn also use colorama on the Windows target.
    subprocess.run([sys.executable, "-m", "pip", "install", "--platform", "win_amd64",
                    "--python-version", "3.13", "--implementation", "cp", "--abi", "cp313",
                    "--only-binary=:all:", "--no-compile", "--target", str(payload / "Lib/site-packages"),
                    *metadata["project"]["dependencies"], "colorama==0.4.6"], check=True)
    shutil.copytree(ROOT / "backend/wat_pilot", payload / "wat_pilot",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "frontend/dist", payload / "static")
    shutil.copy2(ROOT / "LICENSE", payload / "LICENSE-WAT")
    shutil.copy2(HERE / "launch.py", payload)
    output = ROOT / "output/windows"
    output.mkdir(parents=True, exist_ok=True)
    # Remove only files shipped by this installer during uninstall.
    entries = sorted(payload.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    with (stage / "uninstall.nsh").open("w", encoding="utf-8") as manifest:
        for entry in entries:
            path = str(entry.relative_to(payload)).replace("/", "\\")
            command = "RMDir" if entry.is_dir() else "Delete"
            print(f'{command} "$INSTDIR\\{path}"', file=manifest)
    subprocess.run(["makensis", "-V2", f"-DSTAGE={stage}",
                    f"-DOUTPUT={output / 'WAT-G01-Setup-x64.exe'}", str(HERE / "setup.nsi")], check=True)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        build(Path(directory))
