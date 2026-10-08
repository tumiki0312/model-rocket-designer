"""6 自由度（並進 3 + 回転 3）の飛行シミュレーション。

座標系
- 世界座標: x = 風下方向, y = 横方向, z = 上
- 機体座標: x_b = 機首方向, y_b / z_b = 機軸に垂直（z_b 方向が「1 枚目のフィン」の向き）

モデル
- 推力は機軸方向（推力のずれ角を指定すると y_b 方向に傾く）
- 抗力は対気速度と逆向き、法線力は迎角に応じて CP に作用し、復元モーメントを生む
- ピッチ減衰（空力 + ジェット）、フィン取付角によるロールとロール減衰
- フィンは 1 枚ずつ、局所的な気流（機体の回転による分を含む）から力を求める
  （ピッチ/ヨー減衰・ロール・取付角/取付迎角・飛行機のような非対称配置を自然に扱える）
- 抗力は抗力中心に作用（重心より後ろなら安定化）
- ランチロッド上はロッド方向に拘束
- 回収: 放出 → ノーズ分離（ショックコードが伸びきるまで）→ 開傘（抗力面積が増える）→ 降下（3 自由度）
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np

from .aero import analyze
from .design import G, Design, atmosphere

ROD, FREE, CHUTE = 0, 1, 2
PHASE_NAMES = {ROD: "ランチロッド上", FREE: "飛行中", CHUTE: "パラシュート降下"}


def deploy_label(d: float) -> str:
    """回収状態 d（0〜1 分離中、1〜2 開傘中、2 全開）の表示名。"""
    if d < 1:
        return "放出・ノーズ分離中"
    if d < 2:
        return "パラシュート展開中"
    return "パラシュート降下"


class Wind:
    """平均風 + 1 次遅れの乱数で作る突風（再現性のためシード固定）。"""

    def __init__(self, mean: float, intensity: float, seed: int, t_max: float, dt: float = 0.05, tau: float = 1.0):
        self.mean, self.dt = mean, dt
        rng = random.Random(seed)
        sigma = intensity * mean
        a = math.exp(-dt / tau)
        b = sigma * math.sqrt(1 - a * a)
        n = int(t_max / dt) + 3
        g = [rng.gauss(0, sigma) for _ in range(3)]
        self.samples = []
        for _ in range(n):
            self.samples.append(tuple(g))
            g = [a * gi + b * rng.gauss(0, 1) for gi in g]

    def at(self, t: float) -> tuple[float, float, float]:
        f = t / self.dt
        i = min(int(f), len(self.samples) - 2)
        u = min(f - i, 1.0)
        g0, g1 = self.samples[i], self.samples[i + 1]
        return (self.mean + g0[0] + (g1[0] - g0[0]) * u,
                g0[1] + (g1[1] - g0[1]) * u,
                0.5 * (g0[2] + (g1[2] - g0[2]) * u))


def rotmat(q0, q1, q2, q3):
    return (
        (1 - 2 * (q2 * q2 + q3 * q3), 2 * (q1 * q2 - q0 * q3), 2 * (q1 * q3 + q0 * q2)),
        (2 * (q1 * q2 + q0 * q3), 1 - 2 * (q1 * q1 + q3 * q3), 2 * (q2 * q3 - q0 * q1)),
        (2 * (q1 * q3 - q0 * q2), 2 * (q2 * q3 + q0 * q1), 1 - 2 * (q1 * q1 + q2 * q2)),
    )


def quat_from_axis(u) -> list[float]:
    """機体 x 軸を単位ベクトル u に向ける四元数。"""
    ux, uy, uz = u
    ang = math.acos(max(-1.0, min(1.0, ux)))
    ax, ay, az = 0.0, -uz, uy
    n = math.sqrt(ay * ay + az * az)
    if n < 1e-12:
        return [1.0, 0.0, 0.0, 0.0] if ux > 0 else [0.0, 0.0, 0.0, 1.0]
    s = math.sin(ang / 2) / n
    return [math.cos(ang / 2), ax * s, ay * s, az * s]


@dataclass
class FlightResult:
    t: np.ndarray
    pos: np.ndarray       # (N,3)
    vel: np.ndarray       # (N,3)
    quat: np.ndarray      # (N,4) 機体→世界
    omega: np.ndarray     # (N,3) 機体座標の角速度 [rad/s]
    alpha: np.ndarray     # 迎角 [deg]
    mach: np.ndarray
    thrust: np.ndarray
    cd: np.ndarray
    margin: np.ndarray    # その時刻の CG での安定マージン [口径]
    roll: np.ndarray      # 累積ロール角 [deg]
    phase: np.ndarray
    wind: np.ndarray      # (N,3)
    deploy: np.ndarray    # 回収の進み具合（0〜1 分離、1〜2 開傘、2 全開）
    lifted: bool = True
    liftoff_time: float = math.nan      # 動き始めた時刻（推力が重量を超えた時）
    rod_exit_time: float = math.nan
    rod_exit_speed: float = math.nan
    burnout_time: float = 0.0
    apogee: float = 0.0
    apogee_time: float = math.nan
    max_speed: float = 0.0
    max_mach: float = 0.0
    max_accel_powered: float = 0.0
    rod_accel_avg: float = math.nan     # ランチロッド上の平均加速度 [m/s^2]（初期加速度）
    rod_accel_max: float = math.nan     # ランチロッド上の最大加速度 [m/s^2]
    ejection_time: float = math.nan
    ejection_speed: float = math.nan
    ejection_altitude: float = math.nan
    landing_time: float = math.nan
    landing_speed: float = math.nan
    landing_x: float = math.nan
    landing_y: float = math.nan
    max_alpha: float = 0.0
    max_roll_rate: float = 0.0     # rev/s
    tilt_burnout: float = math.nan  # 鉛直からの傾き [deg]
    tilt_apogee: float = math.nan
    separation_time: float = math.nan   # ショックコードが伸びきった時刻
    chute_open_time: float = math.nan   # パラシュートが全開した時刻
    inflation_time: float = math.nan
    opening_shock: float = math.nan     # 開傘衝撃の最大加速度 [G]
    notes: list[str] = field(default_factory=list)

    @property
    def speed(self) -> np.ndarray:
        return np.linalg.norm(self.vel, axis=1)

    @property
    def accel(self) -> np.ndarray:
        if len(self.t) < 3:
            return np.zeros(len(self.t))
        return np.linalg.norm(np.gradient(self.vel, self.t, axis=0), axis=1)

    @property
    def axis(self) -> np.ndarray:
        """機軸方向の単位ベクトル (N,3)。"""
        q0, q1, q2, q3 = self.quat.T
        return np.stack([1 - 2 * (q2 * q2 + q3 * q3), 2 * (q1 * q2 + q0 * q3), 2 * (q1 * q3 - q0 * q2)], axis=1)

    @property
    def tilt(self) -> np.ndarray:
        """機軸の鉛直からの傾き [deg]。"""
        return np.degrees(np.arccos(np.clip(self.axis[:, 2], -1, 1)))

    @property
    def landing_distance(self) -> float:
        return math.hypot(self.landing_x, self.landing_y)


class _Sim:
    def __init__(self, design: Design, t_max: float):
        self.d = design
        self.aero = analyze(design.rocket)
        self.dry = design.rocket.dry_mass_props()
        m = design.motor
        self.motor = m
        self.mr = getattr(m, "exhaust_radius", m.diameter / 2)
        self.radial_sq = getattr(m, "radial_sq", 0.0)
        self.thrust_moment = getattr(m, "thrust_moment", None)
        self.m_cg = design.motor_cg
        self.nozzle = design.nozzle_x
        self.aref = self.aero.ref_area
        self.dref = self.aero.ref_diameter
        self.temp = design.launch.temperature_c
        mis = math.radians(design.launch.thrust_misalign_deg)
        self.cmis, self.smis = math.cos(mis), math.sin(mis)
        # 胴体のピッチ減衰: Σ|CNα_i| (x_i − cg)^2 = S2 − 2 cg S1 + cg^2 S0（フィンは 1 枚ずつ計算）
        body = [p for p in self.aero.parts if p.kind != "fin"]
        self.s0 = sum(abs(p.cn_alpha) for p in body)
        self.s1 = sum(abs(p.cn_alpha) * p.x for p in body)
        self.s2 = sum(abs(p.cn_alpha) * p.x * p.x for p in body)
        # フィン 1 枚ごと: (CNα, CP位置, 腕, 取付方向 ey, ez, 法線 ny, nz, 追加の迎角)
        self.fin_list = []
        for f in self.aero.fins:
            for phi in f.fin.angles():
                ey, ez = math.sin(phi), math.cos(phi)
                ny, nz = ez, -ey
                extra = math.radians(f.fin.cant) - math.radians(f.fin.incidence) * nz
                self.fin_list.append((f.cn_alpha1, f.x_cp, f.span_arm, ey, ez, ny, nz, extra))
        r = design.recovery
        self.chute_cda = r.chute_cd * math.pi * r.chute_diameter ** 2 / 4
        self.chute_factor = 0.0
        self.x_drag = self.aero.wet_x
        self.wind = Wind(design.launch.wind_speed, design.launch.turbulence, design.launch.seed, t_max + 1)
        ang = math.radians(design.launch.rod_angle_deg)
        self.rod_u = (-math.sin(ang), 0.0, math.cos(ang))
        self.cd_now = 0.5

    def mass(self, t):
        mm = self.motor.mass(t)
        d = self.dry
        m = d.mass + mm
        cg = (d.mass * d.cg + mm * self.m_cg) / m
        ia = d.i_axial + mm * (self.mr * self.mr / 2 + self.radial_sq)
        it = (d.i_trans + d.mass * (d.cg - cg) ** 2
              + mm * ((3 * self.mr ** 2 + self.motor.length ** 2) / 12 + self.radial_sq / 2)
              + mm * (self.m_cg - cg) ** 2)
        return m, cg, ia, it

    def body_air(self, t, s):
        """機体座標での対気速度と回転行列。"""
        R = rotmat(s[6], s[7], s[8], s[9])
        wx, wy, wz = self.wind.at(t)
        ax, ay, az = s[3] - wx, s[4] - wy, s[5] - wz
        vb = (R[0][0] * ax + R[1][0] * ay + R[2][0] * az,
              R[0][1] * ax + R[1][1] * ay + R[2][1] * az,
              R[0][2] * ax + R[1][2] * ay + R[2][2] * az)
        return R, vb

    def deriv6(self, t, s, on_rod):
        m, cg, ia, it = self.mass(t)
        T = self.motor.thrust(t)
        R, vb = self.body_air(t, s)
        vbx, vby, vbz = vb
        V = math.sqrt(vbx * vbx + vby * vby + vbz * vbz)
        rho, _ = atmosphere(max(s[2], 0.0), self.temp)
        qd = 0.5 * rho * V * V
        fx, fy, fz = T * self.cmis, T * self.smis, 0.0
        mx = my = 0.0
        mz = (cg - self.nozzle) * T * self.smis
        if self.thrust_moment is not None and T > 0:
            tmy, tmz = self.thrust_moment(t)  # クラスターの推力が偏っている分
            my += tmy
            mz += tmz
        wx, wy, wz = s[10], s[11], s[12]
        if V > 1e-6:
            k = qd * self.aref * self.cd_now / V
            fx -= k * vbx
            fy -= k * vby
            fz -= k * vbz
            rd = cg - self.x_drag  # 抗力は抗力中心に作用
            my += rd * k * vbz
            mz -= rd * k * vby
            lat = math.sqrt(vby * vby + vbz * vbz)
            if lat > 1e-9:
                cn, xcp = self.aero.normal_body(math.atan2(lat, vbx))
                fn = qd * self.aref * cn
                ny, nz = vby / lat, vbz / lat
                fy -= fn * ny
                fz -= fn * nz
                rx = cg - xcp
                my += rx * fn * nz
                mz -= rx * fn * ny
            cdamp = 0.5 * rho * V * self.aref * (self.s2 - 2 * cg * self.s1 + cg * cg * self.s0)
            my -= cdamp * wy
            mz -= cdamp * wz
            # フィン 1 枚ずつ: 局所の気流 = 対気速度 + 回転による速度
            half_rho_a = 0.5 * rho * self.aref
            for cn1, xf, arm, ey, ez, ny, nz, extra in self.fin_list:
                rx, ry, rz = cg - xf, arm * ey, arm * ez
                lx = vbx + wy * rz - wz * ry
                ly = vby + wz * rx - wx * rz
                lz = vbz + wx * ry - wy * rx
                vn = ly * ny + lz * nz
                a = math.atan2(vn, abs(lx)) + (extra if lx >= 0 else -extra)
                f = -half_rho_a * (lx * lx + vn * vn) * cn1 * math.sin(a)
                fy_, fz_ = f * ny, f * nz
                fy += fy_
                fz += fz_
                mx += ry * fz_ - rz * fy_
                my -= rx * fz_
                mz += rx * fy_
        if T > 0:
            mdot = self.motor.prop_mass * T / self.motor.total_impulse
            jet = mdot * (self.nozzle - cg) ** 2
            my -= jet * wy
            mz -= jet * wz
        ax = (R[0][0] * fx + R[0][1] * fy + R[0][2] * fz) / m
        ay = (R[1][0] * fx + R[1][1] * fy + R[1][2] * fz) / m
        az = (R[2][0] * fx + R[2][1] * fy + R[2][2] * fz) / m - G
        if on_rod:
            ux, uy, uz = self.rod_u
            a_al = ax * ux + ay * uy + az * uz
            v_al = s[3] * ux + s[4] * uy + s[5] * uz
            if v_al <= 1e-9 and a_al < 0:
                a_al = 0.0
            return [s[3], s[4], s[5], a_al * ux, a_al * uy, a_al * uz] + [0.0] * 7
        q0, q1, q2, q3 = s[6], s[7], s[8], s[9]
        return [
            s[3], s[4], s[5], ax, ay, az,
            0.5 * (-q1 * wx - q2 * wy - q3 * wz),
            0.5 * (q0 * wx + q2 * wz - q3 * wy),
            0.5 * (q0 * wy + q3 * wx - q1 * wz),
            0.5 * (q0 * wz + q1 * wy - q2 * wx),
            mx / ia,
            (my - (ia - it) * wx * wz) / it,
            (mz - (it - ia) * wx * wy) / it,
        ]

    def deriv3(self, t, s, _):
        m, *_ = self.mass(t)
        wx, wy, wz = self.wind.at(t)
        ax, ay, az = s[3] - wx, s[4] - wy, s[5] - wz
        V = math.sqrt(ax * ax + ay * ay + az * az)
        rho, _ = atmosphere(max(s[2], 0.0), self.temp)
        k = 0.5 * rho * V * (self.cd_now * self.aref + self.chute_cda * self.chute_factor) / m
        return [s[3], s[4], s[5], -k * ax, -k * ay, -k * az - G] + [0.0] * 7

    def observe(self, t, s):
        """(迎角 deg, マッハ, Cd, CG位置) を返す。"""
        _, vb = self.body_air(t, s)
        V = math.sqrt(sum(v * v for v in vb))
        _, sound = atmosphere(max(s[2], 0.0), self.temp)
        alpha = math.degrees(math.atan2(math.hypot(vb[1], vb[2]), vb[0])) if V > 1e-6 else 0.0
        return alpha, V / sound, V


def _rk4(f, t, s, dt, flag):
    k1 = f(t, s, flag)
    k2 = f(t + dt / 2, [a + dt / 2 * b for a, b in zip(s, k1)], flag)
    k3 = f(t + dt / 2, [a + dt / 2 * b for a, b in zip(s, k2)], flag)
    k4 = f(t + dt, [a + dt * b for a, b in zip(s, k3)], flag)
    return [a + dt / 6 * (b + 2 * c + 2 * d + e) for a, b, c, d, e in zip(s, k1, k2, k3, k4)]


def simulate(design: Design, *, deploy: bool = True, stop_at_apogee: bool = False,
             t_max: float = 400.0) -> FlightResult:
    sim = _Sim(design, t_max)
    motor = design.motor
    burn = motor.burn_time
    has_chute = deploy and sim.chute_cda > 0
    t_eject = burn + design.recovery.ejection_delay if deploy else math.inf
    rod_len = max(design.launch.rod_length, 0.01)
    ux, uy, uz = sim.rod_u

    s = [0.0] * 6 + quat_from_axis(sim.rod_u) + [0.0] * 3
    t = 0.0
    phase = ROD
    rec: dict[str, list] = {k: [] for k in ("t", "s", "alpha", "mach", "thrust", "cd", "margin", "roll", "phase",
                                            "wind", "deploy")}
    rcfg = design.recovery
    t_sep = max(rcfg.shock_cord / max(rcfg.ejection_speed, 0.5), 0.05)
    t_sep_end = t_infl_end = math.inf
    t_inf = 0.0
    deploy_state = 0.0
    res_kw: dict = {"burnout_time": burn}
    notes: list[str] = []
    roll_angle = 0.0
    next_rec = 0.0
    apogee_found = False
    lifted = True
    max_speed = max_mach = max_alpha = max_roll = 0.0
    landed = False
    chute_start = math.inf

    def record(t, s):
        alpha, mach, V = sim.observe(t, s)
        m, cg, *_ = sim.mass(t)
        rec["t"].append(t)
        rec["s"].append(list(s))
        rec["alpha"].append(alpha)
        rec["mach"].append(mach)
        rec["thrust"].append(motor.thrust(t))
        rec["cd"].append(sim.cd_now)
        rec["margin"].append((sim.aero.x_cp_worst - cg) / sim.dref)
        rec["roll"].append(math.degrees(roll_angle))
        rec["phase"].append(phase)
        rec["wind"].append(sim.wind.at(t))
        rec["deploy"].append(deploy_state)

    record(t, s)
    while t < t_max:
        _, vb = sim.body_air(t, s)
        V = math.sqrt(sum(v * v for v in vb))
        _, sound = atmosphere(max(s[2], 0.0), sim.temp)
        thrusting = motor.thrust(t) > 0
        sim.cd_now, sim.x_drag = sim.aero.cd_and_center(V, V / sound, sim.mr if thrusting else 0.0)

        if phase == CHUTE:
            # 回収の段階: 分離中は抗力なし → 開傘中は面積が (経過/開傘時間)^2 で増える → 全開
            if t < t_sep_end:
                deploy_state = (t - chute_start) / t_sep
                sim.chute_factor = 0.0
            else:
                if math.isinf(t_infl_end):
                    t_inf = rcfg.inflation_time or min(max(8 * rcfg.chute_diameter / max(V, 3.0), 0.2), 2.5)
                    t_infl_end = t_sep_end + t_inf
                    res_kw["separation_time"] = t_sep_end
                    res_kw["inflation_time"] = t_inf
                f = min((t - t_sep_end) / t_inf, 1.0)
                deploy_state = 1.0 + f
                sim.chute_factor = f * f
                if f >= 1.0 and "chute_open_time" not in res_kw:
                    res_kw["chute_open_time"] = t
            dt = 0.002 if t < t_infl_end + 1.0 else 0.02
            s_new = _rk4(sim.deriv3, t, s, dt, False)
        else:
            dt = 0.001
            s_new = _rk4(sim.deriv6, t, s, dt, phase == ROD)
            qn = math.sqrt(sum(x * x for x in s_new[6:10]))
            s_new[6:10] = [x / qn for x in s_new[6:10]]
            roll_angle += s_new[10] * dt
        t_prev, s_prev = t, s
        t, s = t + dt, s_new

        speed = math.sqrt(s[3] ** 2 + s[4] ** 2 + s[5] ** 2)
        max_speed = max(max_speed, speed)
        max_mach = max(max_mach, speed / sound)

        if phase == ROD:
            travel = s[0] * ux + s[1] * uy + s[2] * uz
            if speed > 0 and "liftoff_time" not in res_kw:
                res_kw["liftoff_time"] = t_prev
            if travel >= rod_len:
                phase = FREE
                res_kw["rod_exit_time"], res_kw["rod_exit_speed"] = t, speed
            elif t > burn and speed < 1e-6:
                lifted = False
                notes.append("推力不足で離陸できません")
                break
        elif phase == FREE:
            max_roll = max(max_roll, abs(s[10]) / (2 * math.pi))
            if not apogee_found and t < t_eject:
                alpha, _, Vair = sim.observe(t, s)
                if Vair > 10:
                    max_alpha = max(max_alpha, alpha)

        if math.isnan(res_kw.get("tilt_burnout", math.nan)) and t >= burn and phase != ROD:
            ax_z = 2 * (s[7] * s[9] - s[6] * s[8])  # 機軸の z 成分
            res_kw["tilt_burnout"] = math.degrees(math.acos(max(-1, min(1, ax_z))))

        if not apogee_found and phase != ROD and s[5] <= 0:
            apogee_found = True
            res_kw["apogee"], res_kw["apogee_time"] = s[2], t
            ax_z = 2 * (s[7] * s[9] - s[6] * s[8])  # 機軸の z 成分
            res_kw["tilt_apogee"] = math.degrees(math.acos(max(-1, min(1, ax_z))))
            if stop_at_apogee:
                record(t, s)
                break

        if t >= t_eject and "ejection_time" not in res_kw:
            res_kw["ejection_time"], res_kw["ejection_speed"], res_kw["ejection_altitude"] = t, speed, s[2]
            if has_chute and phase != ROD:
                phase = CHUTE
                chute_start = t
                t_sep_end = t + t_sep
                s[10:13] = [0.0, 0.0, 0.0]

        if phase != ROD and s[2] < 0:
            f = s_prev[2] / (s_prev[2] - s[2]) if s_prev[2] != s[2] else 1.0
            s = [a + f * (b - a) for a, b in zip(s_prev, s)]
            t = t_prev + f * dt
            s[2] = 0.0
            res_kw["landing_time"] = t
            res_kw["landing_speed"] = math.sqrt(s[3] ** 2 + s[4] ** 2 + s[5] ** 2)
            res_kw["landing_x"], res_kw["landing_y"] = s[0], s[1]
            record(t, s)
            landed = True
            break

        if t >= next_rec:
            record(t, s)
            next_rec = t + (0.005 if phase != CHUTE or t < t_infl_end + 1.0 else 0.05)
    else:
        notes.append("シミュレーション時間の上限に達しました")
    if not landed and not stop_at_apogee and lifted and t >= t_max:
        record(t, s)

    states = np.array(rec["s"])
    res = FlightResult(
        t=np.array(rec["t"]), pos=states[:, 0:3], vel=states[:, 3:6], quat=states[:, 6:10],
        omega=states[:, 10:13], alpha=np.array(rec["alpha"]), mach=np.array(rec["mach"]),
        thrust=np.array(rec["thrust"]), cd=np.array(rec["cd"]), margin=np.array(rec["margin"]),
        roll=np.array(rec["roll"]), phase=np.array(rec["phase"]), wind=np.array(rec["wind"]),
        deploy=np.array(rec["deploy"]),
        lifted=lifted, max_speed=max_speed, max_mach=max_mach, max_alpha=max_alpha,
        max_roll_rate=max_roll, notes=notes, **res_kw,
    )
    if lifted and len(res.t) > 2:
        powered = res.t <= burn
        acc = res.accel
        res.max_accel_powered = float(acc[powered].max()) if powered.any() else 0.0
        if math.isfinite(res.rod_exit_time) and math.isfinite(res.liftoff_time):
            res.rod_accel_avg = res.rod_exit_speed / max(res.rod_exit_time - res.liftoff_time, 1e-6)
            on_rod = res.t <= res.rod_exit_time
            res.rod_accel_max = float(acc[on_rod].max()) if on_rod.any() else math.nan
        if not apogee_found:
            i = int(res.pos[:, 2].argmax())
            res.apogee, res.apogee_time = float(res.pos[i, 2]), float(res.t[i])
        if math.isfinite(res.separation_time):
            # 開傘衝撃: 重力を除いた加速度（比力）の最大値
            acc = np.gradient(res.vel, res.t, axis=0)
            acc[:, 2] += G
            after = (res.t >= res.separation_time) & (res.t <= res.separation_time + t_inf + 1.0)
            if after.any():
                res.opening_shock = float(np.linalg.norm(acc[after], axis=1).max() / G)
    return res


def optimal_delay(design: Design, result: FlightResult | None = None) -> float:
    """頂点で放出されるための遅延秒数。放出が頂点より後の結果があればそれを使う。"""
    r = result
    if r is None or not (r.ejection_time >= r.apogee_time):
        r = simulate(design, deploy=False, stop_at_apogee=True)
    if not r.lifted or math.isnan(r.apogee_time):
        return math.nan
    return max(r.apogee_time - design.motor.burn_time, 0.0)
