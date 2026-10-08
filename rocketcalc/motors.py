"""モーターの推力データ。RASP (.eng) 形式の読み書きに対応する。"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

# 内蔵モーター（Estes 製の公開推力曲線を元にした近似値）。
# 正確な値が必要なら thrustcurve.org などから .eng ファイルを読み込むこと。
BUILTIN_ENG = """\
; Estes A8 (近似)
A8 18 70 3-5 0.00312 0.0162 Estes
0.041 0.512
0.084 2.115
0.127 4.358
0.166 6.794
0.192 8.588
0.206 9.294
0.226 9.730
0.236 8.845
0.247 7.179
0.261 5.063
0.277 3.717
0.306 3.205
0.351 2.884
0.405 2.499
0.467 2.371
0.532 2.307
0.589 2.371
0.632 2.371
0.668 2.243
0.692 1.794
0.701 1.218
0.714 0.448
0.723 0.000
;
; Estes B6 (近似)
B6 18 70 0-2-4-6 0.0056 0.0196 Estes
0.023 0.688
0.057 2.457
0.089 4.816
0.116 7.274
0.148 9.929
0.171 12.140
0.191 11.695
0.200 10.719
0.209 9.240
0.230 7.667
0.255 6.488
0.305 5.505
0.375 4.719
0.477 4.621
0.580 4.522
0.671 4.326
0.746 4.425
0.786 3.243
0.802 1.474
0.825 0.000
;
; Estes C6 (近似)
C6 18 70 0-3-5-7 0.0108 0.0241 Estes
0.031 0.946
0.092 4.826
0.139 9.936
0.192 14.090
0.209 11.446
0.231 7.381
0.248 6.151
0.292 5.489
0.370 4.921
0.475 4.448
0.671 4.258
0.702 4.542
0.723 4.164
0.850 4.448
1.063 4.353
1.211 4.353
1.242 4.069
1.303 4.258
1.468 4.353
1.656 4.448
1.821 4.448
1.834 2.933
1.847 1.325
1.860 0.000
;
; Estes D12 (近似)
D12 24 70 0-3-5-7 0.0211 0.0442 Estes
0.049 2.569
0.116 9.369
0.184 17.275
0.237 24.258
0.282 29.730
0.297 27.010
0.311 22.589
0.322 17.990
0.348 14.126
0.386 12.099
0.442 10.808
0.546 9.876
0.718 9.306
0.879 9.105
1.066 8.901
1.257 8.698
1.436 8.310
1.590 8.294
1.612 4.613
1.650 0.000
;
"""

IMPULSE_CLASSES = [(0.3125, "1/8A"), (0.625, "1/4A"), (1.25, "1/2A"), (2.5, "A"), (5, "B"),
                   (10, "C"), (20, "D"), (40, "E"), (80, "F"), (160, "G"), (320, "H")]


@dataclass
class Motor:
    name: str
    diameter: float     # m
    length: float       # m
    delays: str         # 例: "0-3-5-7"（P はプラグ＝放出なし）
    prop_mass: float    # kg
    total_mass: float   # kg
    manufacturer: str
    times: list[float]
    thrusts: list[float]
    _impulse: list[float] = field(init=False, repr=False)

    def __post_init__(self):
        if not self.times or len(self.times) != len(self.thrusts):
            raise ValueError(f"モーター {self.name} の推力データが不正です")
        if self.times[0] > 0:
            self.times = [0.0] + list(self.times)
            self.thrusts = [0.0] + list(self.thrusts)
        cum = [0.0]
        for i in range(1, len(self.times)):
            dt = self.times[i] - self.times[i - 1]
            cum.append(cum[-1] + 0.5 * (self.thrusts[i] + self.thrusts[i - 1]) * dt)
        self._impulse = cum

    @property
    def display_name(self) -> str:
        return f"{self.manufacturer} {self.name}".strip()

    @property
    def burn_time(self) -> float:
        return self.times[-1]

    @property
    def total_impulse(self) -> float:
        return self._impulse[-1]

    @property
    def average_thrust(self) -> float:
        return self.total_impulse / self.burn_time

    @property
    def max_thrust(self) -> float:
        return max(self.thrusts)

    @property
    def empty_mass(self) -> float:
        return self.total_mass - self.prop_mass

    @property
    def impulse_class(self) -> str:
        for limit, letter in IMPULSE_CLASSES:
            if self.total_impulse <= limit * 1.0001:
                return letter
        return "I 以上"

    @property
    def delay_list(self) -> list[float]:
        out = []
        for part in self.delays.replace(",", "-").split("-"):
            try:
                out.append(float(part))
            except ValueError:
                pass
        return out

    def _interp(self, t: float, ys: list[float]) -> float:
        ts = self.times
        if t <= 0 or t >= ts[-1]:
            return ys[-1] if t >= ts[-1] else ys[0]
        i = bisect.bisect_right(ts, t)
        t0, t1 = ts[i - 1], ts[i]
        return ys[i - 1] + (ys[i] - ys[i - 1]) * (t - t0) / (t1 - t0)

    def thrust(self, t: float) -> float:
        if t < 0 or t >= self.burn_time:
            return 0.0
        return self._interp(t, self.thrusts)

    def mass(self, t: float) -> float:
        """時刻 t のモーター質量。推進剤は力積に比例して消費されると仮定。"""
        frac = self._interp(t, self._impulse) / self.total_impulse if t > 0 else 0.0
        return self.total_mass - self.prop_mass * frac

    def to_eng(self) -> str:
        lines = [f"{self.name} {self.diameter * 1000:g} {self.length * 1000:g} {self.delays} "
                 f"{self.prop_mass:.5g} {self.total_mass:.5g} {self.manufacturer or 'Custom'}"]
        lines += [f"{t:.4f} {f:.4f}" for t, f in zip(self.times, self.thrusts) if t > 0]
        lines.append(";")
        return "\n".join(lines)


def parse_eng(text: str) -> list[Motor]:
    """RASP (.eng) 形式のテキストを読み込む。1 ファイルに複数モーターがあってもよい。"""
    motors: list[Motor] = []
    header: list[str] | None = None
    ts: list[float] = []
    fs: list[float] = []

    def flush():
        nonlocal header, ts, fs
        if header is not None and ts:
            motors.append(Motor(
                name=header[0],
                diameter=float(header[1]) / 1000,
                length=float(header[2]) / 1000,
                delays=header[3],
                prop_mass=float(header[4]),
                total_mass=float(header[5]),
                manufacturer=" ".join(header[6:]),
                times=ts, thrusts=fs,
            ))
        header, ts, fs = None, [], []

    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        try:
            t, f = float(parts[0]), float(parts[1])
        except (ValueError, IndexError):
            flush()
            if len(parts) < 6:
                raise ValueError(f".eng のヘッダ行が不正です: {raw!r}")
            header = parts
            continue
        if header is None:
            raise ValueError(".eng ファイルにヘッダ行がありません")
        ts.append(t)
        fs.append(f)
    flush()
    if not motors:
        raise ValueError("モーターデータが見つかりませんでした")
    return motors


def builtin_motors() -> dict[str, Motor]:
    return {m.display_name: m for m in parse_eng(BUILTIN_ENG)}


def impulse_class_of(impulse: float) -> str:
    for limit, letter in IMPULSE_CLASSES:
        if impulse <= limit * 1.0001:
            return letter
    return "I 以上"


CLUSTER_LAYOUTS = {"ring": "円周上に並べる", "center": "中心に 1 本＋周り"}


@dataclass
class MotorMount:
    motor: Motor
    y: float              # 機軸からの位置（機体座標 y_b, z_b）[m]
    z: float
    ignited: bool = True  # False = 点火しない（不具合の想定）

    @property
    def offset(self) -> float:
        return math.hypot(self.y, self.z)


class MotorCluster:
    """複数のモーター（クラスター）。1 本のモーターと同じように扱えるようにまとめる。

    モーターの後端（ノズル）はそろえて取り付けるものとする。点火しないモーターは推力を出さず、
    推進剤も減らない（重りとして残る）。
    """

    def __init__(self, mounts: list[MotorMount]):
        if not mounts:
            raise ValueError("モーターが 1 本もありません")
        self.mounts = mounts
        self._lit = [m for m in mounts if m.ignited]
        # 合計の推力曲線（グラフ・最大推力用）
        self.times = sorted({0.0} | {t for m in self._lit for t in m.motor.times})
        self.thrusts = [sum(m.motor._interp(t, m.motor.thrusts) for m in self._lit if t <= m.motor.burn_time)
                        for t in self.times]

    # ---- 作成
    @classmethod
    def build(cls, main: Motor, count: int = 1, layout: str = "ring", radius: float = 0.0,
              outer: Motor | None = None, angle_deg: float = 0.0, failed: int = 0) -> "MotorCluster":
        """main を主モーターとしてクラスターを作る。radius = 0 なら隙間なく並べる。"""
        if not 1 <= count <= 9:
            raise ValueError("モーターの本数は 1〜9 本にしてください")
        if not 0 <= failed <= count:
            raise ValueError("点火しない本数は 0〜本数 にしてください")
        outer = outer or main
        gap = 0.0005
        if count == 1:
            specs = [(main, 0.0, 0.0)]
        else:
            center = layout == "center"
            n = count - 1 if center else count
            ring_motor = outer if center else main
            d = ring_motor.diameter
            need = d / 2 / math.sin(math.pi / n) if n > 1 else 0.0
            if center:
                need = max(need, (main.diameter + d) / 2)
            if radius <= 0:
                radius = need + gap if (n > 1 or center) else 0.0
            elif radius + 1e-9 < need:
                raise ValueError(f"モーター同士が重なります（配置半径を {need * 1000:.1f} mm 以上に）")
            specs = [(main, 0.0, 0.0)] if center else []
            for k in range(n):
                phi = math.radians(angle_deg + 360 * k / n)
                specs.append((ring_motor, radius * math.sin(phi), radius * math.cos(phi)))
        # 点火しないモーター: 周りのモーターから順に
        order = sorted(range(len(specs)), key=lambda i: math.hypot(specs[i][1], specs[i][2]) == 0)
        dead = set(order[:failed])
        return cls([MotorMount(m, y, z, i not in dead) for i, (m, y, z) in enumerate(specs)])

    def validate(self) -> None:
        for i, a in enumerate(self.mounts):
            for b in self.mounts[i + 1:]:
                if math.hypot(a.y - b.y, a.z - b.z) + 1e-9 < (a.motor.diameter + b.motor.diameter) / 2:
                    raise ValueError("モーター同士が重なります（配置半径を大きく）")

    # ---- 名前
    @property
    def primary(self) -> Motor:
        return self.mounts[0].motor

    def _label(self, attr: str) -> str:
        counts: dict[str, int] = {}
        for m in self.mounts:
            key = getattr(m.motor, attr)
            counts[key] = counts.get(key, 0) + 1
        return " + ".join(k if c == 1 else f"{k}×{c}" for k, c in counts.items())

    @property
    def name(self) -> str:
        return self._label("name")

    @property
    def display_name(self) -> str:
        return self._label("display_name")

    @property
    def count(self) -> int:
        return len(self.mounts)

    @property
    def delays(self) -> str:
        return self.primary.delays

    @property
    def delay_list(self) -> list[float]:
        return self.primary.delay_list

    # ---- 推力
    @property
    def burn_time(self) -> float:
        return max((m.motor.burn_time for m in self._lit), default=0.0)

    def thrust(self, t: float) -> float:
        return sum(m.motor.thrust(t) for m in self._lit)

    def mount_thrusts(self, t: float) -> list[float]:
        return [m.motor.thrust(t) if m.ignited else 0.0 for m in self.mounts]

    def thrust_moment(self, t: float) -> tuple[float, float]:
        """中心からずれた推力による機体座標のモーメント (M_y, M_z)。"""
        my = mz = 0.0
        for m in self._lit:
            T = m.motor.thrust(t)
            my += m.z * T
            mz -= m.y * T
        return my, mz

    @property
    def total_impulse(self) -> float:
        return sum(m.motor.total_impulse for m in self._lit)

    @property
    def average_thrust(self) -> float:
        return self.total_impulse / self.burn_time if self.burn_time > 0 else 0.0

    @property
    def max_thrust(self) -> float:
        return max(self.thrusts) if self.thrusts else 0.0

    @property
    def impulse_class(self) -> str:
        return impulse_class_of(self.total_impulse)

    # ---- 質量・寸法
    def mass(self, t: float) -> float:
        return sum(m.motor.mass(t) if m.ignited else m.motor.total_mass for m in self.mounts)

    @property
    def total_mass(self) -> float:
        return sum(m.motor.total_mass for m in self.mounts)

    @property
    def prop_mass(self) -> float:
        """燃える推進剤の質量（点火しないモーターの分は含めない）。"""
        return sum(m.motor.prop_mass for m in self._lit)

    @property
    def empty_mass(self) -> float:
        return self.total_mass - self.prop_mass

    @property
    def diameter(self) -> float:
        """全モーターを囲む円の直径。"""
        return 2 * max(m.offset + m.motor.diameter / 2 for m in self.mounts)

    @property
    def length(self) -> float:
        """質量で重み付けした長さ（重心位置の計算用）。"""
        return sum(m.motor.length * m.motor.total_mass for m in self.mounts) / self.total_mass

    @property
    def exhaust_radius(self) -> float:
        """噴射口の合計面積と同じ面積の円の半径（底面抗力の計算用）。"""
        return math.sqrt(sum((m.motor.diameter / 2) ** 2 for m in self._lit))

    @property
    def radial_sq(self) -> float:
        """機軸からの距離の 2 乗の質量平均（慣性モーメント用）。"""
        return sum(m.offset ** 2 * m.motor.total_mass for m in self.mounts) / self.total_mass
