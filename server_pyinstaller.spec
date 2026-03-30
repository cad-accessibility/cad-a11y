# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


project_root = Path(SPECPATH)


def collect_tree(rel_dir: str, dest_prefix: str):
    files = []
    root = project_root / rel_dir
    if not root.exists():
        return files

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parent = path.relative_to(root).parent
        dest_dir = Path(dest_prefix) / rel_parent
        files.append((str(path), str(dest_dir).replace("\\", "/")))
    return files


datas = []
viewer_html = project_root / "accessible-3d-viewer.html"
if viewer_html.exists():
    datas.append((str(viewer_html), "."))

datas += collect_tree("model", "model")
datas += collect_tree("src/models/brep", "src/models/brep")

datas += copy_metadata("trimesh")
datas += copy_metadata("shapely")

hiddenimports = []
for module_name in ("src.converter", "trimesh", "shapely", "matplotlib", "scipy", "networkx"):
    hiddenimports += collect_submodules(module_name)

hiddenimports += [
    "flask",
    "flask_cors",
    "PIL",
    "numpy",
    "hid",
    "serial",
    "bleak",
    "godice",
    "OCC",
    "OCC.Core",
    "OCC.Display.SimpleGui",
]


block_cipher = None


a = Analysis(
    ["server.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cad-a11y-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="cad-a11y-server",
)
