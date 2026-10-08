"""README 用の画像（docs/images/*.png）をアプリの図から作る。

    python tools/make_screenshots.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rocketcalc import templates  # noqa: E402
from rocketcalc.gui import RocketDesignApp  # noqa: E402

OUT = ROOT / "docs" / "images"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    app = RocketDesignApp()
    steps = []

    def wait(fn):
        def check():
            if "計算中" in app.status.get() or app._after_id is not None:
                app.after(100, check)
            else:
                fn()
        app.after(150, check)

    def next_step():
        if steps:
            steps.pop(0)()
        else:
            app._on_close()

    def standard():
        app.load_template(templates.standard)

        def shots():
            app.stab_fig.savefig(OUT / "stability.png", dpi=110)
            app.flight_fig.savefig(OUT / "flight.png", dpi=100)
            v = app.view3d
            r = v.result
            v.show_time(1.2)
            v.fig_main.savefig(OUT / "3d_flight.png", dpi=100)
            v.fig_close.savefig(OUT / "3d_attitude.png", dpi=100)
            v.show_time(r.separation_time + 0.5 * r.inflation_time)
            v.fig_close.savefig(OUT / "3d_parachute.png", dpi=100)
            next_step()
        wait(shots)

    def airplane():
        app.load_template(templates.airplane)
        wait(lambda: (app.stab_fig.savefig(OUT / "airplane.png", dpi=110), next_step()))

    steps += [standard, airplane]
    app.after(200, next_step)
    app.mainloop()
    for p in sorted(OUT.glob("*.png")):
        print(p.relative_to(ROOT), f"{p.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
