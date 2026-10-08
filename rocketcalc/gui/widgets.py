"""GUI 共通の部品。"""
from __future__ import annotations

import math
import sys
import tkinter as tk
import warnings
from pathlib import Path
from tkinter import ttk

import matplotlib

matplotlib.use("TkAgg")
from matplotlib import font_manager, rcParams  # noqa: E402
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

_JP_FONTS = ["Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP", "IPAexGothic", "Hiragino Sans"]
_available = {f.name for f in font_manager.fontManager.ttflist}
rcParams["font.family"] = [f for f in _JP_FONTS if f in _available] + ["DejaVu Sans"]
rcParams["axes.unicode_minus"] = False
# 非表示タブの図はサイズ 0 で描画されることがあり、その時の警告は無視する
warnings.filterwarnings("ignore", message="constrained_layout not applied")

STATUS_COLORS = {"ok": "#1a7f37", "warn": "#b35900", "bad": "#c62828"}
STATUS_MARK = {"ok": "✔", "warn": "△", "bad": "✖"}
# 部品ごとの色（機体図・3D 表示で共通）
PALETTE = ["#e57373", "#cfd8dc", "#ffb74d", "#81c784", "#64b5f6", "#ba68c8", "#4db6ac", "#f06292"]
FIN_PALETTE = ["#546e7a", "#8d6e63", "#5c6bc0", "#26a69a"]


def resource_path(*parts: str) -> Path:
    """同梱ファイルの場所。PyInstaller で .exe にした時は展開先（sys._MEIPASS）を見る。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base.joinpath(*parts)


def set_window_icon(win) -> None:
    ico = resource_path("assets", "rocket.ico")
    if ico.exists():
        try:
            win.iconbitmap(default=str(ico)) if isinstance(win, tk.Tk) else win.iconbitmap(str(ico))
        except tk.TclError:
            pass


def fit_geometry(win, width: int, height: int) -> str:
    """画面の拡大率に合わせ、画面からはみ出さない大きさにする。"""
    scale = max(win.winfo_fpixels("1i") / 96.0, 1.0)
    w = min(int(width * scale), int(win.winfo_screenwidth() * 0.95))
    h = min(int(height * scale), int(win.winfo_screenheight() * 0.88))
    return f"{w}x{h}"


def fmt(value, digits=1, unit=""):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    return f"{value:,.{digits}f} {unit}".strip()


def make_figure(parent, figsize, toolbar=True, layout="constrained"):
    fig = Figure(figsize=figsize, dpi=96, layout=layout)
    if layout is None:
        fig.subplots_adjust(left=0, right=1, bottom=0, top=0.94)
    canvas = FigureCanvasTkAgg(fig, master=parent)
    tb = None
    if toolbar:
        tb = NavigationToolbar2Tk(canvas, parent, pack_toolbar=False)
        tb.update()
    return fig, canvas, tb


class ScrollableFrame(ttk.Frame):
    def __init__(self, parent, width=360):
        super().__init__(parent)
        bg = ttk.Style().lookup("TFrame", "background")
        self.canvas = tk.Canvas(self, highlightthickness=0, width=width, background=bg)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, padding=(6, 6))
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_wheel(self, event):
        if self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


class ResultTable(ttk.Frame):
    """行 = (列1, 列2, ..., 状態)。状態 ok/warn/bad で色と記号が付く（最後の列に）。"""

    def __init__(self, parent, columns=("項目", "値", "判定"), widths=(230, 160, 300), height=10):
        super().__init__(parent)
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=height)
        for col, w in zip(columns, widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor="w", stretch=True)
        for status, color in STATUS_COLORS.items():
            self.tree.tag_configure(status, foreground=color)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def set_rows(self, rows):
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            *values, status = row
            values = list(values)
            if STATUS_MARK.get(status) and values[-1]:
                values[-1] = f"{STATUS_MARK[status]} {values[-1]}"
            self.tree.insert("", "end", values=values, tags=(status,) if status else ())
