"""設計全体のまとめ、安定性解析、大気モデル、パラシュート計算。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .aero import AeroModel, AeroPart, analyze
from .components import MassProps, Rocket, combine_mass
from .motors import Motor

G = 9.80665
R_AIR = 287.05


@dataclass
class LaunchConfig:
    rod_length: float = 1.0        # m
    rod_angle_deg: float = 3.0     # 鉛直からの傾き（風上側へ）
    wind_speed: float = 2.0        # 平均風速 [m/s]（+x 方向へ吹く）
    turbulence: float = 0.2        # 突風の強さ（平均風速に対する比）
    temperature_c: float = 15.0
    thrust_misalign_deg: float = 0.0  # 推力のずれ角
    seed: int = 1                  # 突風の乱数シード


@dataclass
class RecoveryConfig:
    chute_diameter: float = 0.30   # m（0 ならパラシュートなし）
    chute_cd: float = 0.8
    ejection_delay: float = 5.0    # 燃焼終了から放出までの秒数
    shock_cord: float = 0.8        # ショックコード長 [m]
    inflation_time: float = 0.0    # 開傘にかかる時間 [s]（0 なら速度と直径から推定）
    ejection_speed: float = 5.0    # 放出時にノーズが離れる相対速度 [m/s]


@dataclass
class Design:
    rocket: Rocket
    motor: Motor
    motor_overhang: float          # 機体後端からのモーター突出量 [m]
    launch: LaunchConfig
    recovery: RecoveryConfig

    def validate(self) -> None:
        self.rocket.validate()
        if hasattr(self.motor, "validate"):
            self.motor.validate()
        if self.motor.diameter > self.rocket.ref_diameter + 1e-9:
            raise ValueError(f"{self.motor.display_name}（直径 {self.motor.diameter * 1000:g} mm）が"
                             f"機体（最大径 {self.rocket.ref_diameter * 1000:.1f} mm）より太くなっています")
        if self.motor.length > self.rocket.length + self.motor_overhang:
            raise ValueError("モーターが機体より長くなっています")
        if self.launch.rod_length < 0 or not 0 <= self.launch.rod_angle_deg < 60:
            raise ValueError("ランチロッド長は 0 以上、発射角は 0〜60° にしてください")
        if self.launch.wind_speed < 0 or self.launch.turbulence < 0:
            raise ValueError("風速・突風の強さは 0 以上にしてください")
        r = self.recovery
        if min(r.chute_diameter, r.chute_cd, r.ejection_delay, r.shock_cord, r.inflation_time) < 0:
            raise ValueError("回収系の値は 0 以上にしてください")

    def motor_fit_note(self) -> str:
        """モーターが外形からはみ出す場合の注意（球体型など）。"""
        r = self.motor.diameter / 2
        x0 = max(self.nozzle_x - self.motor.length, 0.0)
        xs = [x0 + (min(self.nozzle_x, self.rocket.length) - x0) * k / 10 for k in range(11)]
        if any(self.rocket.radius_at(x) + 1e-9 < r for x in xs if x < self.rocket.length - 1e-6):
            return "モーターが機体の外形からはみ出しています（モーターチューブ部品の追加を推奨）"
        return ""

    @property
    def motor_cg(self) -> float:
        return self.rocket.length + self.motor_overhang - self.motor.length / 2

    @property
    def nozzle_x(self) -> float:
        return self.rocket.length + self.motor_overhang

    def motor_props(self, motor_mass: float) -> MassProps:
        r = getattr(self.motor, "exhaust_radius", self.motor.diameter / 2)
        L = self.motor.length
        rad = getattr(self.motor, "radial_sq", 0.0)  # クラスターで機軸から離れたモーターの分
        return MassProps(motor_mass, 0.0, motor_mass * (r * r / 2 + rad),
                         motor_mass * ((3 * r * r + L * L) / 12 + rad / 2))

    def mass_props(self, motor_mass: float, dry: MassProps | None = None) -> MassProps:
        dry = dry or self.rocket.dry_mass_props()
        return combine_mass([(dry, 0.0), (self.motor_props(motor_mass), self.motor_cg)])


@dataclass
class StabilityResult:
    aero: AeroModel
    launch: MassProps
    burnout: MassProps
    dry: MassProps

    @property
    def cp(self) -> float:
        return self.aero.x_cp

    @property
    def cp_yaw(self) -> float:
        return self.aero.x_cp_yaw

    @property
    def parts(self) -> list[AeroPart]:
        return self.aero.parts

    @property
    def diameter(self) -> float:
        return self.aero.ref_diameter

    @property
    def margin_launch(self) -> float:
        return (self.cp - self.launch.cg) / self.diameter

    @property
    def margin_burnout(self) -> float:
        return (self.cp - self.burnout.cg) / self.diameter

    @property
    def margin_launch_yaw(self) -> float:
        return (self.cp_yaw - self.launch.cg) / self.diameter

    @property
    def margin_burnout_yaw(self) -> float:
        return (self.cp_yaw - self.burnout.cg) / self.diameter

    @property
    def margin_min(self) -> float:
        return min(self.margin_launch, self.margin_launch_yaw)

    def ratio(self, margin: float) -> float:
        """安定比 [%] = (CP − CG) ÷ 全長 × 100。口径のマージンから換算する。"""
        return margin * self.diameter / self.aero.length * 100

    @property
    def ratio_min(self) -> float:
        return self.ratio(self.margin_min)


def analyze_stability(design: Design) -> StabilityResult:
    aero = analyze(design.rocket)
    dry = design.rocket.dry_mass_props()
    return StabilityResult(aero, design.mass_props(design.motor.total_mass, dry),
                           design.mass_props(design.motor.empty_mass, dry), dry)


def judge_margin(margin: float, diameter: float = 0.0, length: float = 0.0) -> tuple[str, str]:
    """スタビリティマージン [口径] の判定 -> (コメント, 状態 ok/warn/bad)。

    全長が直径の 5 倍未満のずんぐりした機体（球体型など）は、口径ではなく全長比で判定する。
    """
    if margin >= 0 and diameter > 0 and length > 0 and length / diameter < 5:
        pct = margin * diameter / length * 100
        if pct < 5:
            return f"安定性不足（全長の {pct:.0f} %、10 % 以上を推奨）", "bad"
        if pct < 10:
            return f"やや不足（全長の {pct:.0f} %）", "warn"
        return f"適正（ずんぐりした機体なので全長比で判定: {pct:.0f} %）", "ok"
    if margin < 0:
        return "不安定（CP が CG より前）", "bad"
    if margin < 1.0:
        return "安定性不足（1 cal 以上を推奨）", "bad"
    if margin <= 2.0:
        return "適正", "ok"
    if margin <= 3.0:
        return "やや強め（風に向かいやすい）", "ok"
    return "過安定（風見効果が大きい）", "warn"


def judge_ratio(ratio: float) -> tuple[str, str]:
    """安定比 [%]（(CP − CG) ÷ 全長）の判定。10〜20 % が目安。"""
    if ratio < 0:
        return "不安定（CP が CG より前）", "bad"
    if ratio < 5:
        return "小さすぎる（10〜20 % が目安）", "bad"
    if ratio < 10:
        return "やや小さい（10〜20 % が目安）", "warn"
    if ratio <= 20:
        return "適正（10〜20 %）", "ok"
    return "大きめ（風に向かいやすい）", "warn"


# ---- 大気・回収 ----
def atmosphere(h: float, ground_temp_c: float = 15.0) -> tuple[float, float]:
    """国際標準大気（地上気温のみ変更可）。高度 h [m] での (密度, 音速)。"""
    t0 = ground_temp_c + 273.15
    t = max(t0 - 0.0065 * h, 150.0)
    p = 101325.0 * (t / t0) ** 5.2559
    return p / (R_AIR * t), math.sqrt(1.4 * R_AIR * t)


def descent_rate(mass: float, diameter: float, cd: float, rho: float = 1.225) -> float:
    area = math.pi * diameter ** 2 / 4
    if area <= 0 or cd <= 0:
        return math.inf
    return math.sqrt(2 * mass * G / (rho * cd * area))


def required_chute_diameter(mass: float, target_rate: float, cd: float, rho: float = 1.225) -> float:
    area = 2 * mass * G / (rho * cd * target_rate ** 2)
    return math.sqrt(4 * area / math.pi)
