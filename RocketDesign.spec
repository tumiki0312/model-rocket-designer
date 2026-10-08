# PyInstaller の設定（build.py から使う）。  pyinstaller RocketDesign.spec
# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["main.py"],
    pathex=[],
    datas=[("assets/rocket.ico", "assets"), ("assets/rocket.png", "assets")],
    hiddenimports=[],
    excludes=[
        # 使わない大きなパッケージを入れない
        "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "gi", "IPython", "jupyter", "notebook",
        "pandas", "scipy", "sympy", "pytest", "unittest.mock", "tests",
        "matplotlib.tests", "numpy.tests", "numpy.f2py", "PIL.ImageQt",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RocketDesign",
    console=False,            # 黒いコンソール画面を出さない
    icon="assets/rocket.ico",
    version="build/version_info.txt",
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="RocketDesign", upx=False)
