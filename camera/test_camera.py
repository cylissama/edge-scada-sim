import math
import random

import camera_sim as cam


def rvec_to_matrix(r):
    angle = math.sqrt(sum(v * v for v in r))
    x, y, z = (v / angle for v in r)
    c, s, t = math.cos(angle), math.sin(angle), 1 - math.cos(angle)
    return [[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
            [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
            [t * x * z - s * y, t * y * z + s * x, t * z * z + c]]


def test_rvec_round_trips_including_180_degrees():
    random.seed(0)
    cases = [cam.rot_x(math.pi), cam.matmul(cam.rot_z(1.0), cam.rot_x(math.pi))]
    cases += [cam.matmul(cam.rot_z(random.uniform(-3, 3)), cam.rot_x(random.uniform(-3, 3))) for _ in range(50)]
    for m in cases:
        back = rvec_to_matrix(cam.rotation_to_rvec(m))
        assert all(math.isclose(back[i][j], m[i][j], abs_tol=1e-9) for i in range(3) for j in range(3))


def test_loop_is_closed_and_continuous():
    step = 0.01
    pts = [cam.point_on_loop(i * step) for i in range(int(cam.PERIMETER / step) + 2)]
    assert all(math.dist(a[:2], b[:2]) <= step * 1.001 for a, b in zip(pts, pts[1:], strict=False))
    assert math.dist(cam.point_on_loop(0)[:2], cam.point_on_loop(cam.PERIMETER)[:2]) < 1e-9


def test_observed_poses_follow_a_path():
    random.seed(1)
    dt = 1 / cam.FPS
    prev, visible, hidden = {}, 0, 0
    for frame in range(int(cam.PERIMETER / cam.SPEED / dt)):   # one full lap
        for m in cam.observe(frame * dt):
            if not m["visible"]:
                hidden += 1
                prev.pop(m["marker_id"], None)
                continue
            visible += 1
            p = (m["tvec"]["x"], m["tvec"]["y"])
            if m["marker_id"] in prev:  # no jumps: moved about SPEED*dt, plus noise
                assert math.dist(p, prev[m["marker_id"]]) < cam.SPEED * dt + 0.02
            prev[m["marker_id"]] = p
    assert visible > hidden > 0  # mostly seen, but leaves the field of view at the loop ends
