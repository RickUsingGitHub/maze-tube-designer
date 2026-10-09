"""Geometry self-tests: watertight meshes and a working maze fit.

Run:  .venv\\Scripts\\python -m tests.test_geometry
"""

import itertools
import math
import os
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mazetube.geometry import TubeDesign, edge_check, mesh_volume, print_plate  # noqa: E402
from mazetube.maze import ALGORITHMS, generate  # noqa: E402
from mazetube.params import TubeParams  # noqa: E402
from mazetube.stl import write_stl  # noqa: E402


def check_maze_is_tree():
    for algo, (rows, cols) in itertools.product(ALGORITHMS, [(5, 2), (8, 7), (12, 10), (3, 3)]):
        m = generate(rows, cols, algo, seed=7)
        n_open = int(m.h_open.sum() + m.v_open.sum())
        assert n_open == rows * cols - 1, (algo, n_open)
        dist, _ = m.bfs((0, 0))
        assert (dist >= 0).all(), f"{algo}: maze not connected"
        assert m.solution[0] == (0, m.home) and m.solution[-1] == (rows - 1, m.entry)
    # determinism
    a = generate(9, 8, "wilson", seed=99)
    b = generate(9, 8, "wilson", seed=99)
    assert (a.h_open == b.h_open).all() and (a.v_open == b.v_open).all()
    print("maze: spanning trees, connected, deterministic  OK")


def check_design(p: TubeParams, label: str, walls=True):
    t0 = time.perf_counter()
    d = TubeDesign(p)
    assert not d.errors, d.errors
    bv, bf = d.body_mesh()
    cv, cf = d.cap_mesh()
    t1 = time.perf_counter()
    for nm, (v, f) in (("body", (bv, bf)), ("cap", (cv, cf))):
        ok, msg = edge_check(f)
        assert ok, f"{label} {nm}: {msg}"
        assert mesh_volume(v, f) > 0
        # no two distinct vertices at the same position (STL welds by position)
        q = np.round(v / 1e-5).astype(np.int64)
        assert len(np.unique(q, axis=0)) == len(v), f"{label} {nm}: coincident vertices"
        # no degenerate triangles
        a = np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)
        assert a.min() > 1e-9, f"{label} {nm}: degenerate triangle"

    # the cap must travel along the whole solution without touching
    gap, snap = d.check_solution_fit()
    assert gap > 0.9 * min(d.c, EPS_OK), f"{label}: solution path collides (gap {gap:.3f})"
    if d.vase:
        # spiral vase: every layer-to-layer step of the outer wall must be <= 45°
        for nm, (th, rr, zz) in (("body", d.body_profile), ("cap", d.cap_profile)):
            dz = np.abs(np.diff(zz))
            assert dz.min() > 1e-9, f"{label} {nm}: horizontal step in vase profile"
            slope = np.abs(np.diff(rr, axis=1)) / dz
            assert slope.max() <= 1.0 + 1e-6, f"{label} {nm}: {slope.max():.3f} steeper than 45°"
        for nm, (v, f) in zip(("body shell", "cap shell"), d.display_meshes()):
            ok, msg = edge_check(f)
            assert ok and mesh_volume(v, f) > 0, f"{label} {nm}: {msg}"
    if d.detent_h > 0:
        expect = d.detent_h - d.c
        assert 0.8 * expect < snap <= expect + 1e-6, f"{label}: detent overlap {snap:.3f} != {expect:.3f}"
        # the nub rests free in the pocket and in the row-0 cell above the ridge
        assert d.min_gap(0.0, 0.0, 24, 64) > 0.9 * min(d.c, EPS_OK), f"{label}: pocket too tight"
        assert d.min_gap(0.0, d.notch, 24, 64) > 0.9 * min(d.c, EPS_OK), f"{label}: ridge hits row 0"
    else:
        assert snap == 0.0

    # ... and must be blocked by every closed wall
    blocked = 0
    if walls:
        mz = d.maze
        for r in range(mz.rows):
            for c in range(mz.cols):
                Z = d.notch + r * d.row_pitch
                phi = (c - mz.home) * d.P_th
                # sideways towards c+1
                if not mz.h_open[r, c]:
                    assert d.min_gap(phi + 0.5 * d.P_th, Z) < 0, f"{label}: h wall {r},{c} passable"
                    blocked += 1
                if r + 1 < mz.rows and not mz.v_open[r, c]:
                    assert d.min_gap(phi, Z + 0.5 * d.row_pitch) < 0, f"{label}: v wall {r},{c} passable"
                    blocked += 1
        # cannot rotate out of the home notch
        if d.notch > 0:
            assert d.min_gap(0.5 * d.P_th, 0.0) < 0, "notch does not lock"
    # requested lengths are honoured exactly (or raised to the minimum)
    if p.body_length > 0:
        assert abs(bv[:, 2].max() - d.H_body) < 1e-6 and d.H_body >= p.body_length - 1e-6
    if p.cap_length > 0:
        assert abs(cv[:, 2].max() - d.H_cap) < 1e-6 and d.H_cap >= p.cap_length - 1e-6
    if p.rows == 0:
        assert d.H_body - d.z0 <= p.maze_length + 1e-6, f"{label}: maze longer than requested"
        assert d.H_body - d.z0 + d.row_pitch > p.maze_length, f"{label}: room for another row"
    det = f", detent {snap:.2f} mm" if d.detent_h > 0 else ""
    print(f"{label:28s} body {len(bf):6d} tris, cap {len(cf):6d} tris, "
          f"min gap {gap:.3f} mm{det}, {blocked} walls verified, {t1 - t0:.2f}s")
    return d


EPS_OK = 0.3


def main():
    check_maze_is_tree()
    base = TubeParams()
    d = check_design(base, "default")
    variants = [
        ("1 nub, no chamfer", dict(nubs=1, chamfer_ceilings=False)),
        ("3 nubs, flutes", dict(nubs=3, grip_flutes=30, maze_diameter=50)),
        ("no notch, prim, loops", dict(notch_depth=0, algorithm="prim", loops=10)),
        ("kruskal, bias 3", dict(algorithm="kruskal", horizontal_bias=3.0, rows=12)),
        ("wilson, forced cols", dict(algorithm="wilson", columns=12, rows=5)),
        ("small tight", dict(maze_diameter=24, path_width=3.0, wall_width=1.5,
                             groove_depth=1.2, clearance=0.2, nubs=1, rows=5)),
        ("big 4 nubs", dict(maze_diameter=80, nubs=4, rows=14, grip_flutes=48)),
        ("detent off", dict(detent=0.0)),
        ("1 nub detent", dict(nubs=1, detent=0.25)),
        ("detent, notch 0", dict(notch_depth=0.0, detent=0.35)),
        ("detent, no chamfer", dict(chamfer_ceilings=False, detent=0.2)),
        ("vase", dict(vase_mode=True, path_width=4.8)),
        ("vase 3 nubs (flutes off)", dict(vase_mode=True, path_width=5.0, nubs=3,
                                          maze_diameter=50, grip_flutes=30)),
        ("vase 1 nub, no detent", dict(vase_mode=True, path_width=4.8, nubs=1, detent=0.0)),
        ("vase shallow 0.8 wall", dict(vase_mode=True, groove_depth=1.2, path_width=4.0,
                                       vase_wall=0.8, chamfer_ceilings=False)),
        ("tall 250/250, 100 maze", dict(body_length=250, cap_length=250, rows=0, maze_length=100)),
        ("tall vase", dict(body_length=250, cap_length=250, rows=0, maze_length=100,
                           vase_mode=True, path_width=4.8)),
        ("tall, straight bore", dict(body_length=180, cap_length=120, rows=0, maze_length=60,
                                     plain_wall=0.0, grip_flutes=24)),
        ("lengths too short", dict(body_length=20, cap_length=10)),
    ]
    for label, kw in variants:
        p = base.copy()
        for k, v in kw.items():
            setattr(p, k, v)
        check_design(p, label)

    # export smoke test
    tmp = tempfile.mkdtemp()
    v, f = print_plate(d)
    ok, msg = edge_check(f)
    assert ok, msg
    path = os.path.join(tmp, "plate.stl")
    write_stl(path, v, f)
    size = os.path.getsize(path)
    assert size == 84 + 50 * len(f)
    print(f"STL export OK ({size / 1e6:.2f} MB, {len(f)} triangles) -> {path}")
    print("\n".join(f"  {k}: {val}" for k, val in d.info().items()))
    check_persistence()
    print("ALL TESTS PASSED")


def check_persistence():
    from mazetube.params import load_design, save_design

    p = TubeParams(rows=6, seed=4242, algorithm="kruskal", name="roundtrip")
    d = TubeDesign(p)
    path = os.path.join(tempfile.mkdtemp(), "t.mazetube")
    save_design(path, p, d.maze, d.maze_key())
    p2, m2, k2 = load_design(path)
    assert p2 == p
    d2 = TubeDesign(p2, m2, k2)
    assert d2.maze is m2, "stored maze not reused"
    assert (d2.maze.h_open == d.maze.h_open).all() and d2.maze.home == d.maze.home
    assert d2.maze.solution == d.maze.solution
    print("save/load round trip OK")


if __name__ == "__main__":
    main()
