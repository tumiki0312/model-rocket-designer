"""任意形状の機体の空力計算。

- 法線力・圧力中心: 細長物体理論（Barrowman 法の一般化）＋ Diederich の翼理論によるフィン、
  大迎角時の胴体揚力（Galejs）。球・楕円体は鈍頭物体として揚力を持たないものとする。
- フィンは 1 枚ずつ取付方向を考えるので、飛行機のような非対称な配置（主翼・垂直尾翼）も扱える。
  ピッチ面（機体の上下 z_b 方向の横流れ）とヨー面（左右 y_b 方向）で別々に CP を求める。
- 抗力: 表面摩擦・底面・フィン前後縁・段差/傾斜面・鈍頭物体の形状抗力の積み上げ。
  各抗力が働く位置も求め、抗力中心が重心の後ろなら安定化（バドミントンの羽根と同じ効果）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .components import FinSet, NoseCone, Rocket

# フィン枚数ごとの干渉補正
_FIN_COUNT_FACTOR = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 0.948, 6: 0.913, 7: 0.854, 8: 0.81}
BODY_LIFT_K = 1.1
SPHERE_CD = 0.47


@dataclass
class AeroPart:
    name: str
    kind: str          # body / step / fin
    cn_alpha: float    # ピッチ面の法線力傾斜（基準面積あたり）[1/rad]
    x: float           # 先端からの CP 位置 [m]
    cn_alpha_yaw: float | None = None  # ヨー面（None ならピッチと同じ）

    @property
    def cn_yaw(self) -> float:
        return self.cn_alpha if self.cn_alpha_yaw is None else self.cn_alpha_yaw


@dataclass
class FinAero:
    fin: FinSet
    cn_alpha1: float     # 1 枚あたり（自身の面内、胴体干渉・枚数補正込み）
    span_arm: float      # 機軸から MAC までの距離
    x_cp: float          # 先端からの CP
    x_centroid: float    # 抗力の作用位置
    area: float
    span: float
    mac: float
    cos_le: float


@dataclass
class AeroModel:
    ref_area: float
    ref_diameter: float
    length: float
    parts: list[AeroPart]
    fins: list[FinAero]
    plan_area: float
    plan_x: float
    body_wet: float
    wet_x: float
    base_radius: float
    roughness: float
    extra_cd: float
    cd_override: float | None
    pressure_cd: float        # 段差・傾斜面の圧力抗力
    rear_step_cd: float       # 後ろ向き段差・ボートテール（底面抗力係数に掛ける面積比）
    pressure_x: float
    bluff: list[tuple[float, float]]  # 鈍頭物体の (形状抗力係数, 中心位置)

    # ---- 静的な CP
    @property
    def cn_alpha(self) -> float:
        return sum(p.cn_alpha for p in self.parts)

    @property
    def x_cp(self) -> float:
        cn = self.cn_alpha
        return sum(p.cn_alpha * p.x for p in self.parts) / cn if abs(cn) > 1e-12 else 0.0

    @property
    def cn_alpha_yaw(self) -> float:
        return sum(p.cn_yaw for p in self.parts)

    @property
    def x_cp_yaw(self) -> float:
        cn = self.cn_alpha_yaw
        return sum(p.cn_yaw * p.x for p in self.parts) / cn if abs(cn) > 1e-12 else 0.0

    @property
    def symmetric(self) -> bool:
        return abs(self.cn_alpha - self.cn_alpha_yaw) < 1e-6

    @property
    def x_cp_worst(self) -> float:
        """ピッチ・ヨーのうち前にある方（安定性の厳しい方）。"""
        return min(self.x_cp, self.x_cp_yaw)

    # ---- 迎角があるとき
    def normal(self, alpha: float) -> tuple[float, float]:
        """ピッチ面で迎角 alpha [rad] のときの法線力係数と CP 位置（フィン込み、胴体揚力込み）。"""
        return self._normal(alpha, self.cn_alpha, self.x_cp)

    def normal_body(self, alpha: float) -> tuple[float, float]:
        """胴体だけの法線力（フィンは飛行計算で 1 枚ずつ扱う）。"""
        body = [p for p in self.parts if p.kind != "fin"]
        cn = sum(p.cn_alpha for p in body)
        x = sum(p.cn_alpha * p.x for p in body) / cn if abs(cn) > 1e-12 else self.plan_x
        return self._normal(alpha, cn, x)

    def _normal(self, alpha, cn_alpha, x_cp):
        sa = math.sin(alpha)
        cn_lin = cn_alpha * sa
        cn_body = BODY_LIFT_K * self.plan_area / self.ref_area * sa * sa
        cn = cn_lin + cn_body
        if abs(cn) < 1e-12:
            return 0.0, x_cp
        return cn, (cn_lin * x_cp + cn_body * self.plan_x) / cn

    # ---- 抗力
    def drag_items(self, v: float, mach: float, motor_radius: float = 0.0,
                   nu: float = 1.5e-5) -> list[tuple[str, float, float]]:
        """抗力の内訳 (名前, Cd, 作用位置)。motor_radius > 0 なら燃焼中（底面抗力を減らす）。"""
        if self.cd_override is not None:
            return [("手動指定", self.cd_override, self.wet_x)]
        m2 = min(mach, 0.95) ** 2
        aref = self.ref_area
        re = max(v, 0.1) * self.length / nu
        cf = 1.48e-2 if re < 1e4 else 1 / (1.50 * math.log(re) - 5.6) ** 2
        cf = max(cf, 0.032 * (self.roughness / self.length) ** 0.2) * (1 - 0.1 * m2)
        fineness = self.length / self.ref_diameter
        items = [("胴体の表面摩擦", cf * (1 + 1 / (2 * fineness)) * self.body_wet / aref, self.wet_x)]
        base_coef = 0.12 + 0.13 * m2
        for f in self.fins:
            fs = f.fin
            fric = cf * (1 + 2 * fs.thickness / f.mac) * 2 * fs.count * f.area / aref
            stag = 0.85 * (1 + m2 / 4 + m2 * m2 / 40)
            le = {"square": stag, "rounded": (1 - m2) ** -0.417 - 1, "airfoil": 0.0}[fs.profile]
            te = {"square": base_coef, "rounded": base_coef / 2, "airfoil": 0.0}[fs.profile]
            press = (le * f.cos_le ** 2 + te) * fs.count * fs.thickness * f.span / aref
            items.append(("フィンの表面摩擦", fric, f.x_centroid))
            items.append(("フィン前縁・後縁", press, f.x_centroid))
        base_area = math.pi * max(self.base_radius ** 2 - motor_radius ** 2, 0.0)
        items.append(("底面", base_coef * base_area / aref, self.length))
        items.append(("段差・傾斜面", self.pressure_cd + base_coef * self.rear_step_cd, self.pressure_x))
        for cd, x in self.bluff:
            items.append(("球・楕円体の形状抗力", cd, x))
        if self.extra_cd:
            items.append(("追加（ランチラグ等）", self.extra_cd, self.wet_x))
        return items

    def drag(self, v: float, mach: float, motor_radius: float = 0.0) -> dict[str, float]:
        out: dict[str, float] = {}
        for name, cd, _ in self.drag_items(v, mach, motor_radius):
            out[name] = out.get(name, 0.0) + cd
        return out

    def cd(self, v: float, mach: float, motor_radius: float = 0.0) -> float:
        return sum(cd for _, cd, _ in self.drag_items(v, mach, motor_radius))

    def cd_and_center(self, v: float, mach: float, motor_radius: float = 0.0) -> tuple[float, float]:
        items = self.drag_items(v, mach, motor_radius)
        cd = sum(c for _, c, _ in items)
        x = sum(c * x for _, c, x in items) / cd if cd > 0 else self.wet_x
        return cd, x


def fin_cn_alpha1(fin: FinSet, aref: float, body_radius: float, mach: float = 0.0):
    """フィン 1 枚あたりの CNα（Diederich の式、胴体干渉込み）と形状。"""
    g = fin.geometry()
    beta = math.sqrt(max(1 - mach * mach, 0.1))
    cos_gc = 1 / math.sqrt(1 + g.tan_mid_sweep ** 2)
    ar = g.aspect_ratio
    cn1 = 2 * math.pi * ar * (g.area / aref) / (2 + math.sqrt(4 + (beta * ar / cos_gc) ** 2))
    cn1 *= 1 + body_radius / (g.span + body_radius)
    return cn1, g


def analyze(rocket: Rocket, mach: float = 0.0) -> AeroModel:
    rocket.validate()
    rmax = rocket.max_radius
    aref = math.pi * rmax * rmax
    parts: list[AeroPart] = []
    plan = plan_mx = wet = wet_mx = 0.0
    pressure = rear = press_mx = 0.0
    bluff: list[tuple[float, float]] = []
    prev_aft_r = 0.0
    prev_bluff = False
    for comp, x0 in rocket.layout():
        prof = comp.profile()
        r_fore, r_aft = prof[0][1], prof[-1][1]
        if comp.BLUFF:
            # 鈍頭物体: 揚力なし、形状抗力（摩擦込み）を中心に
            area = math.pi * max(r for _, r in prof) ** 2
            cd = SPHERE_CD * comp.fineness ** -0.8 * area / aref
            bluff.append((cd, x0 + comp.length / 2))
            prev_aft_r, prev_bluff = r_aft, True
            continue
        # 部品の境目の段差
        d_area = math.pi * (r_fore ** 2 - prev_aft_r ** 2)
        if abs(d_area) > 1e-12:
            parts.append(AeroPart(f"段差（{comp.name} 前端）", "step", 2 * d_area / aref, x0))
            if d_area > 0 and not prev_bluff:  # 鈍頭物体の後ろは後流の中なので圧力抗力なし
                pressure += 0.85 * d_area / aref
                press_mx += 0.85 * d_area / aref * x0
            elif d_area < 0:
                rear += -d_area / aref
                press_mx += 0.12 * -d_area / aref * x0
        # 部品本体: CNα = 2ΔA/Aref, x = (x_aft A_aft − V)/ΔA
        a_fore, a_aft = math.pi * r_fore ** 2, math.pi * r_aft ** 2
        vol = 0.0
        for (xa, ra), (xb, rb) in zip(prof, prof[1:]):
            dx = xb - xa
            xm = x0 + (xa + xb) / 2
            vol += math.pi * dx * (ra * ra + ra * rb + rb * rb) / 3
            dw = math.pi * (ra + rb) * math.hypot(dx, rb - ra)
            wet += dw
            wet_mx += dw * xm
            pa = (ra + rb) * dx
            plan += pa
            plan_mx += pa * xm
            da = math.pi * (rb * rb - ra * ra)
            if da > 0 and not (isinstance(comp, NoseCone) and comp.shape != "cone"):
                sin2 = (rb - ra) ** 2 / ((rb - ra) ** 2 + dx * dx)
                c = 0.8 * sin2 * da / aref
                pressure += c
                press_mx += c * xm
            elif da < 0:
                steep = dx / (2 * (ra - rb))
                c = max(0.0, min(1.0, (3 - steep) / 2)) * (-da) / aref
                rear += c
                press_mx += 0.12 * c * xm
        if abs(a_aft - a_fore) > 1e-12:
            x = x0 + (comp.length * a_aft - vol) / (a_aft - a_fore)
            parts.append(AeroPart(comp.name, "body", 2 * (a_aft - a_fore) / aref, x))
        prev_aft_r, prev_bluff = r_aft, False

    fins = []
    for fin in rocket.fins:
        r_body = rocket.fin_body_radius(fin)
        cn1, g = fin_cn_alpha1(fin, aref, r_body, mach)
        cn1 *= _FIN_COUNT_FACTOR[fin.count]
        angles = fin.angles()
        cn_pitch = cn1 * sum(math.sin(a) ** 2 for a in angles)
        cn_yaw = cn1 * sum(math.cos(a) ** 2 for a in angles)
        x = rocket.fin_le(fin) + g.x_mac_le + 0.25 * g.mac
        parts.append(AeroPart(fin.name, "fin", cn_pitch, x, cn_yaw))
        fins.append(FinAero(fin, cn1, r_body + g.y_mac, x, rocket.fin_le(fin) + g.x_centroid,
                            g.area, g.span, g.mac, 1 / math.sqrt(1 + g.tan_le_sweep ** 2)))

    p_total = pressure + 0.12 * rear
    return AeroModel(
        ref_area=aref, ref_diameter=2 * rmax, length=rocket.length, parts=parts, fins=fins,
        plan_area=plan, plan_x=plan_mx / plan if plan > 0 else rocket.length / 2,
        body_wet=wet, wet_x=wet_mx / wet if wet > 0 else rocket.length / 2,
        base_radius=rocket.aft_radius, roughness=rocket.roughness,
        extra_cd=rocket.extra_cd, cd_override=rocket.cd_override,
        pressure_cd=pressure, rear_step_cd=rear,
        pressure_x=press_mx / p_total if p_total > 0 else rocket.length / 2,
        bluff=bluff,
    )
