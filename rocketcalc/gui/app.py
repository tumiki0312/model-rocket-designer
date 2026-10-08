"""メインウィンドウ。"""
from __future__ import annotations

import copy
import json
import math
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from tkinter import filedialog, messagebox, ttk

import matplotlib.patches as mpatches
import numpy as np

from .. import APP_NAME, __version__, templates
from ..components import Rocket
from ..design import (G, Design, LaunchConfig, RecoveryConfig, analyze_stability, atmosphere, descent_rate,
                      judge_margin, judge_ratio, required_chute_diameter)
from ..flight import CHUTE, optimal_delay, simulate
from ..motors import CLUSTER_LAYOUTS, Motor, MotorCluster, builtin_motors, parse_eng
from .editor import ComponentEditor
from .manual import SECTIONS as MANUAL_SECTIONS
from .manual import ManualWindow
from .viewer3d import Flight3DView
from .widgets import (FIN_PALETTE, PALETTE, STATUS_COLORS, ResultTable, ScrollableFrame, fit_geometry, fmt,
                      make_figure, set_window_icon)

APP_TITLE = "モデルロケット設計ツール"
SAME_MOTOR = "主と同じ"


@dataclass
class Field:
    key: str
    label: str
    unit: str
    default: object
    kind: str = "float"   # float / int / motor / delay
    hint: str = ""


FLIGHT_SECTIONS: list[tuple[str, list[Field]]] = [
    ("モーター", [
        Field("motor", "モーター（主）", "", "Estes C6", "motor"),
        Field("delay", "放出遅延", "s", 5, "delay"),
        Field("motor_overhang", "後端からの突出量", "mm", 5),
    ]),
    ("モーターの本数（クラスター）", [
        Field("cluster_count", "本数", "本", 1, "int", hint="1〜9 本。2 本以上で下の設定が使われます"),
        Field("cluster_layout", "配置", "", CLUSTER_LAYOUTS["ring"], "layout"),
        Field("outer_motor", "周りのモーター", "", SAME_MOTOR, "outer",
              hint="「中心に 1 本＋周り」の時の周りのモーターの種類"),
        Field("cluster_radius", "配置半径", "mm", 0, hint="機軸からモーター中心までの距離。0 で隙間なく並べる"),
        Field("cluster_angle", "1 本目の向き", "°", 0, hint="0° = 真上（フィンの 1 枚目と同じ基準）"),
        Field("failed", "点火しない本数", "本", 0, "int", hint="点火不良の想定。周りのモーターから順に止まります"),
    ]),
    ("回収（パラシュート）", [
        Field("chute_diameter", "直径", "mm", 300, hint="0 でパラシュートなし"),
        Field("chute_cd", "Cd", "", 0.8, hint="平面円形 0.75〜0.8、ドーム型 1.2〜1.5"),
        Field("shock_cord", "ショックコード長", "mm", 800, hint="ノーズと機体をつなぐひも。伸びきるまでが分離"),
        Field("inflation_time", "開傘時間", "s", 0, hint="0 で自動（直径と速度から推定）"),
    ]),
    ("打ち上げ条件", [
        Field("rod_length", "ランチロッド長", "m", 1.0),
        Field("rod_angle", "発射角（風上へ傾斜）", "°", 3),
        Field("wind_speed", "平均風速", "m/s", 2),
        Field("turbulence", "突風の強さ", "%", 20, hint="平均風速に対するばらつき。0 で一定の風"),
        Field("seed", "突風パターン番号", "", 1, "int", hint="変えると別の突風パターンで計算します"),
        Field("misalign", "推力のずれ角", "°", 0, hint="モーター取付誤差。姿勢の乱れ方を見たい時に"),
        Field("temperature", "気温", "°C", 15),
    ]),
]
FLIGHT_FIELDS = [f for _, fs in FLIGHT_SECTIONS for f in fs]


class RocketDesignApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        set_window_icon(self)
        self.geometry(fit_geometry(self, 1500, 920))
        self.minsize(900, 600)
        self.motors: dict[str, Motor] = builtin_motors()
        self.custom_motors: list[Motor] = []
        self.vars: dict[str, tk.StringVar] = {}
        self.hint_labels: dict[str, ttk.Label] = {}
        self._after_id = None
        self.current_file: str | None = None
        self.design: Design | None = None
        self.optimal = math.nan
        self.flight = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.manual: ManualWindow | None = None
        self._gen = 0
        self._future = None

        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=24)
        style.configure("Hint.TLabel", foreground="#666666")
        style.configure("Big.TLabel", font=("Yu Gothic UI", 11, "bold"))
        style.configure("Value.TLabel", font=("Yu Gothic UI", 10, "bold"))
        style.configure("Bad.TEntry", foreground="#c62828")

        self._build_menu()
        self._build_layout()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.editor.set_rocket(templates.standard())
        self.recalc()

    # ------------------------------------------------------------------ UI 構築
    def _build_menu(self):
        menubar = tk.Menu(self)
        filemenu = tk.Menu(menubar, tearoff=False)
        filemenu.add_command(label="新規", command=lambda: self.load_template(templates.standard))
        filemenu.add_command(label="開く...", command=self.open_design, accelerator="Ctrl+O")
        filemenu.add_command(label="上書き保存", command=self.save_design, accelerator="Ctrl+S")
        filemenu.add_command(label="名前を付けて保存...", command=lambda: self.save_design(ask=True))
        filemenu.add_separator()
        filemenu.add_command(label="モーターデータ (.eng) を読み込み...", command=self.load_eng)
        filemenu.add_separator()
        filemenu.add_command(label="終了", command=self._on_close)
        menubar.add_cascade(label="ファイル", menu=filemenu)
        tmenu = tk.Menu(menubar, tearoff=False)
        for name, fn in templates.TEMPLATES.items():
            tmenu.add_command(label=name, command=lambda f=fn: self.load_template(f))
        menubar.add_cascade(label="テンプレート", menu=tmenu)
        helpmenu = tk.Menu(menubar, tearoff=False)
        helpmenu.add_command(label="マニュアル", command=self.show_manual, accelerator="F1")
        helpmenu.add_command(label="安定比について", command=lambda: self.show_manual("安定性（安定比 cal と %）"))
        helpmenu.add_command(label="計算方法について", command=self.show_about)
        helpmenu.add_separator()
        helpmenu.add_command(label="バージョン情報", command=self.show_version)
        menubar.add_cascade(label="ヘルプ", menu=helpmenu)
        self.config(menu=menubar)
        self.bind("<Control-o>", lambda e: self.open_design())
        self.bind("<Control-s>", lambda e: self.save_design())
        self.bind_all("<F1>", lambda e: self.show_manual())

    def _build_layout(self):
        self.status = tk.StringVar(value="")
        self.status_label = tk.Label(self, textvariable=self.status, anchor="w", padx=8, pady=3)
        self.status_label.pack(fill="x", side="bottom")

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Notebook(paned)
        paned.add(left, weight=0)
        sf = ScrollableFrame(left, width=380)
        left.add(sf, text="機体構成")
        self.editor = ComponentEditor(sf.inner, on_change=self.schedule_recalc, on_status=self.set_status)
        self.editor.pack(fill="both", expand=True)
        sf2 = ScrollableFrame(left, width=380)
        left.add(sf2, text="モーター・飛行条件")
        self._build_flight_inputs(sf2.inner)

        self.notebook = ttk.Notebook(paned)
        paned.add(self.notebook, weight=1)
        self._build_stability_tab()
        self._build_aero_tab()
        self._build_flight_tab()
        self.view3d = Flight3DView(self.notebook)
        self.notebook.add(self.view3d, text="3D 姿勢ビュー")
        self._build_recovery_tab()
        self._build_motor_tab()

    def _build_flight_inputs(self, parent):
        for title, fields in FLIGHT_SECTIONS:
            box = ttk.LabelFrame(parent, text=title, padding=(8, 4))
            box.pack(fill="x", pady=(0, 8))
            box.columnconfigure(1, weight=1)
            row = 0
            for f in fields:
                var = tk.StringVar(value=str(f.default))
                self.vars[f.key] = var
                ttk.Label(box, text=f.label).grid(row=row, column=0, sticky="w", pady=2)
                if f.kind == "motor":
                    w = ttk.Combobox(box, textvariable=var, state="readonly", width=14, values=list(self.motors))
                    self.motor_combo = w
                elif f.kind == "delay":
                    w = ttk.Combobox(box, textvariable=var, width=8)
                    self.delay_combo = w
                elif f.kind == "layout":
                    w = ttk.Combobox(box, textvariable=var, state="readonly", width=16,
                                     values=list(CLUSTER_LAYOUTS.values()))
                elif f.kind == "outer":
                    w = ttk.Combobox(box, textvariable=var, state="readonly", width=14,
                                     values=[SAME_MOTOR] + list(self.motors))
                    self.outer_combo = w
                else:
                    w = ttk.Entry(box, textvariable=var, width=10, justify="right")
                w.grid(row=row, column=1, sticky="ew", padx=4, pady=2)
                ttk.Label(box, text=f.unit, width=4).grid(row=row, column=2, sticky="w")
                row += 1
                if f.hint or f.kind in ("motor", "delay"):
                    hint = ttk.Label(box, text=f.hint, style="Hint.TLabel", wraplength=320)
                    hint.grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 3))
                    self.hint_labels[f.key] = hint
                    row += 1
                var.trace_add("write", lambda *_: self.schedule_recalc())
        self.vars["motor"].trace_add("write", lambda *_: self._on_motor_change())
        self._on_motor_change()
        btns = ttk.Frame(parent)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="遅延を最適値にする", command=self.apply_optimal_delay).pack(side="left")
        ttk.Button(btns, text="再計算", command=self.recalc).pack(side="left", padx=6)

    def _build_stability_tab(self):
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="機体図・安定性")
        self.stab_fig, self.stab_canvas, tb = make_figure(tab, (9, 5))
        self.stab_table = ResultTable(tab, height=10)
        self.stab_table.pack(side="bottom", fill="x")
        tb.pack(side="bottom", fill="x")
        self.stab_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _build_aero_tab(self):
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="空力・質量")
        tables = ttk.Frame(tab)
        tables.pack(side="bottom", fill="x")
        self.cd_table = ResultTable(tables, columns=("抗力の内訳", "Cd", "割合"), widths=(170, 70, 70), height=8)
        self.cd_table.pack(side="left", fill="both", expand=True)
        self.mass_table = ResultTable(tables, columns=("部品", "質量", "重心（先端から）"), widths=(200, 80, 110), height=8)
        self.mass_table.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.aero_fig, self.aero_canvas, tb = make_figure(tab, (9, 4))
        tb.pack(side="bottom", fill="x")
        self.aero_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _build_flight_tab(self):
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="飛行解析（6自由度）")
        self.flight_fig, self.flight_canvas, tb = make_figure(tab, (10, 5.2))
        self.flight_table = ResultTable(tab, height=9)
        self.flight_table.pack(side="bottom", fill="x")
        tb.pack(side="bottom", fill="x")
        self.flight_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _build_recovery_tab(self):
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="回収")
        calc = ttk.LabelFrame(tab, text="必要なパラシュート直径を求める", padding=8)
        calc.pack(fill="x")
        self.target_rate = tk.StringVar(value="4.5")
        ttk.Label(calc, text="目標降下速度").pack(side="left")
        ttk.Entry(calc, textvariable=self.target_rate, width=6, justify="right").pack(side="left", padx=4)
        ttk.Label(calc, text="m/s  →").pack(side="left")
        self.required_label = ttk.Label(calc, text="", style="Big.TLabel")
        self.required_label.pack(side="left", padx=8)
        ttk.Button(calc, text="この直径を使う", command=self.apply_required_diameter).pack(side="left", padx=8)
        self.target_rate.trace_add("write", lambda *_: self.schedule_recalc())
        self.rec_fig, self.rec_canvas, tb = make_figure(tab, (9, 4))
        self.rec_table = ResultTable(tab, height=6)
        self.rec_table.pack(side="bottom", fill="x")
        tb.pack(side="bottom", fill="x")
        self.rec_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _build_motor_tab(self):
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="モーター")
        top = ttk.Frame(tab)
        top.pack(fill="x")
        ttk.Button(top, text=".eng ファイルを読み込み...", command=self.load_eng).pack(side="left")
        ttk.Button(top, text="全モーターで比較", command=self.compare_motors).pack(side="left", padx=6)
        ttk.Label(top, text="内蔵データは近似値です。正確なデータは thrustcurve.org の .eng を読み込んでください。",
                  style="Hint.TLabel").pack(side="left", padx=10)
        cols = ("モーター", "全力積", "平均推力", "安定比", "初期加速度", "到達高度", "最高速度", "ロッド離脱速度",
                "最適遅延", "選べる遅延", "判定")
        self.cmp_table = ResultTable(tab, columns=cols, widths=(105, 70, 70, 120, 75, 70, 70, 95, 65, 80, 200),
                                     height=6)
        self.cmp_table.pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Label(tab, text="モーター比較（現在の機体で各モーターを飛ばした結果）").pack(side="bottom", anchor="w")
        self.motor_fig, self.motor_canvas, tb = make_figure(tab, (9, 3.5))
        self.motor_table = ResultTable(tab, height=5)
        self.motor_table.pack(side="bottom", fill="x")
        tb.pack(side="bottom", fill="x")
        self.motor_canvas.get_tk_widget().pack(fill="both", expand=True)

    # ------------------------------------------------------------------ 入力の読み取り
    def _on_motor_change(self):
        motor = self.motors.get(self.vars["motor"].get())
        if motor is None:
            return
        delays = [f"{d:g}" for d in motor.delay_list]
        self.delay_combo.configure(values=delays)
        self.hint_labels["motor"].configure(
            text=f"{motor.impulse_class} 級 / {motor.total_impulse:.2f} Ns / 燃焼 {motor.burn_time:.2f} s / "
                 f"φ{motor.diameter * 1000:g}×{motor.length * 1000:g} mm / {motor.total_mass * 1000:.1f} g")
        self.hint_labels["delay"].configure(text=f"このモーターで選べる遅延: {', '.join(delays) or 'なし'} 秒")

    def _update_cluster_hint(self, cluster: MotorCluster):
        if cluster.count == 1:
            text = "1 本（機体の中心）"
        else:
            lit = sum(m.ignited for m in cluster.mounts)
            r = max(m.offset for m in cluster.mounts) * 1000
            text = (f"{cluster.name}：合計 {cluster.total_impulse:.1f} Ns / 最大推力 {cluster.max_thrust:.1f} N / "
                    f"全質量 {cluster.total_mass * 1000:.0f} g / 配置半径 {r:.1f} mm / 外径 {cluster.diameter * 1000:.1f} mm")
            if lit < cluster.count:
                text += f"\n⚠ {cluster.count - lit} 本が点火しない想定（推力が偏ります）"
        self.hint_labels["cluster_count"].configure(text=text)

    def _read_values(self) -> dict:
        vals = {}
        for f in FLIGHT_FIELDS:
            raw = self.vars[f.key].get().strip()
            if f.kind in ("motor", "layout", "outer"):
                vals[f.key] = raw
                continue
            try:
                vals[f.key] = int(raw) if f.kind == "int" else float(raw)
            except ValueError:
                raise ValueError(f"「{f.label}」に数値を入力してください") from None
        return vals

    def build_design(self) -> Design:
        v = self._read_values()
        motor = self.motors.get(v["motor"])
        if motor is None:
            raise ValueError("モーターを選択してください")
        outer = None if v["outer_motor"] in ("", SAME_MOTOR) else self.motors.get(v["outer_motor"])
        layout = next((k for k, lb in CLUSTER_LAYOUTS.items() if lb == v["cluster_layout"]), "ring")
        # 比較表で主モーターだけ入れ替えられるよう、主以外の設定を覚えておく
        self.cluster_args = dict(count=v["cluster_count"], layout=layout, radius=v["cluster_radius"] / 1000,
                                 outer=outer, angle_deg=v["cluster_angle"], failed=v["failed"])
        cluster = MotorCluster.build(motor, **self.cluster_args)
        design = Design(
            rocket=self.editor.rocket,
            motor=cluster,
            motor_overhang=v["motor_overhang"] / 1000,
            launch=LaunchConfig(rod_length=v["rod_length"], rod_angle_deg=v["rod_angle"],
                                wind_speed=v["wind_speed"], turbulence=v["turbulence"] / 100,
                                temperature_c=v["temperature"], thrust_misalign_deg=v["misalign"], seed=v["seed"]),
            recovery=RecoveryConfig(v["chute_diameter"] / 1000, v["chute_cd"], v["delay"],
                                    shock_cord=v["shock_cord"] / 1000, inflation_time=v["inflation_time"]),
        )
        design.validate()
        return design

    # ------------------------------------------------------------------ 計算と表示
    def schedule_recalc(self):
        if self._after_id is not None:
            self.after_cancel(self._after_id)
        self._after_id = self.after(400, self.recalc)

    def set_status(self, text, error=False):
        self.status.set(text)
        self.status_label.configure(foreground=STATUS_COLORS["bad"] if error else "#333333")

    def recalc(self):
        self._after_id = None
        try:
            design = self.build_design()
            stab = analyze_stability(design)
        except (ValueError, ZeroDivisionError) as e:
            self.set_status(f"入力エラー: {e}", error=True)
            return
        self.design = design
        self.stab = stab
        self._update_cluster_hint(design.motor)
        self._update_stability(design, stab)
        self._update_aero(design, stab)
        self._update_motor(design)

        # 飛行計算は別スレッドで（画面を止めない）
        self._gen += 1
        gen = self._gen
        if self._future is not None:
            self._future.cancel()
        snapshot = copy.deepcopy(design)
        self._future = self.executor.submit(self._run_flight, snapshot)
        self.set_status(f"安定比 {stab.margin_min:.2f} cal / {stab.ratio_min:.1f} % ／ 飛行を計算中…")
        self.after(80, lambda: self._poll(self._future, gen, snapshot, stab))

    @staticmethod
    def _run_flight(design):
        res = simulate(design)
        return res, optimal_delay(design, res)

    def _poll(self, fut, gen, design, stab):
        if gen != self._gen:
            return
        if not fut.done():
            self.after(80, lambda: self._poll(fut, gen, design, stab))
            return
        try:
            res, opt = fut.result()
        except Exception as e:  # noqa: BLE001 計算失敗は画面に出す
            self.set_status(f"飛行計算でエラー: {e}", error=True)
            return
        self.optimal = opt
        self.flight = res
        self._update_flight(design, res, opt)
        self._update_recovery(design, res)
        self.view3d.set_flight(design, res)
        self.set_status(
            f"安定比 {stab.margin_min:.2f} cal / {stab.ratio_min:.1f} % ／ 到達高度 {res.apogee:.0f} m ／ "
            f"最高速度 {res.max_speed:.1f} m/s ／ 最大迎角 {res.max_alpha:.1f}° ／ 最適遅延 {fmt(opt, 1)} s")

    # ---- 機体図・安定性
    def _draw_view(self, ax, d: Design, s, view: str, top: float):
        """view = "side"（横から: z_b が上）または "top"（上から: y_b が上）。top は縦軸の範囲 [m]。"""
        rocket = d.rocket
        mm = 1000
        for fi, fin in enumerate(rocket.fins):
            le = rocket.fin_le(fin)
            R = rocket.fin_body_radius(fin)
            poly = fin.polygon()
            color = FIN_PALETTE[fi % len(FIN_PALETTE)]
            for phi in fin.angles():
                up, depth = (math.cos(phi), math.sin(phi)) if view == "side" else (math.sin(phi), -math.cos(phi))
                if abs(up) < 0.08:
                    continue
                pts = [((le + x) * mm, (R + y) * up * mm) for x, y in poly]
                behind = depth > 0.05
                ax.add_patch(mpatches.Polygon(pts, closed=True, fc=color, ec="#263238", lw=1,
                                              alpha=0.45 if behind else 0.9, zorder=1 if behind else 3))
        for ci, (comp, x0) in enumerate(rocket.layout()):
            prof = comp.profile()
            xs = [(x0 + x) * mm for x, _ in prof]
            rs = [r * mm for _, r in prof]
            ax.fill(xs + xs[::-1], rs + [-r for r in rs[::-1]], fc=PALETTE[ci % len(PALETTE)],
                    ec="#37474f", lw=1.0, zorder=2)
        x_end = d.nozzle_x * mm
        for mt in getattr(d.motor, "mounts", []):
            mm_ = mt.motor
            c = (mt.z if view == "side" else mt.y) * mm
            ax.add_patch(mpatches.Rectangle((x_end - mm_.length * mm, c - mm_.diameter / 2 * mm), mm_.length * mm,
                                            mm_.diameter * mm, fill=False, ls="--" if mt.ignited else ":",
                                            ec="#455a64" if mt.ignited else "#c62828", lw=1, zorder=4))
        for item in rocket.masses:
            ax.plot(item.position * mm, 0, marker="D", ms=6, color="#6d4c41", zorder=5, ls="")
        top *= mm
        cp = s.cp if view == "side" else s.cp_yaw
        ax.plot(s.launch.cg * mm, 0, marker="o", ms=13, mfc="white", mec="#1565c0", mew=2, zorder=6, ls="",
                label="CG（打上げ時）")
        ax.plot(s.launch.cg * mm, 0, marker="+", ms=13, color="#1565c0", mew=2, zorder=7)
        ax.plot(s.burnout.cg * mm, 0, marker="o", ms=8, mfc="none", mec="#64b5f6", mew=1.5, zorder=6, ls="",
                label="CG（燃焼後）")
        ax.plot(cp * mm, 0, marker="o", ms=11, color="#d32f2f", zorder=6, ls="", label="CP")
        if s.aero.cd_override is None:
            xd = s.aero.cd_and_center(50, 0.15)[1]
            ax.plot(xd * mm, 0, marker="^", ms=8, color="#7b1fa2", zorder=6, ls="", label="抗力中心")
        if rocket.masses:
            ax.plot([], [], marker="D", ms=6, color="#6d4c41", ls="", label="質量（おもり等）")
        ax.annotate(f"CG {s.launch.cg * mm:.0f}", (s.launch.cg * mm, 0), (s.launch.cg * mm, top * 1.12),
                    ha="center", color="#1565c0", fontsize=8,
                    arrowprops=dict(arrowstyle="-", color="#1565c0", lw=0.8))
        ax.annotate(f"CP {cp * mm:.0f}", (cp * mm, 0), (cp * mm, -top * 1.25), ha="center", color="#d32f2f",
                    fontsize=8, arrowprops=dict(arrowstyle="-", color="#d32f2f", lw=0.8))
        ax.set_aspect("equal")
        ax.set_xlim(-0.03 * x_end, x_end * 1.04)
        ax.set_ylim(-top * 1.35, top * 1.35)
        ax.grid(alpha=0.3)
        ax.tick_params(labelsize=8)
        title = "側面図（横から見た図）" if view == "side" else "平面図（上から見た図）"
        if not s.aero.symmetric:
            title += "  CP はピッチ面" if view == "side" else "  CP はヨー面"
        ax.set_title(title, fontsize=9, loc="left")

    def _update_stability(self, d: Design, s):
        rocket = d.rocket
        fig = self.stab_fig
        fig.clear()
        # 両方の図を同じ縮尺にするため、縦の範囲をそろえる
        top = max([rocket.max_radius] + [rocket.fin_body_radius(f) + f.geometry().span for f in rocket.fins])
        # 細長い機体は上下に、幅の広い機体（飛行機型・球体型）は左右に並べる
        if (d.nozzle_x) / (2 * top) > 2.5:
            ax1, ax2 = fig.subplots(2, 1, sharex=True)
        else:
            ax1, ax2 = fig.subplots(1, 2, sharey=True)
            ax1.set_xlabel("先端からの距離 [mm]")
        self._draw_view(ax1, d, s, "side", top)
        self._draw_view(ax2, d, s, "top", top)
        fig.legend(*ax1.get_legend_handles_labels(), loc="outside right upper", fontsize=7)
        ax2.set_xlabel("先端からの距離 [mm]")
        fig.suptitle(f"{rocket.name}　全長 {rocket.length * 1000:.0f} mm　最大径 {rocket.ref_diameter * 1000:.1f} mm",
                     fontsize=10)
        if s.aero.symmetric:
            text = f"安定比 {s.margin_launch:.2f} cal / {s.ratio(s.margin_launch):.1f} %"
        else:
            text = (f"安定比 ピッチ {s.margin_launch:.2f} cal / {s.ratio(s.margin_launch):.1f} %　"
                    f"ヨー {s.margin_launch_yaw:.2f} cal / {s.ratio(s.margin_launch_yaw):.1f} %")
        _, st = judge_ratio(s.ratio_min)
        fig.text(0.99, 0.01, text, ha="right", va="bottom", fontsize=11,
                 color=STATUS_COLORS.get(st, "#333333"),
                 bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=STATUS_COLORS.get(st, "#999999"), lw=1.2))
        self.stab_canvas.draw_idle()

        mm = 1000
        L, D = rocket.length, s.diameter
        rows = []
        planes = [("", s.margin_launch, s.margin_burnout)] if s.aero.symmetric else [
            ("・ピッチ", s.margin_launch, s.margin_burnout), ("・ヨー", s.margin_launch_yaw, s.margin_burnout_yaw)]
        for plane, m0, m1 in planes:
            for when, margin in (("打上げ時", m0), ("燃焼終了後", m1)):
                comment, st = judge_margin(margin, D, L)
                rows.append((f"安定比 cal（{when}{plane}）", fmt(margin, 2, "cal"), f"(CP − CG) ÷ 直径。{comment}", st))
            for when, margin in (("打上げ時", m0), ("燃焼終了後", m1)):
                ratio = s.ratio(margin)
                comment, st = judge_ratio(ratio)
                rows.append((f"安定比 %（{when}{plane}）", fmt(ratio, 1, "%"), f"(CP − CG) ÷ 全長。{comment}", st))
        cp_text = fmt(s.cp * mm, 1, "mm") if s.aero.symmetric else \
            f"ピッチ {s.cp * mm:.0f} / ヨー {s.cp_yaw * mm:.0f} mm"
        rows += [
            ("圧力中心 CP（先端から）", cp_text, f"CNα 合計 {s.aero.cn_alpha:.2f} /rad", ""),
            ("重心 CG（打上げ時）", fmt(s.launch.cg * mm, 1, "mm"), f"質量 {s.launch.mass * 1000:.1f} g", ""),
            ("重心 CG（燃焼終了後）", fmt(s.burnout.cg * mm, 1, "mm"), f"質量 {s.burnout.mass * 1000:.1f} g", ""),
        ]
        if s.aero.cd_override is None:
            xd = s.aero.cd_and_center(50, 0.15)[1]
            rows.append(("抗力中心（先端から）", fmt(xd * mm, 0, "mm"),
                         "CG より後ろなら抗力も安定に働く" if xd > s.launch.cg else "CG より前: 抗力が不安定側に働く",
                         "" if xd > s.launch.cg else "warn"))
        total_cn = s.aero.cn_alpha
        for p in s.parts:
            share = f"寄与 {p.cn_alpha / total_cn * 100:+.0f} %" if abs(total_cn) > 1e-9 else ""
            if p.cn_alpha_yaw is not None and abs(p.cn_alpha_yaw - p.cn_alpha) > 1e-9:
                share += f"（ヨー CNα {p.cn_alpha_yaw:+.2f}）"
            rows.append((f"　{p.name}", f"CNα {p.cn_alpha:+.2f} @ {p.x * mm:.0f} mm", share, ""))
        for c, x0 in rocket.layout():
            if c.BLUFF:
                rows.append((f"　{c.name}", "揚力なし（鈍頭物体）", f"形状抗力の中心 {(x0 + c.length / 2) * mm:.0f} mm", ""))
        rows.append(("横軸まわり慣性モーメント（打上げ時）", f"{s.launch.i_trans * 1e6:.0f} kg·mm²", "", ""))
        note = d.motor_fit_note()
        if note:
            rows.append(("モーターの取付", "", note, "warn"))
        if s.margin_min < 1 and L / D >= 5:
            rows.append(("改善のヒント", "", "CG を前に（ノーズにおもり）か、フィンを大きく・後ろに", "warn"))
        self.stab_table.set_rows(rows)

    # ---- 空力・質量
    def _update_aero(self, d: Design, s):
        aero = s.aero
        _, sound = atmosphere(0, d.launch.temperature_c)
        fig = self.aero_fig
        fig.clear()
        ax1, ax2 = fig.subplots(1, 2)
        v_top = max(150.0, self.flight.max_speed * 1.1) if self.flight is not None else 150.0
        vs = np.linspace(5, v_top, 60)
        bd = [aero.drag(v, v / sound) for v in vs]
        keys = list(bd[0])
        ax1.stackplot(vs, *[[b[k] for b in bd] for k in keys], labels=keys, alpha=0.85,
                      colors=["#90caf9", "#a5d6a7", "#ffcc80", "#ef9a9a", "#ce93d8", "#b0bec5"][:len(keys)])
        ax1.set_xlabel("対気速度 [m/s]")
        ax1.set_ylabel("抗力係数 Cd")
        ax1.set_title("抗力係数の内訳（モーター燃焼後）", fontsize=10)
        ax1.legend(fontsize=8, loc="upper right")
        ax1.grid(alpha=0.3)

        alphas = np.linspace(0, 30, 61)
        cns, cps = zip(*[aero.normal(math.radians(a)) for a in alphas])
        ax2.plot(alphas, np.array(cps) * 1000, color="#d32f2f", label="CP 位置")
        ax2.axhline(s.launch.cg * 1000, color="#1565c0", ls="--", lw=1, label="CG（打上げ時）")
        ax2.axhline(s.burnout.cg * 1000, color="#64b5f6", ls=":", lw=1, label="CG（燃焼後）")
        ax2.set_xlabel("迎角 [°]")
        ax2.set_ylabel("先端からの位置 [mm]")
        ax2.invert_yaxis()
        ax2.grid(alpha=0.3)
        ax2b = ax2.twinx()
        ax2b.plot(alphas, cns, color="#757575", lw=1)
        ax2b.set_ylabel("法線力係数 CN（灰）")
        ax2.legend(fontsize=8, loc="lower right")
        ax2.set_title("迎角による CP の移動（胴体揚力を含む）", fontsize=10)
        self.aero_canvas.draw_idle()

        v_ref = 50.0
        b = aero.drag(v_ref, v_ref / sound)
        total = sum(b.values())
        rows = [(k, f"{v:.3f}", f"{v / total * 100:.0f} %", "") for k, v in b.items()]
        rows.append((f"合計（{v_ref:.0f} m/s）", f"{total:.3f}", "", ""))
        self.cd_table.set_rows(rows)

        mrows = [(name, f"{mp.mass * 1000:.1f} g", f"{(mp.cg + off) * 1000:.0f} mm", "")
                 for name, mp, off in d.rocket.mass_breakdown()]
        mrows.append((f"モーター {d.motor.name}（打上げ時）", f"{d.motor.total_mass * 1000:.1f} g",
                      f"{d.motor_cg * 1000:.0f} mm", ""))
        note = "（実測値を使用）" if d.rocket.override_mass is not None or d.rocket.override_cg is not None else ""
        mrows.append((f"機体合計（モーター除く）{note}", f"{s.dry.mass * 1000:.1f} g", f"{s.dry.cg * 1000:.0f} mm", ""))
        self.mass_table.set_rows(mrows)

    # ---- 飛行解析
    def _update_flight(self, d: Design, r, opt):
        fig = self.flight_fig
        fig.clear()
        axs = fig.subplots(2, 4)
        t = r.t
        if not r.lifted:
            self.flight_canvas.draw_idle()
            self.flight_table.set_rows([("結果", "離陸できません", "推力不足（機体を軽くするか大きいモーターに）", "bad")])
            return
        marks = [(r.burnout_time, "燃焼終了", "#ef6c00"), (r.apogee_time, "頂点", "#2e7d32"),
                 (r.ejection_time, "放出", "#6a1b9a"), (r.chute_open_time, "全開", "#c2185b")]
        t_asc = (r.ejection_time if math.isfinite(r.ejection_time) else r.apogee_time) + 0.5
        asc = (t <= t_asc) & (r.phase != CHUTE) if math.isfinite(t_asc) else (r.phase != CHUTE)

        def deco(ax, ylabel, title=None):
            for tm, label, color in marks:
                if math.isfinite(tm) and tm <= ax.get_xlim()[1]:
                    ax.axvline(tm, color=color, ls=":", lw=1, label=label)
            ax.set_xlabel("時間 [s]", fontsize=8)
            ax.set_ylabel(ylabel, fontsize=8)
            ax.grid(alpha=0.3)
            ax.tick_params(labelsize=8)
            if title:
                ax.set_title(title, fontsize=9)

        axs[0, 0].plot(t, r.pos[:, 2], color="#1565c0")
        deco(axs[0, 0], "高度 [m]", "高度")
        axs[0, 0].legend(fontsize=7, loc="upper right")
        axs[0, 1].plot(t, r.speed, color="#c62828")
        deco(axs[0, 1], "速度 [m/s]", "速度")
        air = np.linalg.norm(r.vel - r.wind, axis=1)
        alpha = np.where(air > 3, r.alpha, np.nan)  # 低速時（発射台上など）の迎角は意味が薄いので隠す
        axs[0, 2].plot(t[asc], alpha[asc], color="#6a1b9a")
        deco(axs[0, 2], "迎角 [°]", "迎角（機軸と気流のなす角）")
        acc = r.accel
        ax = axs[1, 0]
        ax.plot(t[asc], acc[asc] / G, color="#ef6c00")
        if math.isfinite(r.rod_exit_time):
            ax.axvspan(0, r.rod_exit_time, color="#ffcc80", alpha=0.35, label="ロッド上")
            ax.axhline(r.rod_accel_avg / G, xmax=0.25, color="#e65100", ls="--", lw=1)
            ax.annotate(f"初期 {r.rod_accel_avg / G:.1f} G", (r.rod_exit_time, r.rod_accel_avg / G),
                        (6, 6), textcoords="offset points", fontsize=7, color="#e65100")
        powered = (t <= r.burnout_time) & asc
        if powered.any():
            k = int(np.argmax(np.where(powered, acc, -1)))
            ax.plot(t[k], acc[k] / G, "o", color="#bf360c", ms=4)
            ax.annotate(f"最大 {acc[k] / G:.1f} G", (t[k], acc[k] / G), (6, -2), textcoords="offset points",
                        fontsize=7, color="#bf360c")
        deco(ax, "加速度 [G]", "加速度（橙の帯 = ロッド上）")
        axs[1, 1].plot(t[asc], r.tilt[asc], color="#00838f")
        deco(axs[1, 1], "傾き [°]", "機軸の傾き（鉛直から）")
        ax = axs[1, 2]
        ax.plot(t[asc], np.degrees(np.hypot(r.omega[asc, 1], r.omega[asc, 2])), color="#5d4037", label="ピッチ/ヨー")
        ax.plot(t[asc], np.degrees(np.abs(r.omega[asc, 0])), color="#f9a825", label="ロール")
        deco(ax, "角速度 [°/s]", "回転の速さ")
        ax.legend(fontsize=7)
        ax = axs[0, 3]
        ax.plot(r.pos[:, 0], r.pos[:, 2], color="#2e7d32")
        ax.set_xlabel("風下 x [m]", fontsize=8)
        ax.set_ylabel("高度 [m]", fontsize=8)
        ax.set_title("経路（横から）", fontsize=9)
        ax.set_aspect("equal", adjustable="datalim")
        ax.grid(alpha=0.3)
        ax.tick_params(labelsize=8)
        ax = axs[1, 3]
        ax.plot(r.pos[:, 0], r.pos[:, 1], color="#2e7d32")
        ax.plot(0, 0, "o", color="#2e7d32", ms=5)
        if math.isfinite(r.landing_x):
            ax.plot(r.landing_x, r.landing_y, "x", color="#c62828", ms=8)
        ax.set_xlabel("風下 x [m]", fontsize=8)
        ax.set_ylabel("横 y [m]", fontsize=8)
        ax.set_title("経路（上から）", fontsize=9)
        ax.set_aspect("equal", adjustable="datalim")
        ax.grid(alpha=0.3)
        ax.tick_params(labelsize=8)
        self.flight_canvas.draw_idle()

        weight = (d.rocket.dry_mass_props().mass + d.motor.total_mass) * G
        tw = d.motor.average_thrust / weight
        rows = [
            ("最高到達高度", fmt(r.apogee, 1, "m"), f"頂点到達 {r.apogee_time:.2f} s", ""),
            ("最高速度", fmt(r.max_speed, 1, "m/s"), f"マッハ {r.max_mach:.2f}", "warn" if r.max_mach > 0.6 else ""),
            ("最大加速度（推進中）", fmt(r.max_accel_powered / G, 1, "G"), f"{r.max_accel_powered:.0f} m/s²", ""),
        ]
        a0 = r.rod_accel_avg
        st = "" if not math.isfinite(a0) else "ok" if a0 >= 4 * G else "warn" if a0 >= 2 * G else "bad"
        rows.append(("初期加速度（ランチロッド上の平均）", f"{fmt(a0 / G, 1, 'G')}（{fmt(a0, 0, 'm/s²')}）",
                     f"ロッド上の最大 {fmt(r.rod_accel_max / G, 1, 'G')}、離脱まで "
                     f"{fmt(r.rod_exit_time - r.liftoff_time, 2, 's')}（4 G 以上が目安）", st))
        m0 = (d.rocket.dry_mass_props().mass + d.motor.total_mass)
        rows.append(("推力から見た初期加速度（(推力 − 重量) ÷ 質量）",
                     f"最大推力で {(d.motor.max_thrust / m0 - G) / G:.1f} G / 平均推力で "
                     f"{(d.motor.average_thrust / m0 - G) / G:.1f} G",
                     f"離陸時の質量 {m0 * 1000:.1f} g（空気抵抗を除いた概算）", ""))
        st = "ok" if tw >= 5 else "warn" if tw >= 3 else "bad"
        rows.append(("推力重量比（平均推力 / 重量）", fmt(tw, 1), "5 以上を推奨", st))
        to_ratio = d.rocket.ref_diameter / d.rocket.length * 100
        flying = (r.phase != CHUTE) & (r.t <= (r.apogee_time if math.isfinite(r.apogee_time) else r.t[-1]))
        i_bo = min(int(np.searchsorted(r.t, r.burnout_time)), len(r.t) - 1)
        ratio_min = float(r.margin[flying].min()) * to_ratio if flying.any() else math.nan
        comment, st = judge_ratio(ratio_min)
        m_min = ratio_min / to_ratio
        rows.append(("安定比（打上げ時 → 燃焼終了）",
                     f"{r.margin[0]:.2f} → {r.margin[i_bo]:.2f} cal / "
                     f"{r.margin[0] * to_ratio:.1f} → {r.margin[i_bo] * to_ratio:.1f} %",
                     f"飛行中の最小 {m_min:.2f} cal / {ratio_min:.1f} %。{comment}", st))
        v = r.rod_exit_speed
        st = "ok" if v >= 15 else "warn" if v >= 10 else "bad"
        rows.append(("ランチロッド離脱速度", fmt(v, 1, "m/s"), "15 m/s 以上が目安（不足なら長いロッドを）", st))
        a = r.max_alpha
        st = "ok" if a < 10 else "warn" if a < 25 else "bad"
        rows.append(("最大迎角（上昇中・10 m/s 以上）", fmt(a, 1, "°"),
                     {"ok": "安定して飛行", "warn": "やや姿勢が乱れる", "bad": "姿勢が大きく乱れる・回転の恐れ"}[st], st))
        rows.append(("機軸の傾き（燃焼終了 / 頂点）", f"{fmt(r.tilt_burnout, 0, '°')} / {fmt(r.tilt_apogee, 0, '°')}",
                     "風に向かって傾くほど高度が下がる", ""))
        if r.max_roll_rate > 0.05:
            rows.append(("最大ロール速度", fmt(r.max_roll_rate, 1, "回転/s"), "フィン取付角による回転", ""))
        diff = d.recovery.ejection_delay - opt
        if not math.isfinite(opt):
            st, comment = "", ""
        elif abs(diff) <= 1:
            st, comment = "ok", "ほぼ頂点で放出"
        elif diff < 0:
            st, comment = "warn", f"頂点の {-diff:.1f} s 前に放出（上昇中）"
        else:
            st, comment = "warn", f"頂点の {diff:.1f} s 後に放出（落下中）"
        rows.append(("放出遅延 / 最適遅延", f"{d.recovery.ejection_delay:g} s / {fmt(opt, 1, 's')}", comment, st))
        v = r.ejection_speed
        st = "ok" if v < 10 else "warn" if v < 20 else "bad"
        rows.append(("放出時の速度 / 高度", f"{fmt(v, 1, 'm/s')} / {fmt(r.ejection_altitude, 0, 'm')}",
                     "速いとパラシュートが破損しやすい", st))
        if math.isfinite(r.separation_time):
            rows.append(("ノーズ分離 → 全開", f"{r.separation_time - r.ejection_time:.2f} s → "
                         f"{fmt(r.chute_open_time - r.separation_time, 2, 's')}",
                         f"放出 {r.ejection_time:.2f} s / 全開 {fmt(r.chute_open_time, 2, 's')}", ""))
            g = r.opening_shock
            force = g * G * (d.rocket.dry_mass_props().mass + d.motor.empty_mass)
            st = "ok" if g < 15 else "warn" if g < 30 else "bad"
            rows.append(("開傘衝撃", f"{fmt(g, 1, 'G')} / {fmt(force, 1, 'N')}",
                         {"ok": "問題なし", "warn": "ショックコードの強度に注意", "bad": "破損の恐れ（放出が早すぎ・速度が高い）"}[st],
                         st))
        rows.append(("着地までの時間 / 着地速度", f"{fmt(r.landing_time, 1, 's')} / {fmt(r.landing_speed, 1, 'm/s')}", "",
                     "bad" if r.landing_speed > 10 else ""))
        rows.append(("着地点（発射点から）", fmt(r.landing_distance, 0, "m"),
                     f"風下 {r.landing_x:.0f} m / 横 {r.landing_y:.0f} m" if math.isfinite(r.landing_x) else "", ""))
        for note in r.notes:
            rows.append(("注意", "", note, "warn"))
        self.flight_table.set_rows(rows)

    # ---- 回収
    def _update_recovery(self, d: Design, r):
        rho, _ = atmosphere(0, d.launch.temperature_c)
        mass = d.rocket.dry_mass_props().mass + d.motor.empty_mass
        cd = d.recovery.chute_cd
        dia = d.recovery.chute_diameter
        rate = descent_rate(mass, dia, cd, rho)
        try:
            target = float(self.target_rate.get())
            if target <= 0 or cd <= 0:
                raise ValueError
            self.required_dia = required_chute_diameter(mass, target, cd, rho)
            self.required_label.configure(text=f"直径 {self.required_dia * 1000:.0f} mm 以上（Cd={cd:g}）")
        except ValueError:
            self.required_dia = math.nan
            self.required_label.configure(text="目標降下速度と Cd に正の数値を入力")

        fig = self.rec_fig
        fig.clear()
        ax = fig.add_subplot()
        ds = np.linspace(0.1, 1.5, 200)
        if cd > 0:
            ax.plot(ds * 1000, [descent_rate(mass, x, cd, rho) for x in ds], color="#1565c0",
                    label=f"質量 {mass * 1000:.0f} g, Cd {cd:g}")
        ax.axhspan(3, 6, color="#2e7d32", alpha=0.1, label="推奨範囲 3〜6 m/s")
        if math.isfinite(rate) and dia > 0:
            ax.plot(dia * 1000, rate, "o", color="#d32f2f", ms=9, label=f"現在: {dia * 1000:.0f} mm → {rate:.1f} m/s")
        ax.set_ylim(0, 15)
        ax.set_xlabel("パラシュート直径 [mm]")
        ax.set_ylabel("降下速度 [m/s]")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=9)
        self.rec_canvas.draw_idle()

        if not math.isfinite(rate):
            rows = [("パラシュート", "なし", "弾道落下になります", "bad")]
        else:
            st = "bad" if rate > 8 else "warn" if rate > 6 or rate < 3 else "ok"
            comment = {"bad": "速すぎ：機体破損の恐れ", "warn": "やや速い" if rate > 6 else "遅い：風で流されやすい",
                       "ok": "適正"}[st]
            rows = [
                ("降下時の質量（燃焼後）", fmt(mass * 1000, 1, "g"), "", ""),
                ("降下速度（地上付近）", fmt(rate, 2, "m/s"), comment, st),
                ("着地時の運動エネルギー", fmt(0.5 * mass * rate ** 2, 2, "J"), "", ""),
                ("パラシュート面積", fmt(math.pi * dia ** 2 / 4 * 1e4, 0, "cm²"), "", ""),
            ]
        if r.lifted and math.isfinite(r.landing_x):
            rows.append(("着地点（6自由度計算・突風込み）", fmt(r.landing_distance, 0, "m"),
                         "広い発射場が必要" if r.landing_distance > 150 else "", "warn" if r.landing_distance > 150 else ""))
        self.rec_table.set_rows(rows)

    # ---- モーター
    def _update_motor(self, d: Design):
        m = d.motor
        fig = self.motor_fig
        fig.clear()
        ax = fig.add_subplot()
        if m.count > 1:
            kinds = {}
            for mt in m.mounts:
                if mt.ignited:
                    kinds[mt.motor.display_name] = mt.motor
            for k, mo in kinds.items():
                ax.plot(mo.times, mo.thrusts, lw=1, ls="--", label=f"{k}（1 本）")
        ax.plot(m.times, m.thrusts, color="#c62828", lw=1.8, label="合計" if m.count > 1 else None)
        ax.fill_between(m.times, m.thrusts, color="#c62828", alpha=0.12)
        ax.axhline(m.average_thrust, color="#555", ls="--", lw=1, label=f"平均推力 {m.average_thrust:.2f} N")
        ax.set_xlabel("時間 [s]")
        ax.set_ylabel("推力 [N]")
        ax.set_title(f"{m.display_name} 推力曲線", fontsize=10)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=9)
        self.motor_canvas.draw_idle()
        self.motor_table.set_rows([
            ("クラス / 全力積", f"{m.impulse_class} / {m.total_impulse:.2f} Ns", "", ""),
            ("最大推力 / 平均推力", f"{m.max_thrust:.2f} / {m.average_thrust:.2f} N", "", ""),
            ("燃焼時間", fmt(m.burn_time, 2, "s"), "", ""),
            ("本数 / 寸法", f"{m.count} 本 / 外径 φ{m.diameter * 1000:.1f} mm", "", ""),
            ("全質量 / 推進剤質量", f"{m.total_mass * 1000:.1f} / {m.prop_mass * 1000:.1f} g", "", ""),
        ] + [(f"　{k + 1} 本目: {mt.motor.display_name}",
              f"位置 y {mt.y * 1000:+.1f} / z {mt.z * 1000:+.1f} mm",
              "点火する" if mt.ignited else "点火しない（不具合の想定）", "" if mt.ignited else "warn")
             for k, mt in enumerate(m.mounts) if m.count > 1])

    def compare_motors(self):
        if self.design is None:
            return
        base = copy.deepcopy(self.design)
        motors = dict(self.motors)
        args = dict(self.cluster_args)
        self.cmp_table.set_rows([("計算中…", "", "", "", "", "", "", "", "", "", "", "")])

        def job():
            rows = []
            for name, single in motors.items():
                try:
                    motor = MotorCluster.build(single, **args)
                except ValueError:
                    motor = MotorCluster.build(single)
                name = motor.display_name
                values = [name, f"{motor.total_impulse:.1f} Ns", f"{motor.average_thrust:.1f} N"]
                d = replace(base, motor=motor)
                try:
                    d.validate()
                except ValueError:
                    rows.append((*values, "—", "—", "—", "—", "—", "—", motor.delays, "機体に入らない", "bad"))
                    continue
                st_m = analyze_stability(d)  # モーターの重さで重心が変わる
                ratio = st_m.ratio_min
                values.append(f"{st_m.margin_min:.2f} cal / {ratio:.1f} %")
                r = simulate(d, deploy=False, stop_at_apogee=True)
                if not r.lifted:
                    rows.append((*values, "—", "—", "—", "—", "—", motor.delays, "推力不足で離陸不可", "bad"))
                    continue
                opt = max(r.apogee_time - motor.burn_time, 0.0)
                problems = []
                if r.rod_exit_speed < 10:
                    problems.append("ロッド離脱速度不足")
                if r.max_alpha > 25:
                    problems.append("姿勢が乱れる")
                weight = (d.rocket.dry_mass_props().mass + motor.total_mass) * G
                if motor.average_thrust / weight < 3:
                    problems.append("推力重量比が低い")
                if motor.delay_list and min(abs(x - opt) for x in motor.delay_list) > 1.5:
                    problems.append("合う遅延秒数がない")
                if r.rod_accel_avg < 2 * G:
                    problems.append("初期加速度が小さい")
                if ratio < 5:
                    problems.append("安定比が小さい")
                status = "bad" if {"ロッド離脱速度不足", "姿勢が乱れる", "安定比が小さい"} & set(problems) else \
                    "warn" if problems else "ok"
                rows.append((*values, f"{r.rod_accel_avg / G:.1f} G", f"{r.apogee:.0f} m", f"{r.max_speed:.0f} m/s",
                             f"{r.rod_exit_speed:.1f} m/s",
                             f"{opt:.1f} s", motor.delays, "、".join(problems) or "OK", status))
            return rows

        fut = self.executor.submit(job)

        def poll():
            if fut.done():
                try:
                    self.cmp_table.set_rows(fut.result())
                except Exception as e:  # noqa: BLE001
                    self.set_status(f"比較計算でエラー: {e}", error=True)
            else:
                self.after(100, poll)
        poll()

    # ------------------------------------------------------------------ 操作
    def apply_optimal_delay(self):
        if self.design is None or not math.isfinite(self.optimal):
            return
        delays = self.design.motor.delay_list
        best = min(delays, key=lambda x: abs(x - self.optimal)) if delays else round(self.optimal, 1)
        self.vars["delay"].set(f"{best:g}")
        self.set_status(f"最適値 {self.optimal:.1f} s に最も近い遅延 {best:g} s を設定しました")

    def apply_required_diameter(self):
        if math.isfinite(getattr(self, "required_dia", math.nan)):
            self.vars["chute_diameter"].set(f"{math.ceil(self.required_dia * 1000):d}")

    def load_template(self, fn):
        self.editor.set_rocket(fn())
        self.current_file = None
        self.title(APP_TITLE)
        self.recalc()

    def load_eng(self):
        path = filedialog.askopenfilename(title="モーターデータを開く",
                                          filetypes=[("RASP エンジンファイル", "*.eng"), ("すべて", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                motors = parse_eng(f.read())
        except (OSError, ValueError) as e:
            messagebox.showerror("読み込みエラー", str(e))
            return
        for m in motors:
            self._add_motor(m)
        self.vars["motor"].set(motors[0].display_name)
        self.set_status(f"{len(motors)} 個のモーターを読み込みました: " + ", ".join(m.display_name for m in motors))

    def _add_motor(self, motor: Motor):
        self.motors[motor.display_name] = motor
        self.custom_motors = [m for m in self.custom_motors if m.display_name != motor.display_name] + [motor]
        self.motor_combo.configure(values=list(self.motors))
        self.outer_combo.configure(values=[SAME_MOTOR] + list(self.motors))

    def show_manual(self, section: str | None = None):
        """マニュアルを開く（開いていれば前面に出して、指定の章を表示）。"""
        if self.manual is not None and self.manual.winfo_exists():
            if section:
                titles = [t for t, _ in MANUAL_SECTIONS]
                self.manual.show(titles.index(section))
            self.manual.deiconify()
            self.manual.lift()
            self.manual.focus_force()
            return
        self.manual = ManualWindow(self, section)

    def show_version(self):
        messagebox.showinfo("バージョン情報", (
            f"{APP_NAME}\nバージョン {__version__}\n\n"
            "機体の形・安定性・6 自由度の飛行・回収を計算するツールです。\n"
            "使い方は「ヘルプ → マニュアル」（F1）をご覧ください。\n\n"
            "計算結果は目安です。実際の打ち上げは安全規則に従ってください。"), parent=self)

    def show_about(self):
        self.show_manual("計算方法")

    # ------------------------------------------------------------------ 保存と読み込み
    def save_design(self, ask=False):
        path = self.current_file
        if ask or not path:
            path = filedialog.asksaveasfilename(title="設計を保存", defaultextension=".json",
                                                filetypes=[("ロケット設計", "*.json")])
            if not path:
                return
        data = {
            "version": 2,
            "rocket": self.editor.rocket.to_dict(),
            "flight": {k: v.get() for k, v in self.vars.items()},
            "custom_motors": [m.to_eng() for m in self.custom_motors],
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            messagebox.showerror("保存エラー", str(e))
            return
        self.current_file = path
        self.title(f"{APP_TITLE} - {path}")
        self.set_status(f"保存しました: {path}")

    def open_design(self):
        path = filedialog.askopenfilename(title="設計を開く", filetypes=[("ロケット設計", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != 2:
                raise ValueError("このバージョンでは開けない形式です")
            rocket = Rocket.from_dict(data["rocket"])
            for text in data.get("custom_motors", []):
                for m in parse_eng(text):
                    self._add_motor(m)
        except (OSError, ValueError, KeyError, TypeError) as e:
            messagebox.showerror("読み込みエラー", str(e))
            return
        flight = data.get("flight", {})
        if flight.get("motor") and flight["motor"] not in self.motors:
            messagebox.showwarning("モーター", f"モーター「{flight['motor']}」が見つからないため既定値を使います")
            flight.pop("motor")
        for key, value in flight.items():
            if key in self.vars:
                self.vars[key].set(value)
        self.editor.set_rocket(rocket)
        self.current_file = path
        self.title(f"{APP_TITLE} - {path}")
        self.recalc()

    def _on_close(self):
        self.view3d.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def main():
    app = RocketDesignApp()
    app.mainloop()
