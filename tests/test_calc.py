import copy
import math
import unittest

import numpy as np

from rocketcalc import templates
from rocketcalc.aero import analyze
from rocketcalc.components import BodyTube, Ellipsoid, FinSet, FreeformBody, NoseCone, Rocket
from rocketcalc.design import Design, LaunchConfig, RecoveryConfig, descent_rate, required_chute_diameter
from rocketcalc.flight import optimal_delay, simulate
from rocketcalc.motors import builtin_motors, parse_eng


def make_design(rocket=None, motor="Estes C6", **launch):
    return Design(rocket or templates.standard(), builtin_motors()[motor], 0.005,
                  LaunchConfig(**launch), RecoveryConfig())


class TestAero(unittest.TestCase):
    def test_matches_barrowman_hand_calculation(self):
        # 台形フィン・オジャイブの手計算（Barrowman）: ノーズ CNα=2 @≈0.466L、フィン CNα≈19.30 @335mm
        a = analyze(templates.standard())
        nose, fin = a.parts
        self.assertAlmostEqual(nose.cn_alpha, 2.0, places=6)
        self.assertAlmostEqual(nose.x / 0.07, 0.466, delta=0.01)
        self.assertAlmostEqual(fin.cn_alpha, 19.30, places=1)
        self.assertAlmostEqual(fin.x * 1000, 335.0, delta=0.5)

    def test_cone_cp_at_two_thirds(self):
        r = Rocket(body=[NoseCone(shape="cone", length=0.1, diameter=0.02), BodyTube(length=0.2, diameter=0.02)])
        self.assertAlmostEqual(analyze(r).parts[0].x, 0.1 * 2 / 3, places=4)

    def test_freeform_equals_parametric(self):
        cone = Rocket(body=[NoseCone(shape="cone", length=0.1, diameter=0.02), BodyTube(length=0.2, diameter=0.02)],
                      fins=[FinSet()])
        free = Rocket(body=[FreeformBody(points=[[0, 0], [0.1, 0.02]]), BodyTube(length=0.2, diameter=0.02)],
                      fins=[FinSet(shape="freeform", points=[[0, 0], [0.03, 0.045], [0.06, 0.045], [0.06, 0]])])
        self.assertAlmostEqual(analyze(cone).x_cp, analyze(free).x_cp, places=4)

    def test_boattail_has_negative_lift(self):
        r = templates.boattail_elliptical()
        bt = [p for p in analyze(r).parts if p.name == "ボートテール"][0]
        self.assertLess(bt.cn_alpha, 0)

    def test_step_up_is_counted(self):
        a = analyze(templates.payload_bulge())
        self.assertTrue(any(p.name == "絞り" and p.cn_alpha < 0 for p in a.parts))

    def test_drag_reasonable(self):
        cd = analyze(templates.standard()).cd(50, 0.15)
        self.assertTrue(0.4 < cd < 0.9, cd)

    def test_body_lift_moves_cp_forward(self):
        a = analyze(templates.standard())
        _, x0 = a.normal(math.radians(1))
        _, x20 = a.normal(math.radians(20))
        self.assertLess(x20, x0)

    def test_fin_outside_body_rejected(self):
        r = templates.standard()
        r.fins[0].position = 0.35
        with self.assertRaises(ValueError):
            r.validate()


class TestMass(unittest.TestCase):
    def test_override(self):
        r = templates.standard()
        r.override_mass, r.override_cg = 0.050, 0.21
        p = r.dry_mass_props()
        self.assertEqual((p.mass, p.cg), (0.050, 0.21))

    def test_roundtrip_dict(self):
        r = templates.payload_bulge()
        r2 = Rocket.from_dict(copy.deepcopy(r.to_dict()))
        self.assertAlmostEqual(analyze(r).x_cp, analyze(r2).x_cp)
        self.assertAlmostEqual(r.dry_mass_props().mass, r2.dry_mass_props().mass)


class TestMotors(unittest.TestCase):
    def test_builtin_classes(self):
        classes = {n: m.impulse_class for n, m in builtin_motors().items()}
        self.assertEqual(classes, {"Estes A8": "A", "Estes B6": "B", "Estes C6": "C", "Estes D12": "D"})

    def test_eng_roundtrip(self):
        m = builtin_motors()["Estes C6"]
        m2 = parse_eng(m.to_eng())[0]
        self.assertAlmostEqual(m.total_impulse, m2.total_impulse, places=3)


class TestFlight(unittest.TestCase):
    def test_all_templates_fly(self):
        for name, fn in templates.TEMPLATES.items():
            r = simulate(make_design(fn()))
            self.assertTrue(r.lifted, name)
            self.assertTrue(math.isfinite(r.landing_time), name)

    def test_stable_rocket_flies_straight_without_wind(self):
        r = simulate(make_design(rod_angle_deg=0, wind_speed=0, turbulence=0), deploy=False, stop_at_apogee=True)
        self.assertLess(r.max_alpha, 0.5)
        self.assertLess(r.tilt_apogee, 1.0)
        self.assertLess(abs(r.pos[-1, 0]), 0.5)

    def test_unstable_rocket_tumbles(self):
        r = simulate(make_design(templates.unstable()))
        self.assertGreater(r.max_alpha, 90)

    def test_weathercocks_into_wind(self):
        r = simulate(make_design(rod_angle_deg=0, wind_speed=5, turbulence=0), deploy=False, stop_at_apogee=True)
        self.assertLess(r.pos[-1, 0], 0)  # 風上（-x）へ向かう

    def test_canted_fins_spin(self):
        r = simulate(make_design(templates.spin()), deploy=False, stop_at_apogee=True)
        self.assertGreater(r.max_roll_rate, 5)
        r0 = simulate(make_design(), deploy=False, stop_at_apogee=True)
        self.assertLess(r0.max_roll_rate, 0.01)

    def test_bigger_motor_flies_higher(self):
        ap = [simulate(make_design(motor=m), deploy=False, stop_at_apogee=True).apogee
              for m in ("Estes A8", "Estes B6", "Estes C6")]
        self.assertEqual(ap, sorted(ap))

    def test_vertical_no_drag_matches_ballistics(self):
        r0 = templates.standard()
        r0.cd_override = 1e-9
        d = make_design(r0, rod_angle_deg=0, wind_speed=0, turbulence=0)
        r = simulate(d, deploy=False, stop_at_apogee=True)
        i = int((r.t >= d.motor.burn_time).argmax())
        expected = r.pos[i, 2] + r.speed[i] ** 2 / (2 * 9.80665)
        self.assertAlmostEqual(r.apogee, expected, delta=expected * 0.01)

    def test_optimal_delay_ejects_near_apogee(self):
        d = make_design()
        d.recovery.ejection_delay = optimal_delay(d)
        r = simulate(d)
        self.assertAlmostEqual(r.ejection_altitude, r.apogee, delta=0.5)

    def test_heavy_rocket_does_not_lift(self):
        rk = templates.standard()
        rk.override_mass = 1.0
        self.assertFalse(simulate(make_design(rk, motor="Estes A8")).lifted)


class TestSpecialShapes(unittest.TestCase):
    def test_sphere_has_no_lift_and_sphere_drag(self):
        r = Rocket(body=[Ellipsoid(length=0.1, diameter=0.1)])
        a = analyze(r)
        self.assertEqual(a.parts, [])
        self.assertAlmostEqual(a.drag(10, 0.03)["球・楕円体の形状抗力"], 0.47, places=3)

    def test_sphere_template_stable_by_length(self):
        from rocketcalc.design import analyze_stability, judge_margin
        d = make_design(templates.sphere())
        st = analyze_stability(d)
        self.assertEqual(judge_margin(st.margin_launch, st.diameter, d.rocket.length)[1], "ok")
        self.assertLess(simulate(d).max_alpha, 20)

    def test_airplane_pitch_and_yaw_differ(self):
        a = analyze(templates.airplane())
        self.assertFalse(a.symmetric)
        wing = [p for p in a.parts if p.name == "主翼"][0]
        self.assertAlmostEqual(wing.cn_yaw, 0.0, places=9)   # 水平な主翼は横風に効かない
        vtail = [p for p in a.parts if p.name == "垂直尾翼"][0]
        self.assertAlmostEqual(vtail.cn_alpha, 0.0, places=9)  # 垂直尾翼は上下の気流に効かない

    def test_airplane_flies(self):
        r = simulate(make_design(templates.airplane()))
        self.assertTrue(r.lifted)
        self.assertLess(r.max_alpha, 20)

    def test_wing_incidence_bends_path(self):
        base = templates.airplane()
        lift = templates.airplane()
        lift.fins[0].incidence = 4.0
        kw = dict(rod_angle_deg=0, wind_speed=0, turbulence=0)
        r0 = simulate(make_design(base, **kw), deploy=False, stop_at_apogee=True)
        r1 = simulate(make_design(lift, **kw), deploy=False, stop_at_apogee=True)
        self.assertLess(abs(r0.pos[-1, 0]) + abs(r0.pos[-1, 1]), 1.0)
        self.assertGreater(r1.tilt_apogee, r0.tilt_apogee + 5)


class TestRecoverySequence(unittest.TestCase):
    def test_stages_in_order(self):
        d = make_design()
        r = simulate(d)
        self.assertAlmostEqual(r.separation_time - r.ejection_time,
                               d.recovery.shock_cord / d.recovery.ejection_speed, delta=0.01)
        self.assertGreater(r.chute_open_time, r.separation_time)
        self.assertAlmostEqual(r.deploy.max(), 2.0)
        self.assertTrue((np.diff(r.deploy) >= -1e-12).all())
        self.assertGreater(r.opening_shock, 1.0)

    def test_early_ejection_gives_bigger_shock(self):
        good = make_design()
        good.recovery.ejection_delay = optimal_delay(good)
        early = make_design()
        early.recovery.ejection_delay = 0.0
        self.assertGreater(simulate(early).opening_shock, 3 * simulate(good).opening_shock)


class TestInitialAcceleration(unittest.TestCase):
    def test_initial_acceleration(self):
        d = make_design()
        r = simulate(d, deploy=False, stop_at_apogee=True)
        self.assertLess(r.liftoff_time, r.rod_exit_time)
        # ロッド上の平均加速度 × 時間 ≈ 離脱速度
        self.assertAlmostEqual(r.rod_accel_avg * (r.rod_exit_time - r.liftoff_time), r.rod_exit_speed, places=6)
        # ロッド上の最大加速度は (最大推力 − 重量) / 質量 とほぼ一致（低速なので抗力は小さい）
        m0 = d.rocket.dry_mass_props().mass + d.motor.total_mass
        est = d.motor.max_thrust / m0 - 9.80665
        self.assertAlmostEqual(r.rod_accel_max, est, delta=est * 0.05)


class TestStabilityRatio(unittest.TestCase):
    def test_ratio_definition(self):
        from rocketcalc.design import analyze_stability, judge_ratio
        d = make_design()
        st = analyze_stability(d)
        expected = (st.cp - st.launch.cg) / d.rocket.length * 100
        self.assertAlmostEqual(st.ratio(st.margin_launch), expected, places=9)
        self.assertEqual(judge_ratio(15)[1], "ok")
        self.assertEqual(judge_ratio(-1)[1], "bad")


class TestCluster(unittest.TestCase):
    def setUp(self):
        from rocketcalc.motors import MotorCluster
        self.MC = MotorCluster
        self.c6 = builtin_motors()["Estes C6"]

    def test_sums_and_symmetry(self):
        cl = self.MC.build(self.c6, 3)
        self.assertAlmostEqual(cl.total_impulse, 3 * self.c6.total_impulse, places=6)
        self.assertAlmostEqual(cl.max_thrust, 3 * self.c6.max_thrust, places=6)
        self.assertAlmostEqual(cl.mass(0), 3 * self.c6.total_mass)
        my, mz = cl.thrust_moment(0.2)
        self.assertAlmostEqual(my, 0.0, places=9)   # 対称に並べれば偏りなし
        self.assertAlmostEqual(mz, 0.0, places=9)

    def test_single_matches_plain_motor(self):
        rk = templates.standard()
        r1 = simulate(make_design(rk), deploy=False, stop_at_apogee=True)
        d = make_design(rk)
        d.motor = self.MC.build(self.c6)
        r2 = simulate(d, deploy=False, stop_at_apogee=True)
        self.assertAlmostEqual(r1.apogee, r2.apogee, delta=0.5)

    def test_overlap_rejected(self):
        with self.assertRaises(ValueError):
            self.MC.build(self.c6, 3, radius=0.005)

    def test_failed_motor_bends_path(self):
        kw = dict(rod_angle_deg=0, wind_speed=0, turbulence=0)
        ok = make_design(templates.wide_cluster(), **kw)
        ok.motor = self.MC.build(self.c6, 3)
        bad = make_design(templates.wide_cluster(), **kw)
        bad.motor = self.MC.build(self.c6, 3, failed=1)
        r_ok = simulate(ok, deploy=False, stop_at_apogee=True)
        r_bad = simulate(bad, deploy=False, stop_at_apogee=True)
        self.assertLess(r_ok.tilt_apogee, 2)
        self.assertGreater(r_bad.tilt_apogee, 20)
        self.assertLess(r_bad.apogee, r_ok.apogee)
        self.assertAlmostEqual(bad.motor.mass(10), bad.motor.empty_mass)  # 不点火の 1 本は重りとして残る
        self.assertGreater(bad.motor.empty_mass, ok.motor.empty_mass)


class TestRecovery(unittest.TestCase):
    def test_inverse(self):
        dia = required_chute_diameter(0.05, 4.0, 0.8)
        self.assertAlmostEqual(descent_rate(0.05, dia, 0.8), 4.0)


if __name__ == "__main__":
    unittest.main()
