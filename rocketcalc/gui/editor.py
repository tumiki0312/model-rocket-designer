"""機体構成エディタ: 部品ツリーとプロパティ入力欄。"""
from __future__ import annotations

import copy
import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..components import (FIN_PROFILES, FIN_REFS, FIN_SHAPES, MATERIALS, NOSE_SHAPES, BodyTube, Ellipsoid, FinSet,
                          FreeformBody, MassItem, NoseCone, Rocket, Transition)

# (属性, ラベル, 単位, 種類, 追加情報)
#  種類: str / int / float / float_opt / len / len_opt / mass / mass_opt / deg / um / choice / material / points
_MAT = ("material", "材質", "", "material", None)
_THK = ("thickness", "肉厚", "mm", "len", None)
_MASS = ("mass_override", "質量（空欄＝材質から推定）", "g", "mass_opt", None)

SPECS = {
    NoseCone: [
        ("name", "名前", "", "str", None),
        ("shape", "形状", "", "choice", NOSE_SHAPES),
        ("shape_param", "形状パラメータ", "", "float", None),
        ("length", "長さ", "mm", "len", None),
        ("diameter", "直径（後端）", "mm", "len", None),
        _MAT, _THK, _MASS,
    ],
    BodyTube: [
        ("name", "名前", "", "str", None),
        ("length", "長さ", "mm", "len", None),
        ("diameter", "外径", "mm", "len", None),
        _MAT, _THK, _MASS,
    ],
    Transition: [
        ("name", "名前", "", "str", None),
        ("length", "長さ", "mm", "len", None),
        ("fore_diameter", "前端直径", "mm", "len", None),
        ("aft_diameter", "後端直径", "mm", "len", None),
        _MAT, _THK, _MASS,
    ],
    Ellipsoid: [
        ("name", "名前", "", "str", None),
        ("diameter", "直径", "mm", "len", None),
        ("length", "長さ（直径と同じで球）", "mm", "len", None),
        _MAT, _THK, _MASS,
    ],
    FreeformBody: [
        ("name", "名前", "", "str", None),
        ("points", "外形（x, 直径）", "mm", "points",
         "1 行に「x, 直径」を mm で。先端から順に並べる。\n直径 0 から始めるとノーズになる。同じ x を 2 行書くと段差。"),
        _MAT, _THK, _MASS,
    ],
    FinSet: [
        ("name", "名前", "", "str", None),
        ("count", "枚数", "枚", "int", None),
        ("shape", "平面形", "", "choice", FIN_SHAPES),
        ("root_chord", "根元翼弦長", "mm", "len", None),
        ("tip_chord", "翼端翼弦長", "mm", "len", None),
        ("span", "翼幅（スパン）", "mm", "len", None),
        ("sweep", "前縁後退距離", "mm", "len", None),
        ("points", "外形（x, y）", "mm", "points",
         "1 行に「x, y」を mm で。根元前縁 (0, 0) から外周を回り、\n根元後縁（根元翼弦長, 0）で終わる。y は胴体表面からの高さ。"),
        ("thickness", "厚さ", "mm", "len", None),
        ("profile", "断面", "", "choice", FIN_PROFILES),
        ("cant", "取付角（キャント）", "°", "deg", None),
        ("incidence", "取付迎角（揚力）", "°", "deg", None),
        ("position_ref", "位置の基準", "", "choice", FIN_REFS),
        ("position", "位置", "mm", "len", None),
        ("roll_offset", "1 枚目の向き", "°", "deg", None),
        ("material", "材質", "", "material", None),
        ("mass_override", "質量（全枚数・空欄＝推定）", "g", "mass_opt", None),
    ],
    MassItem: [
        ("name", "名前", "", "str", None),
        ("mass", "質量", "g", "mass", None),
        ("position", "位置（先端から）", "mm", "len", None),
    ],
    Rocket: [
        ("name", "機体名", "", "str", None),
        ("override_mass", "実測質量（モーター除く）", "g", "mass_opt", None),
        ("override_cg", "実測重心（モーター除く・先端から）", "mm", "len_opt", None),
        ("cd_override", "Cd を手動指定（空欄＝形状から推定）", "", "float_opt", None),
        ("roughness", "表面粗さ", "µm", "um", None),
        ("extra_cd", "追加 Cd（ランチラグ等）", "", "float", None),
    ],
}

HINTS = {
    (NoseCone, "shape_param"): lambda c: ("C: 0 = フォン・カルマン, 0.333 = LV-Haack" if c.shape == "haack"
                                          else "指数 n: 0.5 前後（小さいほど先が丸い）"),
    (FinSet, "cant"): lambda c: "0 以外にすると機体が回転（スピン）します",
    (FinSet, "roll_offset"): lambda c: "0° = 真上、90° = 右。飛行機の主翼は 2 枚・90°、垂直尾翼は 1 枚・0°",
    (FinSet, "incidence"): lambda c: "機体の「上」（0° 方向）へ揚力を出す角度。飛行機型の主翼用",
    (Ellipsoid, "length"): lambda c: "球や楕円体は鈍頭物体として計算します（揚力なし・形状抗力）",
    (Rocket, "override_mass"): lambda c: "実測値がある場合に入力。部品の質量より優先されます",
    (Rocket, "cd_override"): lambda c: "通常は空欄（形状から自動推定）",
}


def visible(comp, attr) -> bool:
    if isinstance(comp, NoseCone) and attr == "shape_param":
        return comp.shape in ("haack", "power")
    if isinstance(comp, FinSet):
        shape_attrs = {"trapezoid": {"root_chord", "tip_chord", "span", "sweep"},
                       "elliptical": {"root_chord", "span"}, "freeform": {"points"}}
        all_shape = {"root_chord", "tip_chord", "span", "sweep", "points"}
        if attr in all_shape:
            return attr in shape_attrs[comp.shape]
    return True


def to_text(value, kind) -> str:
    if value is None:
        return ""
    if kind in ("len", "len_opt", "mass", "mass_opt"):
        return f"{value * 1000:.6g}"
    if kind == "um":
        return f"{value * 1e6:.6g}"
    if kind == "points":
        return "\n".join(f"{x * 1000:g}, {y * 1000:g}" for x, y in value)
    if kind in ("float", "float_opt", "deg"):
        return f"{value:.6g}"
    return str(value)


def from_text(text: str, kind, label):
    text = text.strip()
    if kind == "str":
        return text
    if kind.endswith("_opt") and text == "":
        return None
    if kind == "points":
        pts = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.replace("，", ",").replace(",", " ").split()
            if len(parts) != 2:
                raise ValueError(f"「{label}」の各行は「x, y」の 2 つの数値にしてください")
            pts.append([float(parts[0]) / 1000, float(parts[1]) / 1000])
        return pts
    try:
        num = int(text) if kind == "int" else float(text)
    except ValueError:
        raise ValueError(f"「{label}」に数値を入力してください") from None
    if kind in ("len", "len_opt", "mass", "mass_opt"):
        return num / 1000
    if kind == "um":
        return num / 1e6
    return num


def summary(comp) -> str:
    mm = 1000
    if isinstance(comp, NoseCone):
        return f"{NOSE_SHAPES[comp.shape]} L{comp.length * mm:g} φ{comp.diameter * mm:g}"
    if isinstance(comp, BodyTube):
        return f"L{comp.length * mm:g} φ{comp.diameter * mm:g}"
    if isinstance(comp, Transition):
        return f"L{comp.length * mm:g} φ{comp.fore_diameter * mm:g}→{comp.aft_diameter * mm:g}"
    if isinstance(comp, Ellipsoid):
        kind = "球" if abs(comp.length - comp.diameter) < 1e-9 else "楕円体"
        return f"{kind} φ{comp.diameter * mm:g} L{comp.length * mm:g}"
    if isinstance(comp, FreeformBody):
        return f"L{comp.length * mm:g} ({len(comp.points)} 点)"
    if isinstance(comp, FinSet):
        cant = f" キャント{comp.cant:g}°" if comp.cant else ""
        inc = f" 迎角{comp.incidence:g}°" if comp.incidence else ""
        return f"{comp.count} 枚 {FIN_SHAPES[comp.shape]}{cant}{inc}"
    if isinstance(comp, MassItem):
        return f"{comp.mass * 1000:g} g @ {comp.position * mm:g} mm"
    return ""


class ComponentEditor(ttk.Frame):
    def __init__(self, parent, on_change: Callable[[], None], on_status: Callable[[str, bool], None]):
        super().__init__(parent)
        self.on_change = on_change
        self.on_status = on_status
        self.rocket = Rocket()
        self._selected = None
        self._text_after = None

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 4))
        add = ttk.Menubutton(bar, text="＋ 部品を追加")
        menu = tk.Menu(add, tearoff=False)
        for cls in (NoseCone, BodyTube, Transition, Ellipsoid, FreeformBody, FinSet, MassItem):
            menu.add_command(label=cls.LABEL, command=lambda c=cls: self.add(c))
        add["menu"] = menu
        add.pack(side="left")
        ttk.Button(bar, text="複製", width=5, command=self.duplicate).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="削除", width=5, command=self.delete).pack(side="left", padx=2)
        ttk.Button(bar, text="▲", width=3, command=lambda: self.move(-1)).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="▼", width=3, command=lambda: self.move(1)).pack(side="left", padx=2)

        self.tree = ttk.Treeview(self, columns=("info",), height=9, selectmode="browse")
        self.tree.heading("#0", text="部品")
        self.tree.heading("info", text="寸法")
        self.tree.column("#0", width=170)
        self.tree.column("info", width=170)
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        ttk.Label(self, text="機体は先端から順に積み重なります（▲▼で順序変更）", style="Hint.TLabel").pack(anchor="w")

        self.form = ttk.LabelFrame(self, text="プロパティ", padding=(8, 4))
        self.form.pack(fill="both", expand=True, pady=(6, 0))

    # ---------------------------------------------------------------- ツリー
    def set_rocket(self, rocket: Rocket):
        self.rocket = rocket
        self.refresh_tree()
        self.tree.selection_set("rocket")

    def _lists(self):
        return {"body": self.rocket.body, "fins": self.rocket.fins, "masses": self.rocket.masses}

    def _comp_for(self, iid):
        if iid == "rocket":
            return self.rocket
        group, idx = iid.split(":")
        return self._lists()[group][int(idx)]

    def refresh_tree(self, keep=None):
        keep = keep or (self.tree.selection()[0] if self.tree.selection() else "rocket")
        self.tree.delete(*self.tree.get_children())
        self.tree.insert("", "end", iid="rocket", text=f"🚀 {self.rocket.name}",
                         values=(f"全長 {self.rocket.length * 1000:.0f} mm",), open=True)
        icons = {"body": "▭", "fins": "◣", "masses": "●"}
        for group, items in self._lists().items():
            for i, comp in enumerate(items):
                self.tree.insert("", "end", iid=f"{group}:{i}", text=f"{icons[group]} {comp.name}",
                                 values=(summary(comp),))
        if self.tree.exists(keep):
            self.tree.selection_set(keep)
            self.tree.see(keep)

    def _update_row(self):
        iid = self._selected
        if not iid or not self.tree.exists(iid):
            return
        comp = self._comp_for(iid)
        if iid == "rocket":
            self.tree.item(iid, text=f"🚀 {comp.name}", values=(f"全長 {comp.length * 1000:.0f} mm",))
        else:
            icon = self.tree.item(iid, "text").split(" ", 1)[0]
            self.tree.item(iid, text=f"{icon} {comp.name}", values=(summary(comp),))
            self.tree.item("rocket", values=(f"全長 {self.rocket.length * 1000:.0f} mm",))

    def add(self, cls):
        comp = cls()
        if cls in (FinSet,):
            group = "fins"
        elif cls is MassItem:
            group = "masses"
        else:
            group = "body"
            # 直前の部品の後端径に合わせる
            sel = self._selected
            idx = len(self.rocket.body)
            if sel and sel.startswith("body:"):
                idx = int(sel.split(":")[1]) + 1
            if idx > 0:
                d = 2 * self.rocket.body[idx - 1].aft_radius
                for attr in ("diameter", "fore_diameter"):
                    if hasattr(comp, attr):
                        setattr(comp, attr, d)
                if isinstance(comp, FreeformBody):
                    comp.points = [[0.0, d], [0.03, d * 1.4], [0.06, d * 1.4], [0.09, d]]
            self.rocket.body.insert(idx, comp)
            self.refresh_tree(keep=f"body:{idx}")
            self.on_change()
            return
        lst = self._lists()[group]
        lst.append(comp)
        self.refresh_tree(keep=f"{group}:{len(lst) - 1}")
        self.on_change()

    def duplicate(self):
        sel = self._selected
        if not sel or sel == "rocket":
            return
        group, idx = sel.split(":")
        lst = self._lists()[group]
        new = copy.deepcopy(lst[int(idx)])
        new.name += "（コピー）"
        lst.insert(int(idx) + 1, new)
        self.refresh_tree(keep=f"{group}:{int(idx) + 1}")
        self.on_change()

    def delete(self):
        sel = self._selected
        if not sel or sel == "rocket":
            return
        group, idx = sel.split(":")
        lst = self._lists()[group]
        if group == "body" and len(lst) == 1:
            self.on_status("軸方向の部品は 1 つ以上必要です", True)
            return
        lst.pop(int(idx))
        keep = f"{group}:{min(int(idx), len(lst) - 1)}" if lst else "rocket"
        self.refresh_tree(keep=keep)
        self.on_change()

    def move(self, delta):
        sel = self._selected
        if not sel or sel == "rocket":
            return
        group, idx = sel.split(":")
        i = int(idx)
        lst = self._lists()[group]
        j = i + delta
        if not 0 <= j < len(lst):
            return
        lst[i], lst[j] = lst[j], lst[i]
        self.refresh_tree(keep=f"{group}:{j}")
        self.on_change()

    # ---------------------------------------------------------------- プロパティ欄
    def _on_select(self):
        sel = self.tree.selection()
        self._selected = sel[0] if sel else None
        self.build_form()

    def build_form(self):
        for w in self.form.winfo_children():
            w.destroy()
        if not self._selected:
            return
        comp = self._comp_for(self._selected)
        self.form.configure(text=f"プロパティ：{getattr(comp, 'LABEL', '機体全体')}")
        self.form.columnconfigure(1, weight=1)
        row = 0
        for attr, label, unit, kind, extra in SPECS[type(comp)]:
            if not visible(comp, attr):
                continue
            ttk.Label(self.form, text=label).grid(row=row, column=0, sticky="nw", pady=2)
            value = getattr(comp, attr)
            if kind in ("choice", "material"):
                options = extra if kind == "choice" else {k: k for k in MATERIALS}
                labels = list(options.values())
                var = tk.StringVar(value=options.get(value, value))
                w = ttk.Combobox(self.form, textvariable=var, values=labels, state="readonly", width=18)
                w.bind("<<ComboboxSelected>>", lambda e, a=attr, v=var, o=options, c=comp:
                       self._apply_choice(c, a, v.get(), o))
                w.grid(row=row, column=1, columnspan=2, sticky="ew", padx=4, pady=2)
            elif kind == "points":
                row += 1
                txt = tk.Text(self.form, height=7, width=24, font=("Consolas", 10), undo=True)
                txt.insert("1.0", to_text(value, kind))
                txt.grid(row=row, column=0, columnspan=3, sticky="ew", pady=2)
                txt.bind("<KeyRelease>", lambda e, a=attr, t=txt, c=comp, k=kind, lb=label:
                         self._schedule_text(c, a, t, k, lb))
                row += 1
                ttk.Label(self.form, text=extra, style="Hint.TLabel", justify="left").grid(
                    row=row, column=0, columnspan=3, sticky="w")
            else:
                var = tk.StringVar(value=to_text(value, kind))
                w = ttk.Entry(self.form, textvariable=var, width=12, justify="left" if kind == "str" else "right")
                w.grid(row=row, column=1, sticky="ew", padx=4, pady=2)
                ttk.Label(self.form, text=unit, width=4).grid(row=row, column=2, sticky="w")
                var.trace_add("write", lambda *_, a=attr, v=var, c=comp, k=kind, lb=label, ww=w:
                              self._apply(c, a, v.get(), k, lb, ww))
            row += 1
            hint = HINTS.get((type(comp), attr))
            if hint:
                ttk.Label(self.form, text=hint(comp), style="Hint.TLabel", wraplength=320).grid(
                    row=row, column=0, columnspan=3, sticky="w", pady=(0, 3))
                row += 1

    def _apply(self, comp, attr, text, kind, label, widget=None):
        try:
            value = from_text(text, kind, label)
        except ValueError as e:
            if widget is not None:
                widget.configure(style="Bad.TEntry")
            self.on_status(f"入力エラー: {e}", True)
            return
        if widget is not None:
            widget.configure(style="TEntry")
        setattr(comp, attr, value)
        self._update_row()
        self.on_change()

    def _apply_choice(self, comp, attr, label, options):
        key = next((k for k, v in options.items() if v == label), label)
        if isinstance(comp, FinSet) and attr == "shape" and key == "freeform" and comp.shape != "freeform":
            comp.points = [[round(x, 5), round(y, 5)] for x, y in comp.polygon()]
        setattr(comp, attr, key)
        self._update_row()
        self.build_form()  # 表示する項目が変わることがある
        self.on_change()

    def _schedule_text(self, comp, attr, txt, kind, label):
        if self._text_after:
            self.after_cancel(self._text_after)
        self._text_after = self.after(500, lambda: self._apply(comp, attr, txt.get("1.0", "end"), kind, label))
