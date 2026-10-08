"""機体を構成する部品と、その形状・質量の計算。単位は SI (m, kg)。

機体は「軸方向の部品」（ノーズ・チューブ・トランジション・自由形状）を先端から順に
積み重ねたものとし、そこにフィンセットと質量（おもり等）を取り付ける。
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields
from typing import ClassVar

# 材質の密度 [kg/m^3]
MATERIALS = {
    "紙管（クラフト）": 750,
    "バルサ": 160,
    "ベニヤ（航空合板）": 630,
    "PLA（3Dプリント）": 1240,
    "ABS / スチレン": 1050,
    "ガラス繊維（GFRP）": 1850,
    "カーボン（CFRP）": 1600,
    "アルミニウム": 2700,
    "発泡スチロール": 30,
}

NOSE_SHAPES = {
    "ogive": "オジャイブ",
    "cone": "コーン（円錐）",
    "parabolic": "放物線",
    "elliptical": "楕円",
    "haack": "ハーク（フォン・カルマン等）",
    "power": "べき乗",
}
FIN_SHAPES = {"trapezoid": "台形", "elliptical": "楕円", "freeform": "自由形状"}
FIN_PROFILES = {"square": "四角（平板）", "rounded": "前縁を丸める", "airfoil": "翼型"}
FIN_REFS = {"aft": "機体後端から（後縁まで）", "front": "先端から（前縁まで）"}


@dataclass
class MassProps:
    mass: float
    cg: float        # 基準位置からの重心 [m]
    i_axial: float   # 機軸まわりの慣性モーメント [kg m^2]
    i_trans: float   # 重心を通る横軸まわりの慣性モーメント [kg m^2]


def combine_mass(items: list[tuple[MassProps, float]]) -> MassProps:
    """(質量特性, 基準位置のずれ) のリストを合成する。"""
    m = sum(p.mass for p, _ in items)
    if m <= 0:
        return MassProps(0.0, 0.0, 0.0, 0.0)
    cg = sum(p.mass * (p.cg + off) for p, off in items) / m
    i_ax = sum(p.i_axial for p, _ in items)
    i_tr = sum(p.i_trans + p.mass * (p.cg + off - cg) ** 2 for p, off in items)
    return MassProps(m, cg, i_ax, i_tr)


def _segments(prof):
    return zip(prof, prof[1:])


def _shell_mass(prof, density, thickness, override, length) -> MassProps:
    """回転体の外皮を薄肉シェルとみなした質量特性。override があれば質量だけ置き換える。"""
    m = mx = i_ax = i_tr0 = 0.0
    for (x0, r0), (x1, r1) in _segments(prof):
        ds = math.hypot(x1 - x0, r1 - r0)
        rm, xm = (r0 + r1) / 2, (x0 + x1) / 2
        dm = density * thickness * 2 * math.pi * rm * ds
        m += dm
        mx += dm * xm
        i_ax += dm * rm * rm
        i_tr0 += dm * (rm * rm / 2 + xm * xm)
    if m > 0:
        cg = mx / m
        props = MassProps(m, cg, i_ax, i_tr0 - m * cg * cg)
    else:
        r = max(r for _, r in prof)
        props = MassProps(1e-12, length / 2, 1e-12 * r * r, 1e-12 * length ** 2 / 12)
    if override is not None:
        k = override / props.mass
        props = MassProps(override, props.cg, props.i_axial * k, props.i_trans * k)
    return props


# ----------------------------------------------------------------------------- 軸方向の部品
@dataclass
class _Axial:
    TYPE: ClassVar[str] = ""
    LABEL: ClassVar[str] = ""
    BLUFF: ClassVar[bool] = False  # 鈍頭物体（球など）は細長物体理論を使わない

    def profile(self) -> list[tuple[float, float]]:
        """(先端からの局所位置 x, 半径) のリスト。"""
        raise NotImplementedError

    @property
    def fore_radius(self) -> float:
        return self.profile()[0][1]

    @property
    def aft_radius(self) -> float:
        return self.profile()[-1][1]

    def mass_props(self) -> MassProps:
        return _shell_mass(self.profile(), MATERIALS.get(self.material, 1000), self.thickness,
                           self.mass_override, self.length)

    def validate(self) -> None:
        if self.length <= 0:
            raise ValueError(f"「{self.name}」の長さは正の値にしてください")
        if any(r < 0 for _, r in self.profile()):
            raise ValueError(f"「{self.name}」の直径が負になっています")
        if self.thickness < 0 or (self.mass_override is not None and self.mass_override <= 0):
            raise ValueError(f"「{self.name}」の肉厚・質量が不正です")


@dataclass
class NoseCone(_Axial):
    TYPE: ClassVar[str] = "nose"
    LABEL: ClassVar[str] = "ノーズコーン"
    name: str = "ノーズコーン"
    shape: str = "ogive"
    shape_param: float = 0.5   # べき乗: 指数 n / ハーク: C（0 = フォン・カルマン）
    length: float = 0.070
    diameter: float = 0.025
    material: str = "PLA（3Dプリント）"
    thickness: float = 0.0012
    mass_override: float | None = None

    def profile(self, n: int = 40):
        L, R = self.length, self.diameter / 2
        pts = []
        for i in range(n + 1):
            u = 0.5 * (1 - math.cos(math.pi * i / n))  # 両端を細かく
            x = L * u
            if self.shape == "cone":
                r = R * u
            elif self.shape == "ogive":
                rho = (R * R + L * L) / (2 * R)
                r = math.sqrt(max(rho * rho - (L - x) ** 2, 0.0)) + R - rho
            elif self.shape == "parabolic":
                r = R * (2 * u - u * u)
            elif self.shape == "elliptical":
                r = R * math.sqrt(max(1 - (1 - u) ** 2, 0.0))
            elif self.shape == "haack":
                th = math.acos(max(-1.0, min(1.0, 1 - 2 * u)))
                r = R / math.sqrt(math.pi) * math.sqrt(max(
                    th - math.sin(2 * th) / 2 + self.shape_param * math.sin(th) ** 3, 0.0))
            else:  # power
                r = R * u ** max(self.shape_param, 0.05)
            pts.append((x, max(r, 0.0)))
        return pts

    def validate(self):
        if self.diameter <= 0:
            raise ValueError(f"「{self.name}」の直径は正の値にしてください")
        if self.shape not in NOSE_SHAPES:
            raise ValueError(f"「{self.name}」の形状が不正です")
        super().validate()


@dataclass
class BodyTube(_Axial):
    TYPE: ClassVar[str] = "tube"
    LABEL: ClassVar[str] = "ボディチューブ"
    name: str = "ボディチューブ"
    length: float = 0.300
    diameter: float = 0.025
    material: str = "紙管（クラフト）"
    thickness: float = 0.0008
    mass_override: float | None = None

    def profile(self):
        return [(0.0, self.diameter / 2), (self.length, self.diameter / 2)]

    def validate(self):
        if self.diameter <= 0:
            raise ValueError(f"「{self.name}」の直径は正の値にしてください")
        super().validate()


@dataclass
class Transition(_Axial):
    TYPE: ClassVar[str] = "transition"
    LABEL: ClassVar[str] = "トランジション（円錐台）"
    name: str = "トランジション"
    length: float = 0.030
    fore_diameter: float = 0.025
    aft_diameter: float = 0.020
    material: str = "PLA（3Dプリント）"
    thickness: float = 0.0012
    mass_override: float | None = None

    def profile(self):
        return [(0.0, self.fore_diameter / 2), (self.length, self.aft_diameter / 2)]


@dataclass
class Ellipsoid(_Axial):
    """球（長さ = 直径）または楕円体。鈍頭物体として扱う（形状抗力が大きく、揚力はほぼ出ない）。"""
    TYPE: ClassVar[str] = "ellipsoid"
    LABEL: ClassVar[str] = "球・楕円体"
    BLUFF: ClassVar[bool] = True
    name: str = "球体"
    length: float = 0.080
    diameter: float = 0.080
    material: str = "PLA（3Dプリント）"
    thickness: float = 0.0008
    mass_override: float | None = None

    def profile(self, n: int = 24):
        L, R = self.length, self.diameter / 2
        pts = []
        for i in range(n + 1):
            u = 0.5 * (1 - math.cos(math.pi * i / n))
            pts.append((L * u, R * math.sqrt(max(0.0, 1 - (2 * u - 1) ** 2))))
        return pts

    @property
    def fineness(self) -> float:
        return self.length / self.diameter

    def validate(self):
        if self.diameter <= 0:
            raise ValueError(f"「{self.name}」の直径は正の値にしてください")
        super().validate()


@dataclass
class FreeformBody(_Axial):
    """(x, 直径) の点列で外形を指定する回転体。ノーズにも胴体にも使える。"""
    TYPE: ClassVar[str] = "freeform"
    LABEL: ClassVar[str] = "自由形状ボディ"
    name: str = "自由形状ボディ"
    points: list = field(default_factory=lambda: [[0.0, 0.0], [0.02, 0.016], [0.05, 0.024], [0.08, 0.025]])
    material: str = "PLA（3Dプリント）"
    thickness: float = 0.0012
    mass_override: float | None = None

    @property
    def length(self) -> float:
        return self.points[-1][0] - self.points[0][0] if len(self.points) >= 2 else 0.0

    def profile(self):
        x0 = self.points[0][0]
        return [(x - x0, d / 2) for x, d in self.points]

    def validate(self):
        if len(self.points) < 2:
            raise ValueError(f"「{self.name}」の点は 2 つ以上必要です")
        if any(b[0] < a[0] for a, b in zip(self.points, self.points[1:])):
            raise ValueError(f"「{self.name}」の x は先端から順に増えるように並べてください")
        super().validate()


# ----------------------------------------------------------------------------- フィン
@dataclass
class FinGeometry:
    """フィン 1 枚の平面形を短冊分割して求めた諸量（根元前縁が原点）。"""
    area: float
    span: float
    root_chord: float
    mac: float          # 平均空力翼弦
    x_mac_le: float     # MAC 前縁の x
    y_mac: float        # MAC の翼幅方向位置（= 面積重心の y）
    x_centroid: float
    tan_mid_sweep: float
    tan_le_sweep: float

    @property
    def aspect_ratio(self) -> float:
        return 2 * self.span ** 2 / self.area


@dataclass
class FinSet:
    TYPE: ClassVar[str] = "fin"
    LABEL: ClassVar[str] = "フィン"
    name: str = "フィン"
    count: int = 3
    shape: str = "trapezoid"
    root_chord: float = 0.060
    tip_chord: float = 0.030
    span: float = 0.045
    sweep: float = 0.030          # 根元前縁から翼端前縁までの軸方向距離
    points: list = field(default_factory=lambda: [[0.0, 0.0], [0.04, 0.045], [0.07, 0.045], [0.06, 0.0]])
    thickness: float = 0.002
    profile: str = "rounded"
    cant: float = 0.0             # 取付角 [deg]（回転させたい時に使う）
    incidence: float = 0.0        # 取付迎角 [deg]（機体の「上」方向に揚力。飛行機の主翼用）
    position: float = 0.0
    position_ref: str = "aft"
    roll_offset: float = 0.0      # 1 枚目の取付方向 [deg]（0 = 真上）
    material: str = "バルサ"
    mass_override: float | None = None

    def polygon(self) -> list[tuple[float, float]]:
        """根元前縁 (0,0) から外周を回って根元後縁 (根元翼弦,0) までの点列。"""
        if self.shape == "trapezoid":
            return [(0.0, 0.0), (self.sweep, self.span), (self.sweep + self.tip_chord, self.span),
                    (self.root_chord, 0.0)]
        if self.shape == "elliptical":
            n = 24
            return [(self.root_chord / 2 * (1 - math.cos(math.pi * i / n)), self.span * math.sin(math.pi * i / n))
                    for i in range(n + 1)]
        return [(float(x), float(y)) for x, y in self.points]

    def geometry(self, n: int = 60) -> FinGeometry:
        poly = self.polygon()
        span = max(y for _, y in poly)
        root = max(x for x, y in poly if y == 0) - min(x for x, y in poly if y == 0)
        closed = poly + [poly[0]]
        dy = span / n
        a = sx = sy = sc2 = sle = 0.0
        mids = []
        for j in range(n):
            y = span * (j + 0.5) / n
            xs = [x0 + (y - y0) * (x1 - x0) / (y1 - y0)
                  for (x0, y0), (x1, y1) in zip(closed, closed[1:])
                  if (y0 <= y < y1) or (y1 <= y < y0)]
            if len(xs) < 2:
                continue
            le, te = min(xs), max(xs)
            c = te - le
            a += c * dy
            sx += c * (le + te) / 2 * dy
            sy += c * y * dy
            sc2 += c * c * dy
            sle += le * c * dy
            mids.append((y, le, (le + te) / 2))
        if a <= 0 or not mids:
            raise ValueError(f"「{self.name}」の形状から面積を計算できません")
        y_tip, le_tip, mid_tip = mids[-1]
        y_root, le_root, mid_root = mids[0]
        dy_tot = max(y_tip - y_root, 1e-9)
        return FinGeometry(
            area=a, span=span, root_chord=root, mac=sc2 / a, x_mac_le=sle / a, y_mac=sy / a,
            x_centroid=sx / a,
            tan_mid_sweep=(mid_tip - mid_root) / dy_tot,
            tan_le_sweep=(le_tip - le_root) / dy_tot,
        )

    def validate(self) -> None:
        if not 1 <= self.count <= 8:
            raise ValueError(f"「{self.name}」の枚数は 1〜8 枚にしてください")
        if self.shape == "freeform":
            pts = self.points
            if len(pts) < 3:
                raise ValueError(f"「{self.name}」の自由形状は 3 点以上必要です")
            if pts[0][1] != 0 or pts[-1][1] != 0 or pts[-1][0] <= pts[0][0]:
                raise ValueError(f"「{self.name}」: 最初と最後の点は根元（y=0）にし、最後の点を後ろにしてください")
            if any(y < 0 for _, y in pts):
                raise ValueError(f"「{self.name}」: y（翼幅方向）は 0 以上にしてください")
        else:
            if self.root_chord <= 0 or self.span <= 0:
                raise ValueError(f"「{self.name}」の根元翼弦長と翼幅は正の値にしてください")
            if self.shape == "trapezoid" and self.tip_chord < 0:
                raise ValueError(f"「{self.name}」の翼端翼弦長は 0 以上にしてください")
        if self.thickness < 0 or abs(self.cant) > 15 or abs(self.incidence) > 15:
            raise ValueError(f"「{self.name}」の厚さ・取付角・取付迎角（±15°以内）が不正です")
        self.geometry()

    def angles(self) -> list[float]:
        """各フィンの取付方向 [rad]（0 = 機体の上 z_b、90° = 右 y_b）。"""
        return [math.radians(self.roll_offset + 360 * k / self.count) for k in range(self.count)]

    def mass_props(self, body_radius: float) -> MassProps:
        """フィンセット全体の質量特性（基準は根元前縁）。"""
        g = self.geometry()
        per_fin = (self.mass_override / self.count if self.mass_override is not None
                   else MATERIALS.get(self.material, 500) * g.area * self.thickness)
        m = per_fin * self.count
        rr = body_radius + g.y_mac
        i_ax = m * rr * rr
        i_tr = m * rr * rr / 2 + m * g.root_chord ** 2 / 18
        return MassProps(m, g.x_centroid, i_ax, i_tr)


@dataclass
class MassItem:
    TYPE: ClassVar[str] = "mass"
    LABEL: ClassVar[str] = "質量（おもり・搭載物）"
    name: str = "おもり"
    mass: float = 0.005
    position: float = 0.05        # 先端からの位置

    def validate(self):
        if self.mass < 0:
            raise ValueError(f"「{self.name}」の質量は 0 以上にしてください")


COMPONENT_TYPES = {cls.TYPE: cls for cls in (NoseCone, BodyTube, Transition, Ellipsoid, FreeformBody, FinSet,
                                              MassItem)}


# ----------------------------------------------------------------------------- 機体全体
@dataclass
class Rocket:
    name: str = "マイロケット"
    body: list = field(default_factory=list)
    fins: list = field(default_factory=list)
    masses: list = field(default_factory=list)
    override_mass: float | None = None   # 実測した機体質量（モーター除く）
    override_cg: float | None = None     # 実測した重心（モーター除く、先端から）
    cd_override: float | None = None     # Cd を手動で指定する場合
    roughness: float = 60e-6             # 表面粗さ [m]
    extra_cd: float = 0.0                # ランチラグ等の追加抗力係数

    def layout(self) -> list[tuple[_Axial, float]]:
        out, x = [], 0.0
        for comp in self.body:
            out.append((comp, x))
            x += comp.length
        return out

    @property
    def length(self) -> float:
        return sum(c.length for c in self.body)

    def profile(self) -> list[tuple[float, float]]:
        """機体全体の外形 (先端からの x, 半径)。段差は同じ x に 2 点並ぶ。"""
        pts = []
        for comp, x0 in self.layout():
            pts += [(x0 + x, r) for x, r in comp.profile()]
        return pts

    def radius_at(self, x: float) -> float:
        best = 0.0
        for (x0, r0), (x1, r1) in _segments(self.profile()):
            if x0 <= x <= x1:
                r = r0 if x1 == x0 else r0 + (r1 - r0) * (x - x0) / (x1 - x0)
                best = max(best, r)
        return best

    @property
    def max_radius(self) -> float:
        return max(r for _, r in self.profile())

    @property
    def ref_diameter(self) -> float:
        return 2 * self.max_radius

    @property
    def aft_radius(self) -> float:
        return self.body[-1].aft_radius

    def fin_le(self, fin: FinSet) -> float:
        """先端からフィン根元前縁までの距離。"""
        if fin.position_ref == "front":
            return fin.position
        return self.length - fin.position - fin.geometry().root_chord

    def fin_body_radius(self, fin: FinSet) -> float:
        return self.radius_at(self.fin_le(fin) + fin.geometry().root_chord / 2)

    def mass_breakdown(self) -> list[tuple[str, MassProps, float]]:
        items = [(c.name, c.mass_props(), x0) for c, x0 in self.layout()]
        items += [(f.name, f.mass_props(self.fin_body_radius(f)), self.fin_le(f)) for f in self.fins]
        items += [(m.name, MassProps(m.mass, 0.0, 0.0, 0.0), m.position) for m in self.masses]
        return items

    def dry_mass_props(self) -> MassProps:
        """モーターを除いた質量特性。実測値の指定があればそれを優先する。"""
        p = combine_mass([(mp, off) for _, mp, off in self.mass_breakdown()])
        if self.override_mass is not None:
            k = self.override_mass / p.mass if p.mass > 0 else 1.0
            p = MassProps(self.override_mass, p.cg, p.i_axial * k, p.i_trans * k)
        if self.override_cg is not None:
            p = MassProps(p.mass, self.override_cg, p.i_axial, p.i_trans)
        return p

    def validate(self) -> None:
        if not self.body:
            raise ValueError("機体に軸方向の部品（ノーズ・チューブ等）がありません")
        for c in self.body + self.fins + self.masses:
            c.validate()
        if self.max_radius <= 0:
            raise ValueError("機体の直径が 0 です")
        L = self.length
        for f in self.fins:
            le = self.fin_le(f)
            if le < -1e-9 or le + f.geometry().root_chord > L + 1e-9:
                raise ValueError(f"「{f.name}」が機体からはみ出しています（位置を確認）")
            if self.fin_body_radius(f) <= 0:
                raise ValueError(f"「{f.name}」の取付位置の胴体径が 0 です")
        if self.override_mass is not None and self.override_mass <= 0:
            raise ValueError("実測質量は正の値にしてください")
        if self.cd_override is not None and self.cd_override <= 0:
            raise ValueError("手動 Cd は正の値にしてください")

    # ---- 保存用 ----
    def to_dict(self) -> dict:
        def comp(c):
            d = asdict(c)
            d["type"] = c.TYPE
            return d
        return {
            "name": self.name,
            "body": [comp(c) for c in self.body],
            "fins": [comp(c) for c in self.fins],
            "masses": [comp(c) for c in self.masses],
            "override_mass": self.override_mass, "override_cg": self.override_cg,
            "cd_override": self.cd_override, "roughness": self.roughness, "extra_cd": self.extra_cd,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Rocket":
        def comp(cd):
            klass = COMPONENT_TYPES[cd["type"]]
            return klass(**{f.name: cd[f.name] for f in fields(klass) if f.name in cd})
        return cls(
            name=d.get("name", "マイロケット"),
            body=[comp(c) for c in d.get("body", [])],
            fins=[comp(c) for c in d.get("fins", [])],
            masses=[comp(c) for c in d.get("masses", [])],
            override_mass=d.get("override_mass"), override_cg=d.get("override_cg"),
            cd_override=d.get("cd_override"), roughness=d.get("roughness", 60e-6),
            extra_cd=d.get("extra_cd", 0.0),
        )
