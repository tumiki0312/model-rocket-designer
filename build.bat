@echo off
rem モデルロケット設計ツールを Windows アプリ（.exe）にする。ダブルクリックで実行。
chcp 65001 > nul
cd /d "%~dp0"
if not exist ".venv-build\Scripts\python.exe" (
    echo ビルド用の Python 環境を作っています...
    python -m venv .venv-build || goto :error
    ".venv-build\Scripts\python.exe" -m pip install --disable-pip-version-check numpy matplotlib pyinstaller || goto :error
)
".venv-build\Scripts\python.exe" build.py || goto :error
echo.
echo 完成しました。dist\RocketDesign\RocketDesign.exe を起動してください。
pause
exit /b 0
:error
echo.
echo ビルドに失敗しました。上のメッセージを確認してください。
pause
exit /b 1
