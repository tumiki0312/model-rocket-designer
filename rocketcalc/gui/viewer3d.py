"""飛行の 3D 再生ビュー（軌道全体 + 機体の拡大ビュー）。

回収時は「放出の煙 → ノーズ分離（ショックコードが伸びる）→ パラシュートが引き出されて開く →
機体が吊り下がる」までを表示する。
"""
from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import ttk

import numpy as np
from matplotlib.colors import to_rgb
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

from ..components import FreeformBody, NoseCone, Rocket
from ..design import Design
from ..flight import CHUTE, PHASE_NAMES, FlightResult, deploy_label, quat_from_axis, rotmat
from .widgets import FIN_PALETTE, PALETTE, make_figure

LIGHT = np.array([0.4, -0.5, 0.75]) / np.linalg.norm([0.4, -0.5, 0.75])
UP = np.array([0.0, 0.0, 1.0])
SCALES = {"自動": None, "実寸 ×1": 1, "×5": 5, "×10": 10, "×20": 20, "×50": 50, "×100": 100}
SPEEDS = {"×0.1": 0.1, "×0.25": 0.25, "×0.5": 0.5, "×1（実時間）": 1.0, "×2": 2.0, "×5": 5.0}
CHUTE_COLORS = [(0.86, 0.16, 0.16, 0.9), (0.97, 0.97, 0.97, 0.9)]
CORD_COLOR = (0.36, 0.25, 0.22, 1.0)
SMOKE_TIME = 0.7


def _revolve(pts, n_sides):
    th = np.linspace(0, 2 * np.pi, n_sides + 1)
    c, s = np.cos(th), np.sin(th)
    quads = []
    for (xa, ra), (xb, rb) in zip(pts, pts[1:]):
        for k in range(n_sides):
            quads.append([(xa, ra * s[k], ra * c[k]), (xb, rb * s[k], rb * c[k]),
                          (xb, rb * s[k + 1], rb * c[k + 1]), (xa, ra * s[k + 1], ra * c[k + 1])])
    return quads


def build_mesh(rocket: Rocket, n_sides: int = 12, max_prof: int = 14) -> dict:
    """先端基準の機体座標 (s=先端からの距離, y, z) のメッシュ。分離できるノーズは別に持つ。"""
    layout = rocket.layout()
    first = layout[0][0]
    separable = (len(layout) > 1 and isinstance(first, (NoseCone, FreeformBody))
                 and first.fore_radius < 1e-9)
    groups = {"body": ([], []), "nose": ([], [])}
    prev = None
    for ci, (comp, x0) in enumerate(layout):
        prof = comp.profile()
        if len(prof) > max_prof:
            idx = sorted(set(np.linspace(0, len(prof) - 1, max_prof).round().astype(int)))
            prof = [prof[i] for i in idx]
        pts = [(x0 + x, r) for x, r in prof]
        if prev is not None:
            pts = [prev] + pts  # 前の部品との段差をつなぐ
        quads = _revolve(pts, n_sides)
        if ci == 0 and separable:
            # ノーズの後端（肩）をふさぐ
            quads += _revolve([pts[-1], (pts[-1][0], 0.0)], n_sides)
        g = groups["nose" if ci == 0 and separable else "body"]
        g[0].extend(quads)
        g[1].extend([to_rgb(PALETTE[ci % len(PALETTE)])] * len(quads))
        prev = pts[-1]
    polys, pcolors = [], []
    L, r_aft = rocket.length, rocket.aft_radius
    if r_aft > 0:
        th = np.linspace(0, 2 * np.pi, n_sides + 1)[:-1]
        polys.append(np.array([(L, r_aft * math.sin(a), r_aft * math.cos(a)) for a in th]))
        pcolors.append(to_rgb("#455a64"))
    for fi, fin in enumerate(rocket.fins):
        le = rocket.fin_le(fin)
        R = rocket.fin_body_radius(fin)
        poly = fin.polygon()
        for phi in fin.angles():
            ey, ez = math.sin(phi), math.cos(phi)
            polys.append(np.array([(le + x, (R + y) * ey, (R + y) * ez) for x, y in poly]))
            pcolors.append(to_rgb(FIN_PALETTE[fi % len(FIN_PALETTE)]))
    return {
        "body": (np.array(groups["body"][0], dtype=float), np.array(groups["body"][1])),
        "nose": (np.array(groups["nose"][0], dtype=float), np.array(groups["nose"][1])) if separable else None,
        "polys": (polys, np.array(pcolors)),
        "split": first.length if separable else 0.0,
    }


def _to_world(arr, R, pos, scale, cg):
    b = np.array(arr, dtype=float)
    b[..., 0] = cg - b[..., 0]
    return pos + scale * (b @ R.T)


def _shade(verts, base, two_sided=False):
    if len(verts) == 0:
        return np.zeros((0, 3))
    if isinstance(verts, np.ndarray):
        n = np.cross(verts[:, 1] - verts[:, 0], verts[:, -1] - verts[:, 0])
    else:
        n = np.array([np.cross(p[1] - p[0], p[-1] - p[0]) for p in verts]).reshape(-1, 3)
    norm = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, norm, out=np.zeros_like(n), where=norm > 1e-15)
    d = n @ LIGHT
    d = np.abs(d) if two_sided else np.clip(d, 0, 1)
    return np.clip(base * (0.45 + 0.55 * d)[:, None], 0, 1)


def _slerp(q0, q1, u):
    q0, q1 = np.asarray(q0, float), np.asarray(q1, float)
    dot = float(q0 @ q1)
    if dot < 0:
        q1, dot = -q1, -dot
    if dot > 0.9995:
        q = q0 + u * (q1 - q0)
        return q / np.linalg.norm(q)
    th = math.acos(dot)
    return (math.sin((1 - u) * th) * q0 + math.sin(u * th) * q1) / math.sin(th)


def _smooth(u):
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def _frame(axis):
    """axis を第 1 軸とする正規直交基底（列ベクトル）。"""
    a = axis / np.linalg.norm(axis)
    p = np.cross(a, [0, 0, 1] if abs(a[2]) < 0.9 else [1, 0, 0])
    p /= np.linalg.norm(p)
    return a, p, np.cross(a, p)


def _shift(parts, off):
    """組み立てた形を平行移動する。"""
    parts["body"] = (parts["body"][0] + off, parts["body"][1])
    parts["nose"] = (parts["nose"][0] + off, parts["nose"][1])
    parts["fins"] = ([p + off for p in parts["fins"][0]], parts["fins"][1])
    verts, face, edge = parts["extra"]
    parts["extra"] = ([p + off for p in verts], face, edge)
    parts["lines"] = [p + off for p in parts["lines"]]


class _Blitter:
    """背景（軸・軌跡）を保存し、動く物だけを描き直して高速に更新する。"""

    def __init__(self, canvas, artists):
        self.canvas = canvas
        self.artists = artists
        self.bg = None
        for a in artists:
            a.set_animated(True)
        self.cid = canvas.mpl_connect("draw_event", self._on_draw)

    def disconnect(self):
        self.canvas.mpl_disconnect(self.cid)

    def _on_draw(self, _event):
        self.bg = self.canvas.copy_from_bbox(self.canvas.figure.bbox)
        self._draw_artists()

    def _draw_artists(self):
        for a in self.artists:
            if hasattr(a, "do_3d_projection"):
                a.do_3d_projection()
            a.axes.draw_artist(a)

    def update(self):
        if self.bg is None:
            self.canvas.draw_idle()
            return
        self.canvas.restore_region(self.bg)
        self._draw_artists()
        self.canvas.blit(self.canvas.figure.bbox)


class _Scene:
    """1 つの 3D 軸に描く機体一式（胴体・ノーズ・フィン・炎/パラシュート/煙・ひも）。"""

    def __init__(self, ax, fin_edge=0.3):
        self.body = Poly3DCollection([], linewidths=0)
        self.nose = Poly3DCollection([], linewidths=0)
        self.fins = Poly3DCollection([], linewidths=fin_edge, edgecolors="#263238")
        self.extra = Poly3DCollection([], linewidths=0.4)
        self.lines = Poly3DCollection([], linewidths=0.9, facecolors="none")
        for c in self.artists:
            ax.add_collection3d(c)

    @property
    def artists(self):
        return [self.body, self.nose, self.fins, self.extra, self.lines]

    def set(self, parts):
        for name in ("body", "nose", "fins"):
            verts, colors = parts[name]
            coll = getattr(self, name)
            coll.set_verts(verts)
            coll.set_facecolor(colors if len(verts) else "none")
        verts, face, edge = parts["extra"]
        self.extra.set_verts(verts)
        self.extra.set_facecolor(face if verts else "none")
        self.extra.set_edgecolor(edge if verts else "none")
        self.lines.set_verts(parts["lines"])
        self.lines.set_edgecolor(CORD_COLOR if parts["lines"] else "none")


class Flight3DView(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.result: FlightResult | None = None
        self.design: Design | None = None
        self.playing = False
        self.t_play = 0.0
        self._after = None
        self._updating = False
        self._blit_main = self._blit_close = None
        self._lims_main = self._lims_close = None

        ctrl = ttk.Frame(self)
        ctrl.pack(side="bottom", fill="x", pady=(4, 0))
        self.play_btn = ttk.Button(ctrl, text="▶ 再生", width=8, command=self.toggle_play)
        self.play_btn.pack(side="left")
        ttk.Button(ctrl, text="⏮", width=3, command=lambda: self.jump(0.0)).pack(side="left", padx=2)
        self.slider_var = tk.DoubleVar(value=0.0)
        self.slider = ttk.Scale(ctrl, from_=0, to=1, variable=self.slider_var, command=self._on_slider)
        self.slider.pack(side="left", fill="x", expand=True, padx=6)
        self.time_label = ttk.Label(ctrl, text="t = 0.00 s", width=11)
        self.time_label.pack(side="left")
        ttk.Label(ctrl, text="速度").pack(side="left", padx=(8, 2))
        self.speed_var = tk.StringVar(value="×0.25")
        ttk.Combobox(ctrl, textvariable=self.speed_var, values=list(SPEEDS), width=10, state="readonly").pack(side="left")
        ttk.Label(ctrl, text="機体の拡大").pack(side="left", padx=(8, 2))
        self.scale_var = tk.StringVar(value="自動")
        cb = ttk.Combobox(ctrl, textvariable=self.scale_var, values=list(SCALES), width=8, state="readonly")
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.show_time(self.t_play))
        self.follow_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctrl, text="カメラ追従", variable=self.follow_var,
                        command=lambda: self.show_time(self.t_play)).pack(side="left", padx=8)

        events = ttk.Frame(self)
        events.pack(side="bottom", fill="x")
        ttk.Label(events, text="ジャンプ:").pack(side="left")
        self.event_buttons = {}
        for key, label in [("liftoff_time", "動き出し"), ("rod_exit_time", "ロッド離脱"), ("burnout_time", "燃焼終了"),
                           ("apogee_time", "頂点"), ("ejection_time", "放出"), ("separation_time", "ノーズ分離"),
                           ("chute_open_time", "パラシュート全開"), ("landing_time", "着地")]:
            b = ttk.Button(events, text=label, command=lambda k=key: self.jump_event(k))
            b.pack(side="left", padx=2)
            self.event_buttons[key] = b
        ttk.Label(events, text="（放出の前から ×0.1 で再生すると開傘の様子がよく見えます）",
                  style="Hint.TLabel").pack(side="left", padx=6)

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=3)
        body.add(right, weight=2)

        self.fig_main, self.canvas_main, _ = make_figure(left, (6, 5), toolbar=False, layout=None)
        self.canvas_main.get_tk_widget().pack(fill="both", expand=True)
        ttk.Label(left, text="ドラッグで視点を回転できます", style="Hint.TLabel").pack(anchor="w")
        self.fig_close, self.canvas_close, _ = make_figure(right, (4, 4), toolbar=False, layout=None)
        self.fig_close.subplots_adjust(top=0.88)  # 2 行のタイトルが切れないように
        self.canvas_close.get_tk_widget().pack(fill="both", expand=True)

        info = ttk.LabelFrame(right, text="現在の状態", padding=(8, 4))
        info.pack(fill="x")
        self.info_vars = {}
        names = ["フェーズ", "高度", "対気速度", "加速度", "迎角", "機軸の傾き（鉛直から）", "ロール角 / 速度", "推力",
                 "安定マージン / 安定比"]
        for i, name in enumerate(names):
            ttk.Label(info, text=name).grid(row=i // 2, column=(i % 2) * 2, sticky="w", padx=(0, 4))
            v = tk.StringVar(value="—")
            ttk.Label(info, textvariable=v, width=18, style="Value.TLabel").grid(
                row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(0, 12))
            self.info_vars[name] = v

        self.ax_main = self.fig_main.add_subplot(projection="3d")
        self.ax_close = self.fig_close.add_subplot(projection="3d")
        self._message("飛行計算の完了を待っています…")

    def _message(self, text):
        for b in (self._blit_main, self._blit_close):
            if b is not None:
                b.disconnect()
        self._blit_main = self._blit_close = None
        for ax in (self.ax_main, self.ax_close):
            ax.cla()
            ax.set_axis_off()
        self.ax_main.text2D(0.5, 0.5, text, transform=self.ax_main.transAxes, ha="center")
        self.canvas_main.draw_idle()
        self.canvas_close.draw_idle()

    # ------------------------------------------------------------------ データ設定
    def set_flight(self, design: Design, result: FlightResult):
        self.stop()
        self.design, self.result = design, result
        if not result.lifted or len(result.t) < 2:
            self._message("離陸できないため表示できません")
            return
        rocket = design.rocket
        self.L = rocket.length
        self.cg = design.mass_props(design.motor.total_mass).cg
        self.mesh_main = build_mesh(rocket, n_sides=8, max_prof=8)
        self.mesh_close = build_mesh(rocket, n_sides=16, max_prof=16)
        self.max_thrust = max(design.motor.max_thrust, 1e-9)
        self.acc = result.accel
        self.dref = rocket.ref_diameter
        self.chute_r = max(design.recovery.chute_diameter / 2, 0.01)
        self.cord = max(design.recovery.shock_cord, 0.05)
        chute_idx = np.nonzero(result.phase == CHUTE)[0]
        self.i_eject = int(chute_idx[0]) if len(chute_idx) else None
        self.slider.configure(to=float(result.t[-1]))
        for key, b in self.event_buttons.items():
            b.state(["!disabled"] if math.isfinite(getattr(result, key)) else ["disabled"])

        pos = result.pos
        lo, hi = pos.min(axis=0), pos.max(axis=0)
        span = max((hi - lo).max(), 1.0)
        pad = 0.08 * span
        lo, hi = lo - pad, hi + pad
        lo[2] = 0.0
        for k in range(2):  # 横方向が薄くなりすぎないように
            if hi[k] - lo[k] < 0.3 * span:
                c = (hi[k] + lo[k]) / 2
                lo[k], hi[k] = c - 0.15 * span, c + 0.15 * span
        self.lims = (lo, hi)
        self.span = span

        ax = self.ax_main
        ax.cla()
        ax.plot(*pos.T, color="#b0bec5", lw=1)
        ax.plot(pos[:, 0], pos[:, 1], 0 * pos[:, 2], color="#cfd8dc", lw=0.8, ls=":")
        ax.scatter([0], [0], [0], color="#2e7d32", s=30, label="発射点")
        if math.isfinite(result.landing_x):
            ax.scatter([result.landing_x], [result.landing_y], [0], color="#c62828", marker="x", s=40, label="着地点")
        self.trail, = ax.plot([], [], [], color="#1565c0", lw=1.8)
        self.drop, = ax.plot([], [], [], color="#90a4ae", lw=0.8, ls="--")
        self.scene_main = _Scene(ax)
        ax.set_xlabel("風下方向 x [m]")
        ax.set_ylabel("横方向 y [m]")
        ax.set_zlabel("高度 [m]")
        ax.legend(loc="upper left", fontsize=8)

        axc = self.ax_close
        axc.cla()
        self.scene_close = _Scene(axc, fin_edge=0.4)
        self.vert_line, = axc.plot([], [], [], color="#9e9e9e", ls=":", lw=1)
        axc.computed_zorder = False  # 線を機体より手前に描く
        self.stream_lines = [axc.plot([], [], [], color="#1e88e5", lw=1.4, alpha=0.8, zorder=10)[0] for _ in range(4)]
        self.stream_heads = [axc.plot([], [], [], color="#1e88e5", marker="o", ms=3.5, ls="", zorder=10)[0]
                             for _ in range(4)]
        self.vel_line, = axc.plot([], [], [], color="#43a047", lw=1.5, ls="--", zorder=10)
        axc.set_title("機体の姿勢（拡大）\n青: 機体に当たる気流（●が下流）　緑破線: 進行方向", fontsize=8)
        axc.set_xlabel("x")
        axc.set_ylabel("y")
        axc.set_zlabel("z")
        axc.view_init(elev=12, azim=-60)
        for b in (self._blit_main, self._blit_close):
            if b is not None:
                b.disconnect()
        self._blit_main = _Blitter(self.canvas_main, [self.trail, self.drop, *self.scene_main.artists])
        self._blit_close = _Blitter(self.canvas_close, [*self.scene_close.artists, *self.stream_lines,
                                                        *self.stream_heads, self.vel_line])
        self._lims_main = self._lims_close = None
        self.show_time(0.0)

    # ------------------------------------------------------------------ 形の組み立て
    def _scale(self):
        s = SCALES.get(self.scale_var.get())
        return s if s else max(1.0, 0.12 * self.span / self.L)

    def _flames(self, t, thrust):
        """噴射炎（先端基準座標）。クラスターは 1 本ずつ、その推力に応じた長さで描く。"""
        d = self.design
        x0 = d.nozzle_x
        mounts = getattr(d.motor, "mounts", None)
        if mounts:
            items = [(mt.motor.diameter / 2, mt.y, mt.z, f) for mt, f in zip(mounts, d.motor.mount_thrusts(t))]
            ref = max(mt.motor.max_thrust for mt in mounts)
        else:
            items = [(d.motor.diameter / 2, 0.0, 0.0, thrust)]
            ref = self.max_thrust
        n = 8
        out = []
        for r, y0, z0, f in items:
            if f <= 0:
                continue
            ln = self.L * 0.6 * f / max(ref, 1e-9)
            out += [np.array([(x0, y0 + r * math.sin(2 * math.pi * k / n), z0 + r * math.cos(2 * math.pi * k / n)),
                              (x0, y0 + r * math.sin(2 * math.pi * (k + 1) / n),
                               z0 + r * math.cos(2 * math.pi * (k + 1) / n)),
                              (x0 + ln, y0, z0)]) for k in range(n)]
        return out

    @staticmethod
    def _canopy(conf, axis, radius, height, shroud, n=12):
        """パラシュート: 合流点 conf から axis 方向に shroud 離れた位置に傘。"""
        a, p1, p2 = _frame(axis)
        rim_c = conf + a * shroud
        fr = [1.0, 0.85, 0.5, 0.0]     # 半径の比
        fh = [0.0, 0.45, 0.85, 1.0]    # 高さの比
        rings = []
        for rr, hh in zip(fr, fh):
            rings.append([rim_c + a * height * hh + radius * rr * (p1 * math.cos(2 * math.pi * k / n)
                                                                   + p2 * math.sin(2 * math.pi * k / n))
                          for k in range(n + 1)])
        gores, colors = [], []
        for j in range(len(rings) - 1):
            for k in range(n):
                gores.append(np.array([rings[j][k], rings[j][k + 1], rings[j + 1][k + 1], rings[j + 1][k]]))
                colors.append(CHUTE_COLORS[k % 2])
        shrouds = [np.array([conf, rings[0][k], conf]) for k in range(0, n, 2)]
        return gores, colors, shrouds

    @staticmethod
    def _smoke(center, radius, alpha, n=7):
        blobs = []
        for k in range(n):
            off = np.array([math.cos(k * 2.4), math.sin(k * 2.4), math.cos(k * 1.7)]) * radius * 0.9
            c = center + off
            _, p1, p2 = _frame(np.array([math.sin(k), math.cos(k), 0.5]))
            blobs.append(np.array([c + radius * 0.6 * (p1 * math.cos(2 * math.pi * j / 8) + p2 * math.sin(2 * math.pi * j / 8))
                                   for j in range(8)]))
        return blobs, [(0.6, 0.6, 0.6, alpha)] * n

    def _assemble(self, mesh, i, t, pos, scale):
        """時刻 t（記録の i 番目）の機体・回収系の形を世界座標で作る。"""
        r = self.result
        phase = int(r.phase[i])
        thrust = float(r.thrust[i])
        d_state = float(r.deploy[i]) if phase == CHUTE else 0.0
        body_q, body_c = mesh["body"]
        polys, pcol = mesh["polys"]
        extra, eface, eedge, lines = [], [], [], []
        nose_v, nose_c = np.zeros((0, 4, 3)), np.zeros((0, 3))

        if phase == CHUTE and self.i_eject is not None:
            q_ej = r.quat[self.i_eject]
            q_hang = np.array(quat_from_axis((0.0, 0.0, 1.0)))
            q = _slerp(q_ej, q_hang, _smooth(d_state / 2))
            R = np.array(rotmat(*q))
            u0 = np.array(rotmat(*q_ej))[:, 0]
        else:
            R = np.array(rotmat(*r.quat[i]))
            u0 = R[:, 0]

        body = _to_world(body_q, R, pos, scale, self.cg)
        fins = [_to_world(p, R, pos, scale, self.cg) for p in polys]
        if thrust > 0:
            for f in self._flames(t, thrust):
                extra.append(_to_world(f, R, pos, scale, self.cg))
                eface.append((1.0, 0.55, 0.1, 0.85))
                eedge.append((1.0, 0.55, 0.1, 0.85))

        split = mesh["split"]
        attach = _to_world(np.array([[split, 0.0, 0.0]]), R, pos, scale, self.cg)[0]
        conf = attach
        if phase != CHUTE:
            if mesh["nose"] is not None:
                nose_v = _to_world(mesh["nose"][0], R, pos, scale, self.cg)
                nose_c = mesh["nose"][1]
        else:
            # ---- 回収: 分離 → 開傘 → 吊り下がり
            ext = min(d_state, 1.0)
            w = _smooth((d_state - 0.6) / 1.4)
            uc = (1 - w) * u0 + w * UP
            uc = uc / np.linalg.norm(uc) if np.linalg.norm(uc) > 1e-6 else UP
            cord = self.cord * scale
            conf = attach + uc * cord * ext
            if mesh["nose"] is not None:
                n_len = split
                base = attach + uc * cord * ext * 0.55
                Rn = np.array(rotmat(*quat_from_axis(tuple(uc))))
                nq = np.array(mesh["nose"][0], dtype=float)
                nq[..., 0] = n_len - nq[..., 0]  # 先端が uc 方向（パラシュート側）を向く
                nose_v = base + scale * (nq @ Rn.T)
                nose_c = mesh["nose"][1]
                lines += [np.array([attach, base, attach]), np.array([base + uc * n_len * scale, conf,
                                                                      base + uc * n_len * scale])]
            else:
                lines.append(np.array([attach, conf, attach]))
            rc = self.chute_r * scale
            if d_state < 1:  # 袋に入ったまま引き出される
                radius, height, shroud = 0.12 * rc, 0.5 * rc, 0.05 * rc
            else:
                o = _smooth(d_state - 1)
                radius = rc * (0.12 + 0.88 * o)
                height = rc * (1.0 - 0.5 * o)
                shroud = rc * (0.2 + 0.9 * o)
            gores, gcol, shrouds = self._canopy(conf, uc, radius, height, shroud)
            extra += gores
            eface += gcol
            eedge += [(0.5, 0.1, 0.1, 0.6)] * len(gores)
            lines += shrouds
            tau = t - float(r.ejection_time)
            if 0 <= tau < SMOKE_TIME:
                blobs, bcol = self._smoke(attach + u0 * cord * 0.15 * tau / SMOKE_TIME,
                                          (0.04 + 0.25 * tau / SMOKE_TIME) * max(self.L, 0.2) * scale,
                                          0.55 * (1 - tau / SMOKE_TIME))
                extra += blobs
                eface += bcol
                eedge += [(0, 0, 0, 0)] * len(blobs)

        center = 0.5 * (attach + conf) if phase == CHUTE else pos
        return {
            "center": center,
            "body": (body, _shade(body, body_c)),
            "nose": (nose_v, _shade(nose_v, nose_c) if len(nose_v) else nose_c),
            "fins": (fins, _shade(fins, pcol, two_sided=True)),
            "extra": (extra, eface, eedge),
            "lines": lines,
            "R": R,
            "phase": phase,
            "deploy": d_state,
        }

    # ------------------------------------------------------------------ 描画
    def show_time(self, t):
        r = self.result
        if r is None or not r.lifted or len(r.t) < 2:
            return
        self.t_play = t
        i = min(int(np.searchsorted(r.t, t)), len(r.t) - 1)
        pos = r.pos[i]
        scale = self._scale()

        # --- 軌道全体ビュー
        self.trail.set_data_3d(r.pos[:i + 1, 0], r.pos[:i + 1, 1], r.pos[:i + 1, 2])
        self.drop.set_data_3d([pos[0], pos[0]], [pos[1], pos[1]], [pos[2], 0])
        parts = self._assemble(self.mesh_main, i, t, pos, scale)
        self.scene_main.set(parts)
        phase, R = parts["phase"], parts["R"]
        ax = self.ax_main
        if self.follow_var.get():
            half = max(self.L * scale * 2.0, 5.0)
            if phase == CHUTE:
                half = max(half, (self.cord + 2 * self.chute_r) * scale * 1.3)
            lo, hi = pos - half, pos + half
            if lo[2] < 0:
                hi[2] -= lo[2]
                lo[2] = 0.0
        else:
            lo, hi = self.lims
        lims = (tuple(np.round(lo, 6)), tuple(np.round(hi, 6)))
        main_full = lims != self._lims_main
        if main_full:
            self._lims_main = lims
            ax.set_xlim(lo[0], hi[0])
            ax.set_ylim(lo[1], hi[1])
            ax.set_zlim(lo[2], hi[2])
            ax.set_box_aspect(tuple(np.maximum(hi - lo, 1e-6)))

        # --- 拡大ビュー（機体の重心が原点。降下中は機体とパラシュートの中間が原点）
        cparts = self._assemble(self.mesh_close, i, t, np.zeros(3), 1.0)
        if phase == CHUTE:
            _shift(cparts, -cparts["center"])
        self.scene_close.set(cparts)
        if phase == CHUTE:
            half = (0.5 * self.L + self.cord + 2.2 * self.chute_r) * 0.6
        else:
            half = self.L * 0.75
        axc = self.ax_close
        close_full = half != self._lims_close
        if close_full:
            self._lims_close = half
            axc.set_xlim(-half, half)
            axc.set_ylim(-half, half)
            axc.set_zlim(-half, half)
            axc.set_box_aspect((1, 1, 1))
            self.vert_line.set_data_3d([0, 0], [0, 0], [-half, half])
        v_air = r.vel[i] - r.wind[i]
        sp = float(np.linalg.norm(v_air))
        if sp > 0.5 and phase != CHUTE:
            u = v_air / sp
            _, p1, p2 = _frame(u)
            for k, (line, head) in enumerate(zip(self.stream_lines, self.stream_heads)):
                ang = math.pi / 4 + k * math.pi / 2
                off = (p1 * math.cos(ang) + p2 * math.sin(ang)) * half * 0.45
                a, b = off + u * half * 0.9, off - u * half * 0.9
                line.set_data_3d([a[0], b[0]], [a[1], b[1]], [a[2], b[2]])
                head.set_data_3d([b[0]], [b[1]], [b[2]])
        else:
            for line, head in zip(self.stream_lines, self.stream_heads):
                line.set_data_3d([], [], [])
                head.set_data_3d([], [], [])
        vg = r.vel[i]
        vs = float(np.linalg.norm(vg))
        if vs > 0.5:
            u = vg / vs * half * 0.9
            self.vel_line.set_data_3d([0, u[0]], [0, u[1]], [0, u[2]])
        else:
            self.vel_line.set_data_3d([], [], [])

        # --- 状態表示
        axis = R[:, 0]
        tilt = math.degrees(math.acos(max(-1.0, min(1.0, axis[2]))))
        flying = phase != CHUTE
        iv = self.info_vars
        iv["フェーズ"].set(deploy_label(parts["deploy"]) if not flying else PHASE_NAMES.get(phase, ""))
        iv["高度"].set(f"{pos[2]:.1f} m")
        iv["対気速度"].set(f"{sp:.1f} m/s")
        iv["加速度"].set(f"{self.acc[i] / 9.80665:.1f} G（{self.acc[i]:.0f} m/s²）")
        iv["迎角"].set(f"{r.alpha[i]:.1f}°" if flying else "—")
        iv["機軸の傾き（鉛直から）"].set(f"{tilt:.1f}°" if flying else "—")
        iv["ロール角 / 速度"].set(f"{r.roll[i] % 360:.0f}° / {r.omega[i, 0] / (2 * math.pi):.1f} rps")
        iv["推力"].set(f"{float(r.thrust[i]):.1f} N")
        iv["安定マージン / 安定比"].set(f"{r.margin[i]:.2f} cal / {r.margin[i] * self.dref / self.L * 100:.1f} %")
        self.time_label.configure(text=f"t = {t:.2f} s")
        self._updating = True
        self.slider_var.set(t)
        self._updating = False
        if main_full:
            self.canvas_main.draw_idle()
        else:
            self._blit_main.update()
        if close_full:
            self.canvas_close.draw_idle()
        else:
            self._blit_close.update()

    # ------------------------------------------------------------------ 再生操作
    def toggle_play(self):
        if self.playing:
            self.stop()
            return
        if self.result is None or not self.result.lifted:
            return
        if self.t_play >= self.result.t[-1] - 1e-6:
            self.t_play = 0.0
        self.playing = True
        self.play_btn.configure(text="⏸ 停止")
        self._last = time.perf_counter()
        self._tick()

    def stop(self):
        self.playing = False
        self.play_btn.configure(text="▶ 再生")
        if self._after:
            self.after_cancel(self._after)
            self._after = None

    def _tick(self):
        if not self.playing:
            return
        now = time.perf_counter()
        t = self.t_play + (now - self._last) * SPEEDS.get(self.speed_var.get(), 1.0)
        self._last = now
        if t >= self.result.t[-1]:
            t = float(self.result.t[-1])
            self.show_time(t)
            self.stop()
            return
        self.show_time(t)
        self._after = self.after(30, self._tick)

    def jump(self, t):
        self.show_time(t)

    def jump_event(self, key):
        if self.result is not None:
            t = getattr(self.result, key)
            if math.isfinite(t):
                if key == "ejection_time":
                    t -= 0.05  # 放出の直前から見られるように
                self.show_time(t)

    def _on_slider(self, value):
        # 変数をコードから変えた時にも呼ばれるので、現在時刻と同じなら無視（無限ループ防止）
        if self._updating or self.result is None or abs(float(value) - self.t_play) < 1e-6:
            return
        if self.playing:
            self.stop()
        self.show_time(float(value))
