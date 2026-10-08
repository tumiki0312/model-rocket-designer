"""機体テンプレート。いろいろな形状の例として使う。"""
from __future__ import annotations

from .components import BodyTube, Ellipsoid, FinSet, FreeformBody, MassItem, NoseCone, Rocket, Transition


def _std_masses(length: float) -> list[MassItem]:
    return [
        MassItem("パラシュート・ショックコード", 0.008, 0.16),
        MassItem("エンジンマウント", 0.006, length - 0.035),
    ]


def standard() -> Rocket:
    return Rocket(
        name="標準 3 枚フィン",
        body=[NoseCone(length=0.070, diameter=0.025), BodyTube(length=0.300, diameter=0.025)],
        fins=[FinSet()],
        masses=_std_masses(0.37),
    )


def boattail_elliptical() -> Rocket:
    return Rocket(
        name="ボートテール＋楕円フィン",
        body=[
            NoseCone(shape="haack", shape_param=0.0, length=0.110, diameter=0.025),
            BodyTube(length=0.260, diameter=0.025),
            Transition(name="ボートテール", length=0.025, fore_diameter=0.025, aft_diameter=0.019),
        ],
        fins=[FinSet(name="楕円フィン", shape="elliptical", root_chord=0.075, span=0.045, profile="airfoil",
                     position=0.035)],
        masses=_std_masses(0.395),
    )


def canard() -> Rocket:
    return Rocket(
        name="カナード付き",
        body=[NoseCone(shape="cone", length=0.080, diameter=0.025), BodyTube(length=0.360, diameter=0.025)],
        fins=[
            FinSet(name="カナード", count=4, root_chord=0.025, tip_chord=0.015, span=0.015, sweep=0.010,
                   position=0.110, position_ref="front", roll_offset=45),
            FinSet(name="主翼", count=4, root_chord=0.070, tip_chord=0.035, span=0.050, sweep=0.035),
        ],
        masses=_std_masses(0.44),
    )


def payload_bulge() -> Rocket:
    return Rocket(
        name="大径ペイロード（段付き）",
        body=[
            NoseCone(shape="elliptical", length=0.060, diameter=0.040),
            BodyTube(name="ペイロード部", length=0.090, diameter=0.040),
            Transition(name="絞り", length=0.030, fore_diameter=0.040, aft_diameter=0.025),
            BodyTube(length=0.260, diameter=0.025),
        ],
        fins=[FinSet(root_chord=0.075, tip_chord=0.035, span=0.055, sweep=0.040)],
        masses=[MassItem("ペイロード（センサー等）", 0.015, 0.10)] + _std_masses(0.44),
    )


def spin() -> Rocket:
    r = standard()
    r.name = "スピン（フィン取付角 2°）"
    r.fins = [FinSet(count=4, cant=2.0)]
    return r


def swept_freeform() -> Rocket:
    return Rocket(
        name="自由形状（後退翼＋鉛筆ノーズ）",
        body=[
            FreeformBody(name="鉛筆型ノーズ", points=[[0, 0], [0.004, 0.004], [0.045, 0.025], [0.05, 0.025]]),
            BodyTube(length=0.330, diameter=0.025),
        ],
        fins=[FinSet(name="後退翼", shape="freeform",
                     points=[[0, 0], [0.055, 0.040], [0.080, 0.045], [0.075, 0.030], [0.050, 0.0]],
                     position=0.0)],
        masses=_std_masses(0.38),
    )


def unstable() -> Rocket:
    r = standard()
    r.name = "不安定な例（フィンが小さい）"
    r.fins = [FinSet(root_chord=0.030, tip_chord=0.015, span=0.015, sweep=0.010)]
    r.masses = [MassItem("エンジンマウント（重い）", 0.020, 0.33)]
    return r


def sphere() -> Rocket:
    return Rocket(
        name="球体型",
        body=[
            Ellipsoid(name="球体", length=0.090, diameter=0.090, thickness=0.0006),
            BodyTube(name="モーターチューブ", length=0.120, diameter=0.020),
        ],
        fins=[FinSet(count=4, root_chord=0.080, tip_chord=0.050, span=0.060, sweep=0.020, roll_offset=45)],
        masses=[MassItem("パラシュート（球の中）", 0.008, 0.045), MassItem("機首おもり", 0.015, 0.010)],
    )


def airplane() -> Rocket:
    return Rocket(
        name="飛行機型",
        body=[NoseCone(length=0.070, diameter=0.025), BodyTube(name="胴体", length=0.350, diameter=0.025)],
        fins=[
            FinSet(name="主翼", count=2, roll_offset=90, root_chord=0.080, tip_chord=0.040, span=0.120,
                   sweep=0.045, thickness=0.002, profile="airfoil", position=0.230, position_ref="front"),
            FinSet(name="水平尾翼", count=2, roll_offset=90, root_chord=0.050, tip_chord=0.030, span=0.050,
                   sweep=0.020, position=0.0),
            FinSet(name="垂直尾翼", count=1, roll_offset=0, root_chord=0.065, tip_chord=0.030, span=0.055,
                   sweep=0.035, position=0.0),
        ],
        masses=[MassItem("機首おもり", 0.012, 0.050)] + _std_masses(0.42),
    )


def wide_cluster() -> Rocket:
    return Rocket(
        name="太胴（クラスター用 φ50）",
        body=[NoseCone(length=0.110, diameter=0.050), BodyTube(length=0.400, diameter=0.050)],
        fins=[FinSet(count=4, root_chord=0.090, tip_chord=0.045, span=0.060, sweep=0.045, roll_offset=45)],
        masses=[MassItem("パラシュート・ショックコード", 0.015, 0.20), MassItem("エンジンマウント", 0.015, 0.47),
                MassItem("機首おもり", 0.010, 0.04)],
    )


TEMPLATES = {
    "標準 3 枚フィン": standard,
    "ボートテール＋楕円フィン": boattail_elliptical,
    "カナード付き": canard,
    "大径ペイロード（段付き）": payload_bulge,
    "スピン（フィン取付角 2°）": spin,
    "自由形状（後退翼＋鉛筆ノーズ）": swept_freeform,
    "太胴（クラスター用 φ50）": wide_cluster,
    "球体型": sphere,
    "飛行機型": airplane,
    "不安定な例（フィンが小さい）": unstable,
}
