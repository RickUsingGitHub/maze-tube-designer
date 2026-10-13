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

import mazetube.geometry as geometry  # noqa: E402
from mazetube.geometry import (LOCK_TURN, TubeDesign, edge_check, mesh_volume,  # noqa: E402
                               print_plate, print_plate_text)
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
    pieces = [(pt.name, (pt.verts, pt.faces)) for pt in d.parts()]
    if d.three:
        pieces.append(("whole cap", (cv, cf)))
    for pt in d.parts():
        if pt.inlay is not None:
            pieces.append((pt.name + " letters", pt.inlay))
            r = np.hypot(pt.inlay[0][:, 0], pt.inlay[0][:, 1])
            assert r.max() <= d.R_out + 1e-9, f"{label} {pt.name}: letters stand proud"
            assert r.min() >= d.R_out - 1.5 - p.grip_depth - 1e-9, f"{label} {pt.name}: letters too deep"
    for nm, (v, f) in pieces:
        ok, msg = edge_check(f)
        assert ok, f"{label} {nm}: {msg}"
        assert mesh_volume(v, f) > 0
        # no two distinct vertices at the same position (STL welds by position)
        q = np.round(v / 1e-5).astype(np.int64)
        assert len(np.unique(q, axis=0)) == len(v), f"{label} {nm}: coincident vertices"
        # no degenerate triangles
        a = np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)
        assert a.min() > 1e-9, f"{label} {nm}: degenerate triangle"

    # the body's mesh really is the designed surface: between every two grid lines the built
    # wall lies on body_radius().  (The fit check below works on that function, so a mesh
    # that strays from it - a slope stretched over a whole row, say - would go unnoticed.)
    th, rr, zz = d.profiles["body"]
    k1 = int(np.argmax(np.abs(zz - d.H_body) < 1e-9))
    k0 = int(np.where(np.abs(zz[:k1] - (d.z_seat - d.w_v)) < 1e-9)[0][-1])
    assert 0 < k0 < k1 and np.all(np.diff(zz[k0:k1 + 1]) > 0), f"{label}: maze columns not found"
    for k in range(k0, k1):
        if zz[k + 1] - zz[k] > 2.2 * geometry.EPS:           # not across a step
            zm = np.full(len(th), 0.5 * (zz[k] + zz[k + 1]))
            off = np.abs(d.body_radius(th, zm) - 0.5 * (rr[:, k] + rr[:, k + 1])).max()
            assert off < 1e-6, f"{label}: mesh is {off:.3f} mm off the design at z = {zm[0]:.2f}"
    gap_th = np.diff(np.append(th, th[0] + 2 * np.pi)) * d.r_maze
    wide = gap_th > 2.2 * geometry.EPS
    thm = (th + 0.5 * gap_th / d.r_maze)[wide]
    for k in range(k0, k1 + 1):
        mid = 0.5 * (rr[:, k] + np.roll(rr[:, k], -1))[wide]
        off = np.abs(d.body_radius(thm, np.full(len(thm), zz[k])) - mid).max()
        assert off < 0.08, f"{label}: mesh is {off:.3f} mm off the design round z = {zz[k]:.2f}"

    # the cap must travel along the whole solution without touching
    gap, snap = d.check_solution_fit()
    assert gap > 0.9 * min(d.c, EPS_OK), f"{label}: solution path collides (gap {gap:.3f})"
    if d.vase:
        # spiral vase: every layer-to-layer step of the outer wall must be <= 45°
        for pt in d.parts():
            th, rr, zz = d.profiles[pt.name]
            dz = np.abs(np.diff(zz))
            assert dz.min() > 1e-9, f"{label} {pt.name}: horizontal step in vase profile"
            slope = np.abs(np.diff(rr, axis=1)) / dz
            assert slope.max() <= 1.0 + 1e-6, f"{label} {pt.name}: {slope.max():.3f} steeper than 45°"
    for pt in d.display_parts():                       # what the 3D view shows
        ok, msg = edge_check(pt.faces)
        assert ok and mesh_volume(pt.verts, pt.faces) > 0, f"{label} {pt.name} (display): {msg}"
    heights = d.part_heights()
    for pt in d.parts():                               # print orientation: standing on the bed
        pv = pt.print_verts()
        assert abs(pv[:, 2].min()) < 1e-6, f"{label} {pt.name}: not on the bed"
        assert abs(pv[:, 2].max() - heights[pt.name]) < 1e-6, f"{label} {pt.name}: wrong height"
    free = 0.9 * min(d.c, EPS_OK)
    home_phi = -LOCK_TURN * d.lock_cols * d.P_th       # cap angle with the nubs in the home column
    if d.detent_h > 0:
        expect = d.detent_h - d.c
        assert 0.8 * expect < snap <= expect + 1e-6, f"{label}: detent overlap {snap:.3f} != {expect:.3f}"
        # the nub rests free in its pocket, and the ridge leaves the home column clear
        assert d.min_gap(0.0, 0.0, 64, 24) > free, f"{label}: pocket too tight"
        assert d.min_gap(home_phi, 0.0, 64, 24) > free, f"{label}: ridge reaches the home column"
        assert d.min_gap(home_phi, d.notch, 64, 24) > free, f"{label}: ridge hits row 0"
    else:
        assert snap == 0.0
    if d.lock_cols:
        # locked: the cap cannot be pulled off or turned further, only turned back as far as home
        assert d.min_gap(0.0, 0.5 * d.notch) < 0, f"{label}: the locked cap pulls straight off"
        assert d.min_gap(LOCK_TURN * 0.5 * d.P_th, 0.0) < 0, f"{label}: turns past its pocket"
        assert d.min_gap(home_phi - LOCK_TURN * 0.5 * d.P_th, 0.0) < 0, f"{label}: turns past home"

    # ... and must be blocked by every closed wall
    blocked = 0
    if walls:
        mz = d.maze
        for r in range(mz.rows):
            for c in range(mz.cols):
                Z = d.notch + r * d.row_pitch
                phi = home_phi + (c - mz.home) * d.P_th
                # sideways towards c+1
                if not mz.h_open[r, c]:
                    assert d.min_gap(phi + 0.5 * d.P_th, Z) < 0, f"{label}: h wall {r},{c} passable"
                    blocked += 1
                if r + 1 < mz.rows and not mz.v_open[r, c]:
                    assert d.min_gap(phi, Z + 0.5 * d.row_pitch) < 0, f"{label}: v wall {r},{c} passable"
                    blocked += 1
        # cannot rotate out of the home notch
        if d.notch > 0 and not d.lock_cols:
            assert d.min_gap(0.5 * d.P_th, 0.0) < 0, "notch does not lock"
    if p.auto_size:
        # sized from the contents: they must fit, and both parts must fit the printer
        bore = 2 * ((d.r_floor - d.w_v) if d.vase else d.r_cav)
        storage = d.z0 + d.H_ci - p.bottom_thickness
        assert bore >= p.content_diameter + p.fit_slack - 1e-6, f"{label}: bore too small"
        assert storage >= p.content_length + p.fit_slack - 1e-6, f"{label}: too short inside"
        if not d.bands:        # text may lengthen the cap or the grip band on purpose
            assert storage <= p.content_length + p.fit_slack + d.row_pitch + 12, f"{label}: wasteful"
        assert max(d.part_heights().values()) <= p.printer_height + 0.05, f"{label}: taller than printer"
        assert d.z0 >= p.base_height - 1e-6, f"{label}: grip band lost"
    # requested lengths are honoured exactly (or raised to the minimum)
    if p.auto_size:
        pass
    elif p.body_length > 0:
        assert abs(bv[:, 2].max() - d.H_body) < 1e-6 and d.H_body >= p.body_length - 1e-6
    if not p.auto_size and p.cap_length > 0:
        assert abs(cv[:, 2].max() - d.H_cap) < 1e-6 and d.H_cap >= p.cap_length - 1e-6
    if not p.auto_size and p.rows == 0:
        assert d.H_body - d.z0 <= p.maze_length + 1e-6, f"{label}: maze longer than requested"
        assert d.H_body - d.z0 + d.row_pitch > p.maze_length, f"{label}: room for another row"
    det = f", detent {snap:.2f} mm" if d.detent_h > 0 else ""
    if d.three:
        det += ", 3 pieces"
    print(f"{label:28s} body {len(bf):6d} tris, cap {len(cf):6d} tris, "
          f"min gap {gap:.3f} mm{det}, {blocked} walls verified, {t1 - t0:.2f}s")
    return d


EPS_OK = 0.3


def main():
    # the summary uses a few non-cp1252 symbols; line buffering keeps progress visible
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    check_maze_is_tree()
    # explicit-dimension designs (the settings every older design file carries)
    base = TubeParams(auto_size=False, rows=8, nubs=2, detent=0.3)
    d = check_design(base, "explicit sizes")
    variants = [
        ("1 nub, no chamfer", dict(nubs=1, chamfer_ceilings=False)),
        ("3 nubs, flutes", dict(nubs=3, grip_flutes=30, maze_diameter=50)),
        ("no notch, prim, loops", dict(notch_depth=0, algorithm="prim", loops=10)),
        ("kruskal, bias 3", dict(algorithm="kruskal", horizontal_bias=3.0, rows=12)),
        ("wilson, forced cols", dict(algorithm="wilson", columns=12, rows=5)),
        ("small tight", dict(maze_diameter=24, path_width=3.0, wall_width=1.5,
                             groove_depth=1.2, clearance=0.2, nubs=1, rows=5)),
        ("big 4 nubs", dict(maze_diameter=80, nubs=4, rows=14, grip_flutes=48)),
        ("click off", dict(detent_level=0)),
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

    check_auto_sizing()
    check_three_pieces()
    check_text()
    check_two_colour_text()
    check_bed_and_exports()
    check_map_window()
    check_click()

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


def check_auto_sizing():
    """Contents-driven sizing: defaults, every preset, printer-height limits."""
    from mazetube.presets import all_presets

    d = check_design(TubeParams(), "default (from contents)")
    assert d.n == 3 and d.auto and not d.split

    # every preset builds, fits its contents and fits a 250 mm printer
    count = 0
    for cat, label, dia, length, _ in all_presets():
        for vase in (False, True):
            p = TubeParams(content_diameter=dia, content_length=length, preset=label,
                           vase_mode=vase, path_width=4.8 if vase else 4.0)
            d = TubeDesign(p)
            assert not d.errors, (label, d.errors)
            (bv, bf), (cv, cf) = d.body_mesh(), d.cap_mesh()
            assert edge_check(bf)[0] and edge_check(cf)[0], label
            bore = 2 * ((d.r_floor - d.w_v) if d.vase else d.r_cav)
            assert bore >= dia + p.fit_slack - 1e-6, label
            assert d.z0 + d.H_ci - p.bottom_thickness >= length + p.fit_slack - 1e-6, label
            assert max(d.part_heights().values()) <= 250.05, label
            assert d.split == (length + p.fit_slack + p.bottom_thickness > 250), label
            assert not d.three, label                      # nothing here needs three pieces
            count += 1
    print(f"presets: {count} designs (normal + vase) fit their contents and a 250 mm printer  OK")

    # full checks (walls, fit, detent) on a few sized designs
    wine = dict(content_diameter=77, content_length=300)
    check_design(TubeParams(**wine), "wine bottle, 250 printer")
    d = check_design(TubeParams(**wine, printer_height=180), "wine bottle, 180 printer")
    assert d.M < 15 and not d.three and any("shortened" in n for n in d.notes)   # smaller overlap
    d = check_design(TubeParams(**wine, printer_height=164, pieces=2), "wine, 164 printer, 2 forced")
    assert d.M <= 2 and not d.three and any("shortened" in n for n in d.notes)
    check_design(TubeParams(content_diameter=66, content_length=115, nubs=4), "coke can, 4 nubs")
    check_design(TubeParams(content_diameter=30, content_length=65, vase_mode=True,
                            path_width=4.8), "cash roll, vase")
    check_design(TubeParams(content_diameter=10, content_length=5), "tiny contents")

    # too long for the printer: a clear error, not a broken design
    d = TubeDesign(TubeParams(**wine, printer_height=150, pieces=2))
    assert d.errors and "too long" in d.errors[0], d.errors
    d = TubeDesign(TubeParams(**wine, printer_height=100))
    assert d.errors and "three parts" in d.errors[0], d.errors

    # switching to explicit sizes reproduces the same tube
    a = TubeDesign(TubeParams(**wine))
    q = a.p.copy()
    q.auto_size, q.rows = False, a.M
    q.body_length, q.cap_length = a.H_body, a.H_cap
    b = TubeDesign(q, a.maze, a.maze_key())
    assert np.allclose(a.body_mesh()[0], b.body_mesh()[0]) and np.allclose(a.cap_mesh()[0], b.cap_mesh()[0])

    # design files from before contents-driven sizing keep their explicit dimensions
    legacy = TubeParams.from_dict({"rows": 8, "nubs": 2, "maze_diameter": 40.0, "body_length": 250.0})
    assert legacy.auto_size is False and legacy.maze_diameter == 40.0
    print("auto sizing: printer-height limits, explicit round trip, legacy files  OK")


def check_three_pieces():
    """Body + extension + cap: chosen automatically, joint fits, same assembled tube."""
    wine = dict(content_diameter=77, content_length=300)
    d = check_design(TubeParams(**wine, printer_height=160), "wine, 160 printer -> 3 pieces")
    assert d.three and d.M == 15, "three pieces should keep the full maze"
    h = d.part_heights()
    assert abs(h["body"] - h["extension"]) < 0.11 and abs(h["extension"] - h["cap"]) < 1e-6
    parts = {pt.name: pt for pt in d.parts()}
    ext, end = parts["extension"], parts["cap"]
    # the joint: spigot inside socket with a glue gap, and the two never share space
    assert abs((d.R_sk - d.R_sp) - d.cj) < 1e-9 and d.cj > 0
    re, ze = np.hypot(ext.verts[:, 0], ext.verts[:, 1]), ext.verts[:, 2]
    rc, zc = np.hypot(end.verts[:, 0], end.verts[:, 1]), end.verts[:, 2]
    assert abs(ze.max() - d.z_sh) < 1e-9 and abs(zc.min() - (d.z_sh - d.lap)) < 1e-9
    assert re[ze > d.z_s + 1e-6].min() >= d.R_sk - 1e-9            # socket wall
    assert rc[zc < d.z_sh - 1e-6].max() <= d.R_sp + 1e-9           # spigot
    assert abs(re.min() - d.r_tip) < 1e-9, "the extension carries the nubs"
    assert rc[rc > 1e-6].min() >= d.r_cap_in - 1e-9, "the cap end has a plain bore"
    # glued together they are exactly the one-piece cap: same closed length and storage
    two = TubeDesign(TubeParams(**wine, printer_height=400))
    assert not two.three
    assert d.z0 + d.H_ci - d.p.bottom_thickness >= 302 - 1e-6

    check_design(TubeParams(**wine, printer_height=160, vase_mode=True, path_width=5.2),
                 "3 pieces, spiral vase")
    d = check_design(TubeParams(**wine, printer_height=120), "wine, 120 printer")
    assert d.three and d.M < 15
    d = check_design(TubeParams(auto_size=False, rows=8, body_length=200, cap_length=400),
                     "explicit 400 cap -> split")
    assert d.three and max(d.part_heights().values()) <= 250
    d = check_design(TubeParams(auto_size=False, rows=8, body_length=200, cap_length=400, pieces=2),
                     "explicit 400 cap, 2 forced")
    assert not d.three and any("taller than the printer" in w for w in d.warnings)
    d = check_design(TubeParams(pieces=3), "three pieces forced, small")
    assert d.three
    print("three pieces: automatic choice, joint clearance, equal heights  OK")


def check_text():
    """Text on the cap and the base: raised, engraved, in vase mode, on three pieces."""
    plain = TubeDesign(TubeParams())
    v_plain = {pt.name: mesh_volume(pt.verts, pt.faces) for pt in plain.parts()}

    d = check_design(TubeParams(cap_text="Happy Birthday", base_text="Rick"), "text raised")
    assert set(d.bands) == {"cap", "base"}
    cap = d.parts()[1]
    r = np.hypot(cap.verts[:, 0], cap.verts[:, 1])
    assert abs(r.max() - (d.R_out + 0.6)) < 1e-6, "raised text should stand 0.6 mm proud"
    assert mesh_volume(cap.verts, cap.faces) > v_plain["cap"] + 20, "raised text adds material"
    assert d.z0 > plain.z0, "the grip band grows to hold the base text"
    assert d.M == plain.M, "... without costing a maze row"

    d = check_design(TubeParams(cap_text="Happy Birthday", base_text="Rick", text_style=1),
                     "text engraved")
    cap = d.parts()[1]
    r = np.hypot(cap.verts[:, 0], cap.verts[:, 1])
    assert abs(r.max() - d.R_out) < 1e-6 and mesh_volume(cap.verts, cap.faces) < v_plain["cap"] - 20

    d = check_design(TubeParams(cap_text="Happy Birthday", base_text="Rick", vase_mode=True,
                                path_width=5.2, text_style=1), "text, spiral vase")
    assert d.bands["cap"].depth > 0 > d.bands["base"].depth        # cap forced to raised

    # several short lines: stacked, level, centred - and the letters keep their size
    d = check_design(TubeParams(cap_text="Happy\nBirthday\nRick"), "three stacked lines")
    band = d.bands["cap"]
    assert not band.spiral and len(band.layout.lines) == 3
    assert abs(band.layout.cap_height - 6.0) < 0.3
    assert band.height > 3 * band.layout.pitch and band.width < 2 * np.pi * d.R_out

    # a line too long for the tube spirals round and round instead of shrinking
    d = check_design(TubeParams(cap_text="Congratulations on your retirement, Margaret!"),
                     "long line spirals")
    band = d.bands["cap"]
    assert band.spiral and band.turns > 1 and abs(band.layout.cap_height - 6.0) < 0.3
    assert not any("reduced" in n for n in d.notes)

    # a whole poem: the cap grows to hold it, every new line starts at the start meridian
    poem = ("Roses are red, violets are blue,\nthis little tube has a maze just for you.\n"
            "Twist it and turn it, feel your way through,\n\nHappy Birthday!")
    plain_cap = plain.H_cap
    d = check_design(TubeParams(cap_text=poem), "poem spirals, cap grows")
    band, lay = d.bands["cap"], d.bands["cap"].layout
    turn = 2 * np.pi * d.R_out
    assert band.spiral and len(lay.lines) == 5 and abs(lay.cap_height - 6.0) < 0.3
    assert d.H_cap > plain_cap + 20 and any("lengthened" in n for n in d.notes)
    assert all(abs(st / turn - round(st / turn)) < 0.01 for st in lay.line_starts)
    assert lay.line_starts[4] - lay.line_starts[3] > 0.99 * turn, "a blank line leaves a turn empty"
    # the text really is where the layout says: ink on the start meridian side of each line start
    z = band.z_lines()
    for k, st in enumerate(lay.line_starts):
        if not lay.lines[k]:
            continue
        th = band.theta0 + np.linspace(0.5, 12.0, 60) / d.R_out        # first 12 mm of that line
        row_top = band.z_text - lay.pitch * (st / turn)
        zz = z[(z < row_top) & (z > row_top - lay.pitch * 1.15)]
        assert band.sample(th, zz).max() > 0.5, f"line {k} not found at the start meridian"
    # ... and stays inside its band
    assert band.sample(band.theta_lines(), np.array([band.z0, band.z1])).max() == 0.0

    d = check_design(TubeParams(base_text=poem), "poem on the base")
    assert d.bands["base"].spiral and d.z0 > 60 and d.H_body <= 250

    d = check_design(TubeParams(cap_text=poem, vase_mode=True, path_width=5.2), "poem, spiral vase")
    assert d.bands["cap"].spiral

    # on a tube wide enough, the same poem is just stacked lines
    d = check_design(TubeParams(cap_text=poem, content_diameter=77, content_length=300),
                     "poem on a wide tube")
    assert not d.bands["cap"].spiral

    # only when the printer cannot hold it are the letters made smaller
    d = check_design(TubeParams(cap_text=poem, printer_height=75), "poem, 75 mm printer")
    assert d.H_cap <= 75 + 1e-6 and d.bands["cap"].layout.cap_height < 5.5
    assert any("reduced" in n for n in d.notes)

    d = check_design(TubeParams(content_diameter=77, content_length=300, printer_height=160,
                                cap_text="Cheers!", base_text="2026"), "text on three pieces")
    assert d.three and d.bands["cap"].z0 > d.z_sh, "cap text sits on the cap end, above the joint"

    # letter edges are rounded ramps the mesh can follow, not on/off steps (which made
    # curves and diagonals come out as staircases)
    for kw in (dict(cap_text="Happy Birthday"), dict(cap_text="Sphinx of black quartz, judge my vow. "
                                                              "Pack my box with five dozen jugs.")):
        d = TubeDesign(TubeParams(**kw))
        band = d.bands["cap"]
        relief = band.sample(np.sort(band.theta_lines()), band.z_lines())
        assert abs(relief.max() - 0.6) < 0.02, "letters still reach their full height"
        for axis in (0, 1):
            step = np.abs(np.diff(relief, axis=axis)).max()
            assert step <= 0.5 * 0.6, f"letter edge jumps {step:.2f} mm between mesh points"

    d = TubeDesign(TubeParams(cap_text="   "))
    assert not d.bands and not d.warnings
    print("text: raised / engraved / vase / stacked lines / spiral poem / three pieces  OK")


def check_two_colour_text():
    """Flush lettering for a second colour: separate solids that fill their cut-outs exactly."""
    # the cut itself, on a plain drum with a random pattern (every marching-squares case)
    T, K = 40, 30
    th = np.linspace(0.0, 2 * np.pi, T, endpoint=False)
    zz = np.linspace(0.0, 20.0, K)
    drum = geometry._sweep(th, np.full((T, K), 15.0), zz, 0.0, 20.0)
    rng = np.random.default_rng(5)
    seen = set()
    for trial in range(6):
        cover = rng.random((T, 20))
        if trial >= 3:                                   # blobs rather than speckle
            cover = (cover + np.roll(cover, 1, 0) + np.roll(cover, 1, 1) + np.roll(cover, (1, 1), (0, 1))) / 4
            cover = np.clip((cover - 0.5) * 4 + 0.5, 0, 1)
        v, f, letters = geometry._inlay_cut(*drum, T, 5, cover, 0.8)
        lv, lf = letters
        assert edge_check(f)[0] and edge_check(lf)[0]
        vol, lvol = mesh_volume(v, f), mesh_volume(lv, lf)
        assert lvol > 0 and abs(vol + lvol - mesh_volume(*drum)) < 1e-6, "letters must fill the cut exactly"
        assert len(v) == len(drum[0]) + (len(lv) - 2 * int(((cover > 0.5)[:, 1:-1]).sum()))
        inside = cover > 0.5
        inside[:, 0] = inside[:, -1] = False
        i2 = (np.arange(T) + 1) % T
        case = (inside[:, :-1] * 1 + inside[i2][:, :-1] * 2 + inside[i2][:, 1:] * 4 + inside[:, 1:] * 8)
        seen |= set(np.unique(case).tolist())
    assert seen == set(range(16)), f"cases not exercised: {set(range(16)) - seen}"
    assert geometry._inlay_cut(*drum, T, 5, np.zeros((T, 20)), 0.8)[2] is None

    words = dict(cap_text="Happy Birthday", base_text="Rick")
    d = check_design(TubeParams(**words, text_style=2), "text in a second colour")
    body, cap = d.parts()
    assert body.inlay is not None and cap.inlay is not None and set(d.inlays) == {"cap", "base"}
    for pt in (body, cap):
        r = np.hypot(pt.verts[:, 0], pt.verts[:, 1])
        assert abs(r.max() - d.R_out) < 1e-9, "flush: nothing stands out from the wall"
        lr = np.hypot(pt.inlay[0][:, 0], pt.inlay[0][:, 1])
        assert abs(lr.max() - d.R_out) < 1e-9 and abs(lr.min() - (d.R_out - 0.8)) < 1e-9
    band = d.bands["cap"]
    assert band.inlay and abs(band.depth + 0.8) < 1e-9, "deep enough for two printed walls"
    assert "second colour" in d.info()["Text"]
    # piece + letters = the piece as it would be without the cut
    cut = geometry._inlay_cut
    try:
        geometry._inlay_cut = lambda v, f, *a: (v, f, None)
        whole = TubeDesign(TubeParams(**words, text_style=2)).parts()
    finally:
        geometry._inlay_cut = cut
    for pt, uncut in zip((body, cap), whole):
        total = mesh_volume(pt.verts, pt.faces) + mesh_volume(*pt.inlay)
        assert abs(total - mesh_volume(uncut.verts, uncut.faces)) < 1e-6, pt.name
    # the letters are the size of the text: covered area x depth, less the sloping edges
    th, z = np.sort(band.theta_lines()), band.z_lines()
    covered = (np.abs(band.sample(th, z)) / 0.8 > 0.5).mean() * band.width * band.height
    assert 0.75 * covered * 0.8 < mesh_volume(*cap.inlay) < 1.05 * covered * 0.8
    # in print orientation the letters stay inside their piece
    for pt in (body, cap):
        pv, lv = pt.print_verts(), pt.inlay_print_verts()
        assert lv[:, 2].min() > pv[:, 2].min() + 0.5 and lv[:, 2].max() < pv[:, 2].max() - 0.5

    # the Depth setting can make them deeper, never shallower; a thin wall limits it
    assert abs(TubeDesign(TubeParams(**words, text_style=2, text_depth=1.1)).bands["cap"].depth + 1.1) < 1e-9
    d = TubeDesign(TubeParams(**words, text_style=2, auto_size=False, cap_wall=1.4))
    assert abs(d.bands["cap"].depth + 0.6) < 1e-9
    d = TubeDesign(TubeParams(**words, text_style=2, auto_size=False, cap_wall=1.1))
    assert "cap" not in d.bands and any("too thin for second-colour" in w for w in d.warnings)

    poem = ("Roses are red, violets are blue,\nthis little tube has a maze just for you.\n"
            "Twist it and turn it, feel your way through")
    d = check_design(TubeParams(cap_text=poem, text_style=2), "poem in a second colour")
    assert d.bands["cap"].spiral and d.parts()[1].inlay is not None
    d = check_design(TubeParams(content_diameter=77, content_length=300, printer_height=160,
                                cap_text="Cheers!", base_text="2026", text_style=2),
                     "second colour, three pieces")
    body, ext, cap = d.parts()
    assert d.three and body.inlay is not None and ext.inlay is None and cap.inlay is not None
    d = check_design(TubeParams(cap_text="Grip", text_style=2, auto_size=False, grip_flutes=24),
                     "second colour on flutes")
    assert d.parts()[1].inlay is not None

    # spiral vase printing is one continuous wall: no second colour there
    d = check_design(TubeParams(**words, text_style=2, vase_mode=True, path_width=5.2),
                     "second colour asked, vase")
    assert not d.inlays and d.bands["cap"].depth > 0 and any("Second-colour" in n for n in d.notes)

    # designs saved when there was only an "engraved" switch
    assert TubeParams.from_dict({"text_engrave": True}).text_style == 1
    assert TubeParams.from_dict({"text_engrave": False}).text_style == 0
    assert TubeParams.from_dict({"text_style": 7}).text_style == 0
    print("two-colour text: exact cut-outs, flush, 2 walls deep, poem / three pieces / flutes  OK")


def check_bed_and_exports():
    """Bed check, plate layout, 3MF round trip, gift tag."""
    from mazetube.threemf import read_3mf, write_3mf

    magnum = dict(content_diameter=105, content_length=350)
    d = TubeDesign(TubeParams(**magnum, bed_x=100, bed_y=100))
    assert any("wider than the printer bed" in w for w in d.warnings)
    d = TubeDesign(TubeParams(**magnum, bed_x=200, bed_y=125))
    assert not any("wider" in w for w in d.warnings)
    assert any("don't all fit" in n for n in d.notes) and not d.plate_layout()[1]
    d = TubeDesign(TubeParams(**magnum))
    assert d.plate_layout()[1] and not any("bed" in w for w in d.warnings)

    # plate: parts stand on the bed, inside it, without touching each other
    for kw in (dict(), dict(content_diameter=77, content_length=300, printer_height=160)):
        d = TubeDesign(TubeParams(**kw))
        pos, fits = d.plate_layout()
        assert fits and len(pos) == len(d.parts())
        for i, (x, y) in enumerate(pos):
            assert abs(x) + d.R_max <= d.p.bed_x / 2 + 1e-6 and abs(y) + d.R_max <= d.p.bed_y / 2 + 1e-6
            for x2, y2 in pos[i + 1:]:
                assert np.hypot(x - x2, y - y2) >= 2 * d.R_max + 7.9
        v, f = print_plate(d)
        assert edge_check(f)[0] and abs(v[:, 2].min()) < 1e-6

        # 3MF: every piece a separate, closed object at its place on the bed
        path = os.path.join(tempfile.mkdtemp(), "plate.3mf")
        objs = [(pt.name, pt.print_verts(), pt.faces, (x + d.p.bed_x / 2, y + d.p.bed_y / 2, 0.0))
                for pt, (x, y) in zip(d.parts(), pos)]
        write_3mf(path, objs, "test")
        back = read_3mf(path)
        assert [b[0] for b in back] == [pt.name for pt in d.parts()]
        for (name, v, f, at), (_, v0, f0, at0) in zip(back, objs):
            assert edge_check(f)[0] and np.array_equal(f, f0)
            assert np.abs(v - v0).max() < 1e-4 and np.allclose(at, at0, atol=1e-3)
            assert 0 <= at[0] - d.R_max and at[0] + d.R_max <= d.p.bed_x

    # second-colour lettering: each piece and its letters as one object with two parts
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from mazetube.app import plate_objects, write_part_stls
    import zipfile
    d = TubeDesign(TubeParams(cap_text="Hi", base_text="2026", text_style=2))
    tmp = tempfile.mkdtemp()
    objs, fits = plate_objects(d, "t", (d.p.bed_x / 2, d.p.bed_y / 2))
    assert fits and [len(o[4]) for o in objs] == [1, 1]
    path = os.path.join(tmp, "two.3mf")
    write_3mf(path, objs, "test")
    xml = zipfile.ZipFile(path).read("3D/3dmodel.model").decode()
    assert xml.count("<item ") == 2 and xml.count("<components>") == 2 and "<basematerials" in xml
    # the part list Orca-family slicers read: names, and filament 2 for the letters
    import re
    import xml.etree.ElementTree as ET
    cfg = ET.fromstring(zipfile.ZipFile(path).read("Metadata/model_settings.config"))
    items = re.findall(r'<item objectid="(\d+)"', xml)
    assert [o.get("id") for o in cfg.findall("object")] == items
    for o in cfg.findall("object"):
        comps = re.search(rf'<object id="{o.get("id")}"[^>]*>\s*<components>(.*?)</components>', xml, re.S)
        parts = o.findall("part")
        assert [pt.get("id") for pt in parts] == re.findall(r'objectid="(\d+)"', comps.group(1))
        meta = [{m.get("key"): m.get("value") for m in pt.findall("metadata")} for pt in parts]
        assert [m["extruder"] for m in meta] == ["1", "2"] and meta[1]["name"].endswith(" text")
        assert all(pt.get("subtype") == "normal_part" for pt in parts)
    assert 'Extension="config"' in zipfile.ZipFile(path).read("[Content_Types].xml").decode()
    plain_path = os.path.join(tmp, "plain.3mf")
    write_3mf(plain_path, plate_objects(TubeDesign(TubeParams()), "p")[0], "test")
    assert "Metadata/model_settings.config" not in zipfile.ZipFile(plain_path).namelist()
    back = read_3mf(path)
    assert [b[0] for b in back] == ["t body", "t body text", "t cap", "t cap text"]
    for k in (0, 2):
        (_, v, f, at), (_, tv, tf, tat) = back[k], back[k + 1]
        assert edge_check(f)[0] and edge_check(tf)[0] and at == tat, "letters travel with their piece"
        assert (tv.min(axis=0) > v.min(axis=0) - 1e-4).all() and (tv.max(axis=0) < v.max(axis=0) + 1e-4).all()
    pv, pf = print_plate(d)
    tv, tf = print_plate_text(d)
    assert edge_check(tf)[0] and (tv.min(axis=0) >= pv.min(axis=0) - 1e-6).all()
    assert (tv.max(axis=0) <= pv.max(axis=0) + 1e-6).all()
    assert print_plate_text(TubeDesign(TubeParams(cap_text="Hi"))) is None
    cap = d.parts()[1]
    files = write_part_stls(os.path.join(tmp, "x_cap"), cap, "x cap")
    assert [os.path.basename(f) for f in files] == ["x_cap.stl", "x_cap_text.stl"]
    assert os.path.getsize(files[1]) == 84 + 50 * len(cap.inlay[1])
    assert len(write_part_stls(os.path.join(tmp, "y_cap"), TubeDesign(TubeParams()).parts()[1], "y")) == 1

    # gift tag (needs Qt's GUI classes)
    from mazetube.gifttag import save_gift_tag
    tmp = tempfile.mkdtemp()
    d = TubeDesign(TubeParams(cap_text="Happy Birthday"))
    for ext, magic in ((".pdf", b"%PDF"), (".png", b"\x89PNG")):
        path = os.path.join(tmp, "tag" + ext)
        save_gift_tag(path, d, "test", "Happy Birthday")
        data = open(path, "rb").read()
        assert data[:4] == magic and len(data) > 15000, f"gift tag {ext} looks empty"
    assert app is not None
    print("bed check, plate layout, 3MF round trip, two-colour exports, gift tag  OK")


def check_map_window():
    """The unrolled map is cut so the solution never runs off the edge of the page."""
    kinds = {"sector": 0, "turn": 0, "wide": 0}
    wide_seed = None
    for seed in range(1, 81):
        for kw in (dict(), dict(nubs=2), dict(nubs=1), dict(content_diameter=77, content_length=300),
                   dict(nubs=4, content_diameter=60, algorithm="wilson")):
            d = TubeDesign(TubeParams(seed=seed, **kw))
            lo, hi = d.solution_columns()
            col0, n = d.map_window()
            span = hi - lo + 1
            # the highlighted route is always inside the window, with the whole tube shown
            assert col0 <= lo and hi < col0 + n and n >= d.N, (seed, kw)
            ths = np.array([d.nub_position(phi, Z)[0] for phi, Z in d.solution_keyframes(lift=0.0)])
            page = ths - col0 * d.P_th
            assert page.min() > 0 and page.max() < n * d.P_th, (seed, kw)
            if span <= d.Ns:
                # every nub's route stays inside its own sector of the page
                kinds["sector"] += 1
                sector = d.Ns * d.P_th
                assert n == d.N and page.min() // sector == page.max() // sector, (seed, kw)
            elif span <= d.N:
                kinds["turn"] += 1
                assert n == d.N
            else:
                kinds["wide"] += 1                          # winds more than once: wider map
                assert n == span + 2 > d.N
                wide_seed = wide_seed or (seed, kw)
    assert all(kinds.values()), kinds

    # drawing: normal and wider-than-a-turn maps render, and the route is really drawn
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from mazetube.mazeview import MazeMapWidget
    for seed, kw in ((2026, {}), wide_seed):
        d = TubeDesign(TubeParams(seed=seed, **kw))
        w = MazeMapWidget()
        w.dark = False
        w.set_design(d)
        img = QImage(900, 420, QImage.Format_RGB32)
        img.fill(QColor("white"))
        painter = QPainter(img)
        w.render_map(painter, QRectF(0, 0, 900, 420))
        painter.end()
        blue = sum(1 for x in range(0, 900, 3) for y in range(0, 420, 3)
                   if QColor(img.pixel(x, y)).blue() > 180 and QColor(img.pixel(x, y)).red() < 60)
        assert blue > 150, "solution line missing from the map"
    assert app is not None
    print(f"maze map window: route always on the page ({kinds['sector']} per-sector, "
          f"{kinds['turn']} one-turn, {kinds['wide']} wider)  OK")


def check_click():
    """The twist lock: one extra row, a flat pocket under solid wall, one small bump on the way in."""
    from mazetube.geometry import CLICK_BUMP
    for nubs in (1, 2, 3, 4):
        d = check_design(TubeParams(nubs=nubs), f"twist lock, {nubs} nub(s)")
        assert d.lock_cols == 1 and d.detent_h > 0
        assert abs(d.notch - d.row_pitch) < 1e-9, "exactly one extra row below the maze"
        # one bump only: across nub 0's slot; the other nubs' slots have a plain floor
        z = np.array([d.z_notch_floor + 0.3 * d.wp])
        for j in range(d.n):
            r = d.body_radius(np.array([d.th_ridge + j * 2 * np.pi / d.n]), z)[0]
            assert abs(r - (d.r_floor + (d.detent_h if j == 0 else 0.0))) < 1e-9, (nubs, j)
        # ... a thin line with 45° sides, between the nub's two resting places and clear of both
        assert abs(d.ridge_in - d.detent_h) < 1e-9 and abs(d.ridge_out - d.detent_h) < 1e-9
        nub0, half = d.nub_angles[0], 0.5 * d.nub_th
        lo, hi = d.th_ridge - d.a_lo, d.th_ridge + d.a_hi
        home0 = nub0 - LOCK_TURN * d.lock_cols * d.P_th
        assert min(nub0, home0) + half < lo < hi < max(nub0, home0) - half
        assert (hi - lo) * d.r_floor < 1.0, "the bump is a thin line, the rest of the landing is flat"
        # the landing is flat: under the resting nub the floor is the plain groove floor, over
        # the whole height of the nub's flat tip (a stretched ceiling slope once made it a ramp)
        zs = d.z0 + np.linspace(d.zn0, d.zn1 - d.dk, 9)
        ths = nub0 + np.linspace(-half, half, 9)
        assert np.abs(d.body_radius(ths[:, None], zs[None, :]) - d.r_floor).max() < 1e-9
        # it stands just above the nub's tip clearance
        assert abs(d.detent_h - (d.c + d.detent)) < 1e-9 and abs(d.detent - CLICK_BUMP[1]) < 1e-9
        # closing ends with a clockwise turn (seen from above the cap); opening starts anticlockwise
        frames = d.solution_keyframes(lift=0.0)
        assert LOCK_TURN == -1 and d.lock_way == "clockwise" and d.unlock_way == "anticlockwise"
        assert frames[0] == (0.0, 0.0) and frames[1][1] == 0.0 and frames[2][1] == d.notch
        assert abs(frames[1][0] - d.lock_cols * d.P_th) < 1e-12 and frames[2][0] == frames[1][0]
        lo_c, hi_c = d.solution_columns()
        assert lo_c <= d.maze.home - d.lock_cols and hi_c >= d.maze.home, "the map shows the pocket"
        # the lock row's floor stops the cap, not its rim on the shoulder: pushed right down
        # the nub still fits, and the body's shoulder is lower than the cap's rim ever gets
        assert abs(d.z0 - d.z_seat - (d.c + 0.1)) < 1e-9 and d.z0 - d.z_seat > d.c
        assert d.min_gap(0.0, -d.c + 0.02) > 0 and d.min_gap(frames[1][0], -d.c + 0.02) > 0
        bv = d.body_mesh()[0]
        band = bv[np.hypot(bv[:, 0], bv[:, 1]) > d.r_cap_in + 0.5]
        assert abs(band[:, 2].max() - d.z_seat) < 1e-9, "the shoulder is where the design says"

    prev = 0.0
    for level in (1, 2, 3):
        d = TubeDesign(TubeParams(detent_level=level))
        assert prev < d.detent <= 0.4 + 1e-9 and abs(d.detent - CLICK_BUMP[level]) < 1e-9
        assert d.detent_h <= d.d - 0.3 + 1e-9, "the bump stays inside the groove"
        assert 1 <= d.lock_cols <= d.Ns - 1, "the slot ends before the next nub's home column"
        room = (d.lock_cols * d.P_th - d.nub_th) * d.r_floor
        assert d.ridge_in + d.ridge_out <= room - 0.3 + 1e-9, "the bump fits in its slot"
        prev = d.detent
    # thin maze walls leave less room: a firm bump then gets a slot two columns long
    assert TubeDesign(TubeParams(wall_width=0.9, detent_level=3)).lock_cols == 2

    # Off: no lock row, just the home notch, and the cap's rim sits on the shoulder
    d = check_design(TubeParams(detent_level=0), "click off: home notch")
    assert d.detent_h == 0 and d.lock_cols == 0 and abs(d.notch - 1.5) < 1e-9 and d.z_seat == d.z0
    assert d.solution_keyframes()[1] == (0.0, d.notch)

    # set by hand, and the limits
    d = TubeDesign(TubeParams(detent=0.3))
    assert abs(d.detent - 0.3) < 1e-9 and abs(d.detent_h - 0.6) < 1e-9
    assert TubeDesign(TubeParams(detent=1.4)).errors
    d = TubeDesign(TubeParams(groove_depth=0.6, auto_size=False))    # no depth to spare for a bump
    assert d.detent_h == 0 and d.lock_cols == 1 and any("too shallow" in n for n in d.notes)
    assert not d.errors and "no click" in d.info()["Home click"]
    # two columns per nub and thin walls: the slot can only be one column long, so the bump is lower
    kw = dict(columns=6, nubs=3, wall_width=0.8)
    d = check_design(TubeParams(**kw, detent_level=3), "twist lock, 2 columns per nub")
    assert d.Ns == 2 and d.lock_cols == 1 and 0.05 <= d.detent < 0.4
    assert any("too short for the full click bump" in n for n in d.notes)
    assert TubeDesign(TubeParams(**kw, detent=0.9)).errors

    # designs saved with the old per-nub interference come back at the gentle setting
    old = TubeParams.from_dict({"nubs": 3, "detent": 0.25, "auto_size": True})
    assert old.detent_level == 1 and old.detent == 0.0
    assert TubeParams.from_dict({"detent": 0.0}).detent_level == 0
    print("twist lock: clockwise into a flat pocket, one small bump, floor-stopped, 1-4 nubs  OK")


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
