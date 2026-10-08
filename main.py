"""モデルロケット設計ツールの起動スクリプト（アプリ化した .exe の入口でもある）。

    python main.py                 アプリを起動
    python main.py --selftest 結果.txt
                                   起動して 1 回計算し、結果をファイルに書いて終了（動作確認用）
"""
import sys


def _enable_high_dpi():
    """Windows の高解像度画面で文字がぼやけないようにする。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def _selftest(path: str) -> None:
    import traceback

    from rocketcalc import __version__
    from rocketcalc.gui import RocketDesignApp

    app = RocketDesignApp()
    lines = [f"version {__version__}"]

    def check(tries=[0]):
        tries[0] += 1
        if "計算中" in app.status.get() and tries[0] < 300:
            app.after(100, check)
            return
        try:
            lines.append("status: " + app.status.get())
            r = app.view3d.result
            lines.append(f"flight points: {len(r.t)}  apogee: {r.apogee:.1f} m")
            app.show_manual()
            app.update()
            lines.append(f"manual: {app.manual.text.get('1.0', '2.0').strip()}")
            lines.append("OK")
        except Exception:  # noqa: BLE001 結果ファイルに残す
            lines.append(traceback.format_exc())
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        app._on_close()

    app.after(300, check)
    app.mainloop()


def main():
    _enable_high_dpi()
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        _selftest(sys.argv[2])
        return
    from rocketcalc.gui import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
