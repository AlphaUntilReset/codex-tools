"""Build the asset-free public Windows program, observer and optional hook helper.

This is a separate build target. The private artwork gates and private release
script remain unchanged. No user asset pack, source history or diagnostics enter
the package. Prefer a clean CI checkout when publishing binaries.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
QT_DISTRIBUTIONS = ("PySide6-Essentials", "shiboken6")
LICENSE_DISTRIBUTIONS = QT_DISTRIBUTIONS + ("pillow", "numpy", "PyInstaller")
MEDIA_EXTENSIONS = {".png", ".webp", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".mp3", ".wav", ".mp4"}
QT_MODULES = {"Core", "Gui", "Network", "Widgets"}
QT_PLUGINS = {
    "platforms/qwindows.dll", "platforms/qoffscreen.dll",
    "styles/qmodernwindowsstyle.dll", "tls/qschannelbackend.dll",
    "networkinformation/qnetworklistmanager.dll",
}


def write_public_qt_hooks(directory: Path) -> None:
    """Keep unneeded plugins out before PyInstaller recursively collects DLLs.

    Its default QtGui hook also collects optional virtual-keyboard and PDF
    plugins, which have separate licensing and are not used by this PNG app.
    """
    directory.mkdir(parents=True, exist_ok=True)
    source = (
        "from pathlib import Path\n"
        "from PyInstaller.utils.hooks.qt import add_qt6_dependencies\n"
        "hiddenimports, binaries, datas = add_qt6_dependencies(__file__)\n"
        f"allowed = {sorted(QT_PLUGINS)!r}\n"
        "binaries = [(source, target) for source, target in binaries\n"
        "            if (Path(target).name + '/' + Path(source).name).lower() in allowed]\n"
    )
    for module in QT_MODULES:
        (directory / f"hook-PySide6.Qt{module}.py").write_text(source, encoding="utf-8")


def audit_qt_payload(package: Path) -> dict:
    """Fail on unexpected Qt modules/plugins instead of distributing them."""
    allowed_dlls = {f"qt6{name.lower()}.dll" for name in QT_MODULES}
    allowed_extensions = {f"qt{name.lower()}.pyd" for name in QT_MODULES}
    inventory = {}
    found_plugins = set()
    found_dlls = set()
    for file in package.rglob("*"):
        if not file.is_file():
            continue
        relative = file.relative_to(package).as_posix()
        lowered = relative.lower()
        name = file.name.lower()
        if name.startswith("qt6") and file.suffix.lower() == ".dll":
            if name not in allowed_dlls:
                raise RuntimeError(f"Unreviewed Qt library in public package: {relative}")
            found_dlls.add(name)
            inventory[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
        if "/pyside6/" not in lowered:
            continue
        if "/qml/" in lowered:
            raise RuntimeError(f"Unexpected Qt QML payload: {relative}")
        if name.startswith("qt") and file.suffix.lower() == ".pyd" and name not in allowed_extensions:
            raise RuntimeError(f"Unreviewed PySide module in public package: {relative}")
        if "/plugins/" in lowered:
            plugin = lowered.split("/plugins/", 1)[1]
            if plugin not in QT_PLUGINS:
                raise RuntimeError(f"Unreviewed Qt plugin in public package: {relative}")
            found_plugins.add(plugin)
        if file.suffix.lower() in (".dll", ".pyd"):
            inventory[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
    if found_dlls != allowed_dlls or not {"platforms/qwindows.dll", "platforms/qoffscreen.dll"} <= found_plugins:
        raise RuntimeError("Required Qt Windows/offscreen runtime is incomplete")
    return inventory


def collect_dependency_licenses(destination: Path) -> None:
    """Copy wheel notices as text, retaining subdirectories and failing closed."""
    supplemental = ROOT.parents[1] / "LICENSES"
    for filename in ("LGPL-3.0-only.txt", "GPL-3.0-only.txt"):
        if not (supplemental / filename).is_file():
            raise RuntimeError(f"Reviewed upstream license is missing: LICENSES/{filename}")
    from PySide6.QtCore import qVersion
    attribution_dir = supplemental / "qt-third-party"
    provenance = json.loads((attribution_dir / "PROVENANCE.json").read_text(encoding="utf-8"))
    attributions = provenance.get("files", [])
    if (provenance.get("schema_version") != 1 or provenance.get("qt_release") != qVersion()
            or len(attributions) != 147 or len({item["file"] for item in attributions}) != 147):
        raise RuntimeError("Qt third-party license inventory does not match the reviewed Qt runtime")
    for item in attributions:
        source = attribution_dir / item["file"]
        if (source.is_symlink() or source.parent.resolve() != attribution_dir.resolve()
                or source.suffix.lower() != ".txt"):
            raise RuntimeError("Unsafe Qt attribution path")
        content = source.read_bytes()
        if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise RuntimeError(f"Qt attribution text is missing or changed: {source.name}")
    for source in supplemental.rglob("*"):
        if not source.is_file() or source.suffix.lower() not in ("", ".txt", ".md", ".json", ".rst", ".html", ".htm"):
            continue
        content = source.read_bytes()
        content.decode("utf-8")
        target = destination / "upstream" / source.relative_to(supplemental)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    versions = {}
    for name in LICENSE_DISTRIBUTIONS:
        distribution = importlib.metadata.distribution(name)
        copied = []
        for entry in distribution.files or ():
            parts = entry.parts
            lowered = [part.lower() for part in parts]
            if "licenses" in lowered:
                relative = Path(*parts[lowered.index("licenses") + 1:])
            elif any(part.endswith(".dist-info") for part in lowered) and any(
                    marker in entry.name.lower() for marker in ("license", "copying", "gpl")):
                relative = Path(entry.name)
            else:
                continue
            if relative.suffix.lower() not in ("", ".txt", ".md", ".rst", ".html", ".htm"):
                continue
            content = Path(distribution.locate_file(entry)).read_bytes()
            content.decode("utf-8")  # Binary files must never become license payloads.
            target = destination / name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            copied.append(relative.as_posix())
        if not copied:
            raise RuntimeError(f"No redistribution license texts found for {name}")
        expression = distribution.metadata.get("License-Expression") or distribution.metadata.get("License", "")
        if name in QT_DISTRIBUTIONS and "LGPL-3.0" not in expression:
            raise RuntimeError(f"{name} metadata does not declare the reviewed LGPL license")
        versions[name] = {"version": distribution.version, "license_expression": expression,
                          "license_files": copied}
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        python_license = Path(sys.base_prefix) / "LICENSE"
    if not python_license.is_file():
        raise RuntimeError("Python runtime license is missing")
    (destination / "Python").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(python_license, destination / "Python/LICENSE.txt")
    (destination / "dependencies.json").write_text(json.dumps(versions, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build an asset-free public Windows package")
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist-public")
    args = parser.parse_args(argv)
    output = args.dist_dir.resolve()
    if not output.is_relative_to(ROOT) or output == ROOT or output == ROOT / "dist":
        parser.error("Use a dedicated output directory inside the component (not its root or private dist)")
    if os.name != "nt":
        parser.error("The Windows package must be built on Windows")
    work = ROOT / "build" / "public-package"
    specs = work / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    notices = work / "licenses"
    collect_dependency_licenses(notices)
    qt_hooks = work / "qt-hooks"
    write_public_qt_hooks(qt_hooks)
    environment = os.environ.copy()
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    environment["PATH"] = os.pathsep.join(map(str, (
        Path(sys.executable).parent, Path(sys.base_prefix),
        Path(sys.base_prefix) / "DLLs", windows / "System32", windows,
    )))
    for key in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH"):
        environment.pop(key, None)
    common = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
              "--workpath", str(work / "analysis"), "--specpath", str(specs)]
    # Deliberately no --add-data or --icon: all artwork is supplied after download.
    command = common + ["--windowed", "--name", "CodexToolsPet", "--distpath", str(output),
                        "--paths", str(ROOT / "src"), "--exclude-module", "tkinter",
                        # Essentials provides PySide6; the optional metapackage is not installed.
                        "--hidden-import", "PySide6.support.deprecated",
                        "--additional-hooks-dir", str(qt_hooks),
                        str(ROOT / "run_pet.py")]
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    package = output / "CodexToolsPet"
    shutil.copy2(package / "CodexToolsPet.exe", package / "CodexToolsMonitor.exe")
    hook_output = work / "hook-dist"
    subprocess.run(common + ["--console", "--name", "CodexToolsHook", "--distpath", str(hook_output),
                   "--exclude-module", "PySide6", "--exclude-module", "PIL",
                   str(ROOT / "tools/codex_pet_hook.py")], cwd=ROOT, env=environment, check=True)
    shutil.copytree(hook_output / "CodexToolsHook", package / "hooks", dirs_exist_ok=True)
    shutil.copytree(notices, package / "licenses", dirs_exist_ok=True)
    shutil.copyfile(ROOT.parents[1] / "LICENSE", package / "LICENSE")
    shutil.copyfile(ROOT / "PUBLIC-ASSETS.md", package / "PUBLIC-ASSETS.md")
    (package / "START.cmd").write_text('@echo off\nstart "" "%~dp0CodexToolsPet.exe"\n', encoding="ascii")
    (package / "README.txt").write_text(
        "ASSET-FREE PUBLIC BUILD\n\n"
        "Run CodexToolsPet.exe and select your own transparent PNG on first launch.\n"
        "The image is copied to user-assets/pet/idle.png beside the program.\n"
        "For animations, provide user-assets/pet/pack.json and your own PNG frames.\n"
        "No character artwork is included or licensed by this program.\n"
        "Keep the entire folder, including CodexToolsMonitor.exe, hooks and _internal.\n",
        encoding="utf-8")
    if any((package / relative).exists() for relative in
           ("assets", "user-assets", "resources", "cache", "_internal/assets", "_internal/resources", "_internal/cache")):
        raise RuntimeError("The public package unexpectedly contains an artwork or cache directory")
    if any(file.suffix.lower() in MEDIA_EXTENSIONS for file in package.rglob("*") if file.is_file()):
        raise RuntimeError("The public package unexpectedly contains a loose media file")
    inventory = audit_qt_payload(package)
    (package / "QT-INVENTORY.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    print(f"Asset-free package ready: {package}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
