"""Windows アプリ（.exe）を作る。

    build.bat をダブルクリック（またはビルド用の Python で  python build.py）

できるもの:
    dist/RocketDesign/RocketDesign.exe       … これをダブルクリックで起動（フォルダごと使う）
    dist/RocketDesign-<版>-windows.zip       … 配布用（解凍して RocketDesign.exe を起動）
"""
import importlib.metadata as md
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from rocketcalc import __version__  # noqa: E402

VERSION_INFO = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({v}, 0), prodvers=({v}, 0), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041104B0', [
      StringStruct('FileDescription', 'モデルロケット設計ツール'),
      StringStruct('ProductName', 'モデルロケット設計ツール'),
      StringStruct('FileVersion', '{s}'),
      StringStruct('ProductVersion', '{s}'),
      StringStruct('OriginalFilename', 'RocketDesign.exe'),
      StringStruct('InternalName', 'RocketDesign')])]),
    VarFileInfo([VarStruct('Translation', [0x0411, 1200])])
  ]
)
"""


def run(cmd):
    print(">", " ".join(map(str, cmd)), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def main():
    if not (ROOT / "assets" / "rocket.ico").exists():
        run([sys.executable, "tools/make_icon.py"])
    (ROOT / "build").mkdir(exist_ok=True)
    nums = ", ".join((__version__.split(".") + ["0", "0", "0"])[:3])
    (ROOT / "build" / "version_info.txt").write_text(VERSION_INFO.format(v=nums, s=__version__), encoding="utf-8")

    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "RocketDesign.spec"])

    app_dir = ROOT / "dist" / "RocketDesign"
    exe = app_dir / "RocketDesign.exe"

    # 受け取った人向けの説明書とライセンス表記（メモ帳で文字化けしないよう BOM 付き UTF-8）
    versions = {"version": __version__, "python": platform.python_version()}
    for pkg in ("numpy", "matplotlib", "pillow"):
        versions[pkg] = md.version(pkg)
    for name in ("はじめにお読みください.txt", "LICENSES.txt"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8").format(**versions)
        (app_dir / name).write_text(text, encoding="utf-8-sig")

    # 配布用 zip
    zip_path = ROOT / "dist" / f"RocketDesign-{__version__}-windows.zip"
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in app_dir.rglob("*"):
            z.write(f, Path("RocketDesign") / f.relative_to(app_dir))

    # 動作確認: zip を別の場所に解凍し、Python に頼らない環境で起動して 1 回計算させる
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        test_exe = Path(tmp) / "RocketDesign" / "RocketDesign.exe"
        result = Path(tmp) / "selftest.txt"
        windir = os.environ.get("SystemRoot", r"C:\Windows")
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(("PYTHON", "CONDA", "TCL", "TK", "VIRTUAL_ENV"))}
        env["PATH"] = os.pathsep.join([str(Path(windir) / "System32"), windir])
        subprocess.run([str(test_exe), "--selftest", str(result)], timeout=180, env=env, cwd=tmp)
        text = result.read_text(encoding="utf-8") if result.exists() else "（結果ファイルができませんでした）"
    print("---- 動作確認（zip を別の場所に解凍して起動） ----\n" + text)
    if not text.rstrip().endswith("OK"):
        sys.exit("動作確認に失敗しました")
    size = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file()) / 1e6
    print(f"\n完成: {exe}\n      （フォルダ {size:.0f} MB）\n配布用: {zip_path} ({zip_path.stat().st_size / 1e6:.0f} MB)")
    shutil.rmtree(ROOT / "build" / "RocketDesign", ignore_errors=True)


if __name__ == "__main__":
    main()
