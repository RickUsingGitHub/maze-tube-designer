"""Geometry of the maze tube: layout, surface functions and watertight meshes.

Two printable parts are produced:

* **Body** - a closed-bottom inner tube.  Its outer surface carries the maze
  as grooves; a grip band (flange) at the bottom matches the cap diameter.
* **Cap**  - a closed-top outer tube that slides over the body.  Nubs on its
  inner surface ride in the grooves, so the cap only comes off once the maze
  has been solved.

Both surfaces are *radial height maps* r(theta, z).  Every part is built as a
surface of revolution whose profile radius may vary with theta, swept on a
grid that contains every break line of the height map.  Step edges are
modelled by pairs of grid lines a tiny distance (EPS) apart, which keeps the
mesh a plain deformed grid: always closed, 2-manifold and free of
self-intersections, without any boolean operations.

Coordinates: millimetres, z up.  Body: z = 0 at the bottom.  Cap: z = 0 at
its open rim; when assembled in the home position the cap rim sits on the
body shoulder at z = base_height and the cap needs no rotation.
"""

from __future__ import annotations

import math

import numpy as np

from .maze import Maze, generate
from .params import TubeParams

EPS = 0.05          # width of the near-vertical step facets (mm)
TWO_PI = 2.0 * math.pi


class DesignError(Exception):
    pass


class TubeDesign:
    """All derived dimensions + maze for one set of parameters."""

    def __init__(self, params: TubeParams, maze: Maze | None = None, maze_key=None):
        self.p = p = params.copy()
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.maze: Maze | None = None
        self._body = self._cap = None

        # ---------------------------------------------------------- radii
        self.d = p.groove_depth
        self.c = p.clearance
        self.r_maze = p.maze_diameter / 2.0
        self.r_floor = self.r_maze - self.d
        self.r_cav = self.r_floor - p.core_wall
        self.r_cap_in = self.r_maze + self.c
        self.r_tip = self.r_floor + self.c
        # Spiral vase mode: the slicer prints one wall of width w_v along the
        # outer contour, so every z-step must be a <= 45° slope (groove floors
        # and nub undersides too) and the parts are exported solid.
        self.vase = bool(p.vase_mode)
        self.w_v = p.vase_wall if self.vase else 0.0
        self.R_out = self.r_cap_in + (self.w_v if self.vase else p.cap_wall)
        self.dk = self.d if (p.chamfer_ceilings or self.vase) else 0.0   # ceiling slope
        self.dkb = self.d if self.vase else 0.0                        # floor slope
        self.wp = p.path_width
        self.ww = p.wall_width
        self.n = max(1, int(p.nubs))
        self.M = max(1, int(p.rows))
        self.notch = p.notch_depth if p.notch_depth >= 0.3 else 0.0
        self.detent = p.detent if p.detent >= 0.05 else 0.0
        self.detent_h = 0.0
        self.notes: list[str] = []

        self._validate_basic()
        if self.errors:
            return

        # -------------------------------------------------------- columns
        circ = TWO_PI * self.r_maze
        if p.columns > 0:
            N = int(p.columns)
            N -= N % self.n
            N = max(N, self.n)
        else:
            N = int(circ // (self.wp + self.ww))
            N -= N % self.n
        self.N = N
        self.Ns = N // self.n if N else 0
        if self.Ns < 2:
            self.errors.append(
                "Too few maze columns per nub. Increase the diameter or reduce "
                "path/wall width or the number of nubs.")
            return
        self.P_th = TWO_PI / N
        self.w_th = self.ww / self.r_maze
        self.path_arc_top = (self.P_th - self.w_th) * self.r_maze
        self.path_arc_floor = (self.P_th - self.w_th) * self.r_floor
        # nub angular width: groove angle minus sideways clearance at the tip
        self.nub_th = (self.P_th - self.w_th) - 2.0 * self.c / self.r_tip
        self.nub_arc = self.nub_th * self.r_tip
        if self.nub_arc < 0.8:
            self.errors.append(
                f"Paths are too narrow for a nub ({self.nub_arc:.2f} mm). "
                "Use fewer columns or a wider path.")
            return
        if self.path_arc_top < self.wp - 1e-6:
            self.warnings.append(
                f"Columns forced: path width around the tube is only "
                f"{self.path_arc_top:.2f} mm (requested {self.wp:.2f} mm).")

        # ------------------------------------------------------ home notch
        if self.vase and 0 < self.notch < self.dkb:
            # the notch floor is a 45° slope; it has to fit inside the notch
            self.notch = math.ceil(self.dkb * 10.0 - 1e-6) / 10.0
            self.notes.append(f"Home notch deepened to {self.notch:.1f} mm for the 45° floor "
                              f"needed in spiral vase mode.")
        self._plan_detent()
        if self.errors:
            return

        # ------------------------------------------------- rows (auto = fill length)
        if p.rows <= 0:
            # maze section = margin + notch + M cells + (M-1) walls + top wall
            fit = int((p.maze_length - self.notch - self.ww) // (self.wp + self.ww))
            self.M = max(1, min(200, fit))
            if fit < 1:
                self.notes.append("Maze length is too short for even one row; using 1 row.")

        # ----------------------------------------------------------- maze
        key = self.maze_key()
        if maze is not None and maze_key is not None and tuple(maze_key) == key \
                and maze.rows == self.M and maze.cols == self.Ns:
            self.maze = maze
        else:
            self.maze = generate(self.M, self.Ns, p.algorithm, p.seed,
                                 p.horizontal_bias, p.loops, p.longest_path)

        self._build_layout()
        self._validate_derived()

    # ------------------------------------------------------------------ keys
    def maze_key(self):
        p = self.p
        return (self.M, self.Ns, p.algorithm, int(p.seed), float(p.horizontal_bias),
                float(p.loops), bool(p.longest_path))

    # ------------------------------------------------------------ validation
    def _validate_basic(self):
        P = self.p
        if P.maze_diameter <= 0:
            self.errors.append("Maze diameter must be positive.")
        if self.d < 0.6:
            self.errors.append("Groove depth must be at least 0.6 mm.")
        if self.c < 0.05:
            self.errors.append("Clearance must be at least 0.05 mm.")
        if self.r_cav < 2.0:
            self.errors.append("Inner cavity is too small: increase the diameter or "
                               "reduce groove depth / core wall.")
        if self.wp < 1.0 or self.ww < 0.6:
            self.errors.append("Path width must be >= 1 mm and wall width >= 0.6 mm.")
        if P.cap_wall < 0.8 or P.core_wall < 0.6:
            self.errors.append("Cap wall must be >= 0.8 mm and core wall >= 0.6 mm.")
        if P.cap_top < 0.6 or P.bottom_thickness < 0.6:
            self.errors.append("Top and bottom thickness must be >= 0.6 mm.")
        if P.base_height < 1.0:
            self.errors.append("Base height must be at least 1 mm.")
        if not self.vase and P.grip_flutes > 0 and P.grip_depth > P.cap_wall - 0.6:
            self.errors.append("Grip flutes are deeper than the cap wall allows.")
        if self.vase:
            need = 2 * self.d + 2 * self.c + 0.6
            if self.wp < need - 1e-9:
                self.errors.append(
                    f"Spiral vase mode slopes every groove edge at 45°, so the path must be at "
                    f"least {need:.1f} mm wide for {self.d:.1f} mm deep grooves "
                    f"(or make the grooves shallower).")
            if not 0.3 <= P.vase_wall <= 1.5:
                self.errors.append("Vase line width must be between 0.3 and 1.5 mm.")
            if P.base_height < P.vase_wall + min(P.edge_chamfer, 0.4 * P.base_height) + 0.5:
                self.errors.append("Base height is too small for vase mode.")
        elif self.dk > 0 and self.wp - 2 * self.c - self.dk < 0.4:
            self.errors.append(
                "Path too narrow for 45° chamfered ceilings at this groove depth: "
                "widen the path, reduce groove depth, or turn chamfers off.")
        if self.wp - 2 * self.c < 0.6:
            self.errors.append("Clearance is too large for the path width.")
        if P.rows > 200:
            self.errors.append("Rows must be between 1 and 200.")
        if P.rows <= 0 and P.maze_length < 5:
            self.errors.append("Set a maze length (or a number of rows).")
        if not self.vase and P.plain_wall > 0 and P.plain_wall < 0.8:
            self.errors.append("Plain wall must be at least 0.8 mm (or 0 for a straight bore).")
        if (not self.vase and P.plain_wall > 0 and P.grip_flutes > 0
                and P.plain_wall < P.grip_depth + 0.8):
            self.errors.append("Plain wall is too thin for the grip flutes.")
        if P.resolution < 0.1:
            self.errors.append("Resolution must be at least 0.1 mm.")

    def _validate_derived(self):
        P = self.p
        if self.d < 1.2:
            self.warnings.append("Shallow grooves (< 1.2 mm): the nub may skip over walls.")
        if self.ww < 1.2:
            self.warnings.append("Thin maze walls (< 1.2 mm) may break or print poorly.")
        if self.c < 0.15:
            self.warnings.append("Very tight clearance - parts may not slide on most printers.")
        elif self.c > 0.6:
            self.warnings.append("Large clearance - the fit will be loose.")
        tip_h = self.wp - 2 * self.c - self.dk - self.dkb
        if tip_h < 1.0:
            self.warnings.append(f"Nub tip is only {tip_h:.2f} mm tall; consider a wider path.")
        if self.vase:
            if P.grip_flutes > 0:
                self.notes.append("Grip flutes are left off in spiral vase mode.")
            if self.w_v < 0.5:
                self.warnings.append("Thin vase wall: a 0.5-0.8 mm line width gives much stronger parts.")
            if self.ww < 2 * self.w_v:
                self.warnings.append(f"Maze walls ({self.ww:.1f} mm) are narrower than two line widths "
                                     f"({2 * self.w_v:.1f} mm), so the spiral wall will overlap itself there.")
        elif P.cap_wall - (P.grip_depth if P.grip_flutes else 0) < 1.2:
            self.warnings.append("Cap wall is thin (< 1.2 mm at the flutes).")
        if self.maze is not None and len(self.maze.solution) < 2:
            self.warnings.append("Maze solution is trivial.")
        if self.nub_arc < 1.5:
            self.warnings.append(f"Nubs are narrow ({self.nub_arc:.2f} mm) and may snap.")
        if self.detent > 0.45 or (self.detent > 0.3 and self.n >= 3):
            self.warnings.append("Strong detent: the cap may be very hard to close/open. "
                                 "0.2-0.35 mm suits 2 nubs, 0.15-0.25 mm suits 3+ nubs.")

    # ---------------------------------------------------------------- detent
    def _plan_detent(self):
        """Size the snap ridge at the home position.

        A ramped ridge runs across the floor of the home notch.  Pushing the
        cap closed forces the nub over it (the cap wall flexes) and it drops
        into the pocket below; lifting the cap means pushing back over it.
        The notch is deepened if needed so the nub clears the ridge both in
        the pocket and in the row-0 cell above it.
        """
        if self.detent <= 0:
            return
        # radial ridge height = clearance + wanted interference.  A single
        # nub can push the whole cap sideways by one more clearance first.
        self.detent_h = self.c + self.detent + (self.c if self.n == 1 else 0.0)
        if self.detent_h > self.d - 0.3:
            self.errors.append("Home detent is too strong for this groove depth - "
                               "reduce the detent or deepen the grooves.")
            return
        self.ridge_hw = 1.2 * self.detent_h          # ~40° ramps: rides over, prints upright
        tip_h = self.wp - 2 * self.c - self.dk       # flat face of the nub tip
        gap_pocket, gap_cell = 0.05, 0.2
        self.ridge_off = self.c + tip_h + gap_pocket  # lower foot, above the notch floor
        need = tip_h + gap_pocket + 2 * self.ridge_hw + gap_cell
        if self.notch < need:
            self.notch = math.ceil(need * 10.0) / 10.0
            self.notes = [n for n in self.notes if not n.startswith("Home notch deepened")]
            self.notes.append(f"Home notch deepened to {self.notch:.1f} mm to fit the detent.")

    # ---------------------------------------------------------------- layout
    def _build_layout(self):
        p, mz = self.p, self.maze
        N, Ns, M = self.N, self.Ns, self.M

        # theta blocks: even = wall column (between cells c-1 and c), odd = cell
        th = []
        for col in range(N):
            th.append(col * self.P_th)
            th.append(col * self.P_th + self.w_th)
        th.append(TWO_PI)
        self.th_edges = np.array(th)

        # z blocks, from the shoulder upwards.  The maze section sits at the top
        # of the body (where the cap slides on); everything below it is plain tube.
        maze_sec = self.ww + self.notch + M * self.wp + (M - 1) * self.ww + self.ww
        self.z0 = p.base_height
        if p.body_length > 0:
            self.z0 = p.body_length - maze_sec
            if self.z0 < p.base_height - 1e-9:
                self.z0 = p.base_height
                self.notes.append(f"Body length raised to {self.z0 + maze_sec:.1f} mm "
                                  f"(maze {maze_sec:.1f} mm + grip band {p.base_height:.1f} mm).")
        z = [self.z0]
        kinds = []

        def add(h, kind):
            z.append(z[-1] + h)
            kinds.append(kind)

        add(self.ww, ("margin", -1))
        if self.notch > 0:
            add(self.notch, ("notch", -1))
        for r in range(M):
            add(self.wp, ("cell", r))
            if r < M - 1:
                add(self.ww, ("wall", r))
        add(self.ww, ("top", -1))
        self.z_edges = np.array(z)
        self.z_kinds = kinds
        self.H_body = z[-1]
        self.z_notch_floor = self.z0 + self.ww
        self.z_row0 = self.z_notch_floor + self.notch
        self.row_pitch = self.wp + self.ww

        Bz, Bt = len(kinds), 2 * N
        op = np.zeros((Bz, Bt), dtype=bool)
        for bz, (kind, r) in enumerate(kinds):
            for bt in range(Bt):
                col = bt // 2
                sc = col % Ns
                is_cell = bt % 2 == 1
                if kind == "notch":
                    op[bz, bt] = is_cell and sc == mz.home
                elif kind == "cell":
                    op[bz, bt] = True if is_cell else bool(mz.h_open[r, (col - 1) % Ns])
                elif kind == "wall":
                    op[bz, bt] = is_cell and bool(mz.v_open[r, sc])
                elif kind == "top":
                    op[bz, bt] = is_cell and sc == mz.entry
        self.open = op
        ceil = np.zeros_like(op)
        ceil[:-1] = op[:-1] & ~op[1:]
        self.ceil = ceil
        floor = np.zeros_like(op)
        floor[1:] = op[1:] & ~op[:-1]
        self.floor = floor
        self.home_cols = np.array([bt % 2 == 1 and (bt // 2) % Ns == mz.home for bt in range(Bt)])
        if self.detent_h > 0:
            self.z_ridge = self.z_notch_floor + self.ridge_off + self.ridge_hw

        self.lead = min(0.6, self.d * 0.5)  # lead-in chamfer on the body rim

        # plain section bore: thinner wall below the maze, 45° taper up into the
        # maze-section bore (prints without support, saves a lot of material)
        self.r_plain = self.r_cav
        if not self.vase and p.plain_wall > 0:
            want = self.R_out - p.plain_wall
            room = self.z0 - p.bottom_thickness - 0.5       # height available for the taper
            self.r_plain = max(self.r_cav, min(want, self.r_cav + max(room, 0.0)))
        self.z_taper = self.z0 - (self.r_plain - self.r_cav)

        # cap: covers the maze; any extra length extends past the body's rim
        self.top_gap = max(0.5, self.c)
        cover = (self.H_body - self.z0) + self.top_gap
        self.H_ci = cover
        if p.cap_length > 0:
            self.H_ci = p.cap_length - p.cap_top
            if self.H_ci < cover - 1e-9:
                self.H_ci = cover
                self.notes.append(f"Cap length raised to {cover + p.cap_top:.1f} mm so it "
                                  f"covers the maze.")
        self.cap_ext = self.H_ci - cover + self.top_gap       # length beyond the body rim
        self.H_cap = self.H_ci + p.cap_top
        self.zn0 = self.ww + self.c                 # nub bottom (cap coords)
        self.zn1 = self.ww + self.wp - self.c       # nub top at the wall
        self.nub_angles = np.array(
            [self.cell_theta(mz.home + j * Ns) for j in range(self.n)])

    # helpers --------------------------------------------------------------
    def cell_theta(self, col: int) -> float:
        """Angle of the centre of global column ``col``."""
        return col * self.P_th + self.w_th + 0.5 * (self.P_th - self.w_th)

    def flute(self, th):
        p = self.p
        if p.grip_flutes <= 0 or self.vase:
            return np.zeros_like(th)
        return p.grip_depth * 0.5 * (1.0 - np.cos(p.grip_flutes * th))

    # ------------------------------------------------------- surface radius
    def body_radius(self, th, z):
        """Outer radius of the body's maze surface at (theta, z)."""
        th = np.mod(th, TWO_PI)
        z = np.asarray(z, dtype=float)
        Bz, Bt = self.open.shape
        bt = np.clip(np.searchsorted(self.th_edges, th, side="right") - 1, 0, Bt - 1)
        bz = np.searchsorted(self.z_edges, z, side="right") - 1
        bz = np.where(z == self.H_body, Bz - 1, bz)       # rim belongs to top row
        inside = (bz >= 0) & (bz < Bz)
        bzc = np.clip(bz, 0, Bz - 1)
        op = self.open[bzc, bt] & inside
        r = np.where(op, self.r_floor, self.r_maze)
        if self.dk > 0:
            ce = op & self.ceil[bzc, bt]
            zt = self.z_edges[bzc + 1]
            t = (z - (zt - self.dk)) / self.dk
            r = np.where(ce & (t > 0), self.r_floor + np.clip(t, 0, 1) * self.d, r)
        if self.dkb > 0:                                   # 45° groove floors (vase mode)
            fl = op & self.floor[bzc, bt]
            zb = self.z_edges[bzc]
            t = ((zb + self.dkb) - z) / self.dkb
            r = np.where(fl & (t > 0), np.maximum(r, self.r_floor + np.clip(t, 0, 1) * self.d), r)
        if self.vase:                                      # 45° shoulder the cap rim seats on
            seat = self.r_cap_in - (z - self.z0)
            r = np.where(z <= self.z0 + self.c, np.maximum(r, seat), r)
        if self.detent_h > 0:                              # snap ridge in the home notch
            t = 1.0 - np.abs(z - self.z_ridge) / self.ridge_hw
            on = self.home_cols[bt] & inside & (t > 0)
            r = np.where(on, np.maximum(r, self.r_floor + self.detent_h * t), r)
        if self.lead > 0:
            r = np.minimum(r, self.r_maze - np.clip(z - (self.H_body - self.lead), 0, None))
        return np.where(z > self.H_body + 1e-9, 0.0, r)   # no material above the rim

    def cap_radius(self, th, z):
        """Inner radius of the cap at (theta, z) in cap coordinates."""
        th = np.mod(th, TWO_PI)
        z = np.asarray(z, dtype=float)
        th, z = np.broadcast_arrays(th, z)
        r = np.full(th.shape, self.r_cap_in)
        in_th = np.zeros(th.shape, dtype=bool)
        for a in self.nub_angles:
            dth = np.mod(th - a + math.pi, TWO_PI) - math.pi
            in_th |= np.abs(dth) < 0.5 * self.nub_th
        top_flat = self.zn1 - self.dk if self.dk > 0 else self.zn1
        bot_flat = self.zn0 + self.dkb
        flat = in_th & (z >= bot_flat) & (z <= top_flat)
        r = np.where(flat, self.r_tip, r)
        if self.dk > 0:
            slope = in_th & (z > top_flat) & (z <= self.zn1)
            t = (z - top_flat) / self.dk
            r = np.where(slope, self.r_tip + t * (self.r_cap_in - self.r_tip), r)
        if self.dkb > 0:                                   # 45° nub underside (vase mode)
            slope = in_th & (z >= self.zn0) & (z < bot_flat)
            t = (bot_flat - z) / self.dkb
            r = np.where(slope, self.r_tip + t * (self.r_cap_in - self.r_tip), r)
        return r

    # ------------------------------------------------------------ grids
    def _theta_grid(self, breaks, r_ref):
        """Global theta samples: break pairs + uniform fill."""
        eps_t = EPS / r_ref
        lines = np.concatenate([np.asarray(breaks) - eps_t, np.asarray(breaks) + eps_t])
        lines = np.mod(lines, TWO_PI)
        n_u = int(math.ceil(TWO_PI * self.R_out / self.p.resolution))
        if self.p.grip_flutes > 0:
            n_u = max(n_u, self.p.grip_flutes * 16)
        n_u = max(n_u, 48)
        uni = np.linspace(0.0, TWO_PI, n_u, endpoint=False)
        if len(lines):
            srt = np.sort(lines)
            idx = np.searchsorted(srt, uni)
            lo = srt[np.clip(idx - 1, 0, len(srt) - 1)]
            hi = srt[np.clip(idx, 0, len(srt) - 1)]
            dist = np.minimum(np.abs(uni - lo), np.abs(hi - uni))
            # wrap-around distance to first/last line
            dist = np.minimum(dist, np.abs(uni + TWO_PI - srt[-1]))
            dist = np.minimum(dist, np.abs(srt[0] + TWO_PI - uni))
            uni = uni[dist > 3 * eps_t]
        g = np.unique(np.concatenate([lines, uni]))
        return g

    @staticmethod
    def _uniq(vals, lo, hi):
        v = np.array(sorted(x for x in vals if lo - 1e-9 <= x <= hi + 1e-9))
        keep = np.concatenate([[True], np.diff(v) > 1e-6])
        return v[keep]

    # ------------------------------------------------------------ meshes
    def body_mesh(self):
        if self._body is None:
            self._body = self._make_body()
        return self._body

    def cap_mesh(self):
        if self._cap is None:
            self._cap = self._make_cap()
        return self._cap

    def _make_body(self):
        p = self.p
        th = self._theta_grid(self.th_edges[:-1], self.r_maze)
        z_start = self.z0 - self.w_v if self.vase else self.z0
        zl = [z_start, self.z0, self.H_body]
        for i, e in enumerate(self.z_edges[1:-1], start=1):
            zl += [e - EPS, e + EPS]
            if self.dk > 0 and self.z_kinds[i - 1][0] == "cell":
                zl.append(e - self.dk)
            if self.dkb > 0 and self.z_kinds[i][0] in ("cell", "notch"):
                zl.append(e + self.dkb)
        if self.vase:
            zl.append(self.z0 + self.c)
        if self.lead > 0:
            zl.append(self.H_body - self.lead)
        if self.detent_h > 0:
            zl += [self.z_ridge - self.ridge_hw, self.z_ridge, self.z_ridge + self.ridge_hw]
        zg = self._uniq(zl, z_start, self.H_body)

        R = self.R_out - self.flute(th)                    # (T,)
        ch = min(p.edge_chamfer, p.base_height * 0.4)
        ch2 = min(0.5, p.base_height * 0.3)
        cols_r, cols_z = [], []

        def col(r, z):
            cols_r.append(np.broadcast_to(r, th.shape).astype(float))
            cols_z.append(float(z))

        if ch > 0.01:
            col(R - ch, 0.0)
            col(R, ch)
        else:
            col(R, 0.0)
        if not self.vase:
            col(R, self.z0 - ch2)
            col(R - ch2, self.z0)
        rz = self.body_radius(th[:, None], zg[None, :])    # (T, K)
        for k, z in enumerate(zg):
            col(rz[:, k], z)
        rr, zz = np.stack(cols_r, 1), np.array(cols_z)
        if self.vase:
            # solid: the slicer's single spiral wall follows this outer surface
            self.body_profile = (th, rr, zz)
            return _sweep(th, rr, zz, 0.0, self.H_body)
        bore_r = [self.r_cav]
        bore_z = [self.H_body]
        if self.r_plain > self.r_cav + 1e-6:              # taper into the wider plain bore
            bore_r += [self.r_cav, self.r_plain]
            bore_z += [self.z0, self.z_taper]
        bore_r.append(self.r_plain)
        bore_z.append(p.bottom_thickness)
        rr = np.concatenate([rr, np.tile(bore_r, (len(th), 1))], 1)
        zz = np.concatenate([zz, bore_z])
        self.body_profile = (th, rr, zz)
        return _sweep(th, rr, zz, 0.0, p.bottom_thickness)

    def _make_cap(self):
        p = self.p
        half = 0.5 * self.nub_th
        breaks = np.concatenate([self.nub_angles - half, self.nub_angles + half])
        th = self._theta_grid(breaks, self.r_tip)
        zl = [0.0, self.H_ci]
        if self.dkb > 0:
            zl += [self.zn0, self.zn0 + self.dkb]
        else:
            zl += [self.zn0 - EPS, self.zn0 + EPS]
        if self.dk > 0:
            zl += [self.zn1 - self.dk, self.zn1, self.zn1 + EPS]
        else:
            zl += [self.zn1 - EPS, self.zn1 + EPS]

        R = self.R_out - self.flute(th)
        ch = min(p.edge_chamfer, p.cap_top * 0.8)
        ch2 = 0.4
        cols_r, cols_z = [], []

        def col(r, z):
            cols_r.append(np.broadcast_to(r, th.shape).astype(float))
            cols_z.append(float(z))

        if self.vase:
            # solid cap whose outer skin is the inner fit surface + one line width:
            # the nubs become inward dents of the single printed wall
            top = self.H_cap - ch if ch > 0.01 else self.H_cap
            zg = self._uniq(zl + [top], 0.0, top)[::-1]
            if ch > 0.01:
                col(R - ch, self.H_cap)
            rz = self.cap_radius(th[:, None], zg[None, :]) + self.w_v
            for k, z in enumerate(zg):
                col(rz[:, k], z)
            rr, zz = np.stack(cols_r, 1), np.array(cols_z)
            self.cap_profile = (th, rr, zz)
            return _sweep(th, rr, zz, self.H_cap, 0.0)

        zg = self._uniq(zl, 0.0, self.H_ci)
        if ch > 0.01:
            col(R - ch, self.H_cap)
            col(R, self.H_cap - ch)
        else:
            col(R, self.H_cap)
        col(R, ch2)
        col(R - ch2, 0.0)
        rz = self.cap_radius(th[:, None], zg[None, :])
        for k, z in enumerate(zg):
            col(rz[:, k], z)
        rr, zz = np.stack(cols_r, 1), np.array(cols_z)
        self.cap_profile = (th, rr, zz)
        return _sweep(th, rr, zz, self.H_cap, self.H_ci)

    def display_meshes(self):
        """Meshes for the 3D view: in vase mode, the thin printed shells.

        The exported vase models are solid (the slicer hollows them); here the
        inner skin is added one line width inside, the way spiral vase mode
        offsets each layer, so the section view shows what will be printed.
        """
        body, cap = self.body_mesh(), self.cap_mesh()
        if not self.vase:
            return body, cap
        w, bt = self.w_v, self.p.bottom_thickness
        th, rr, zz = self.body_profile                      # z ascending
        keep = zz > bt + 1e-6
        r_bt = np.array([np.interp(bt, zz, row) for row in rr]) - w
        b = _sweep(th, np.concatenate([rr, rr[:, keep][:, ::-1] - w, r_bt[:, None]], 1),
                   np.concatenate([zz, zz[keep][::-1], [bt]]), 0.0, bt)
        th, rr, zz = self.cap_profile                       # z descending
        keep = zz < self.H_ci - 1e-6
        r_ci = np.array([np.interp(self.H_ci, zz[::-1], row[::-1]) for row in rr]) - w
        c = _sweep(th, np.concatenate([rr, rr[:, keep][:, ::-1] - w, r_ci[:, None]], 1),
                   np.concatenate([zz, zz[keep][::-1], [self.H_ci]]), self.H_cap, self.H_ci)
        return b, c

    # ------------------------------------------------------- solution poses
    def solution_keyframes(self, lift: float = 12.0):
        """Cap poses (phi_radians, Z_mm) from closed to removed.

        Z is the cap lift above the closed position; phi its rotation.
        """
        mz = self.maze
        frames = [(0.0, 0.0)]
        phi, Z = 0.0, self.notch
        if self.notch > 0:
            frames.append((phi, Z))
        for dc, dr in mz.moves:
            phi += dc * self.P_th
            Z += dr * self.row_pitch
            frames.append((phi, Z))
        z_out = (self.H_body - self.z0) - self.zn0 + 1.0
        frames.append((phi, z_out))
        frames.append((phi, z_out + lift))
        return frames

    def nub_position(self, phi, Z):
        """(theta, z) of nub 0's centre on the body for a given cap pose."""
        th = self.nub_angles[0] + phi
        z = self.z0 + Z + 0.5 * (self.zn0 + self.zn1)
        return th, z

    def min_gap(self, phi, Z, n_th=24, n_z=24):
        """Smallest radial gap between cap inner surface and body at a pose.

        Negative values mean the parts would collide (the maze blocks it).
        """
        half = 0.5 * self.nub_th
        margin = 1.0 / self.r_tip
        gaps = []
        zs = np.linspace(self.zn0 - 0.5, self.zn1 + 0.5, n_z)
        for a in self.nub_angles:
            ths = np.linspace(a - half - margin, a + half + margin, n_th)
            T, Zg = np.meshgrid(ths, zs, indexing="ij")
            rc = self.cap_radius(T, Zg)
            rb = self.body_radius(T + phi, Zg + self.z0 + Z)
            gaps.append(np.min(rc - rb))
        return float(min(gaps))

    def check_solution_fit(self, step=0.25):
        """Walk the cap along the solution path.

        Returns (min_gap, detent_overlap): the smallest clearance anywhere on
        the path except while snapping over the home detent, and how far the
        nub has to be pushed outwards to pass the detent (0 without one).
        """
        frames = self.solution_keyframes(lift=0.0)
        worst, overlap = float("inf"), 0.0
        for i, ((p0, z0), (p1, z1)) in enumerate(zip(frames, frames[1:])):
            snap = self.detent_h > 0 and i == 0       # home pocket -> row 0
            dist = math.hypot((p1 - p0) * self.r_maze, z1 - z0)
            n = max(2, int(dist / (step * (0.25 if snap else 1.0))) + 1)
            for t in np.linspace(0.0, 1.0, n):
                g = self.min_gap(p0 + (p1 - p0) * t, z0 + (z1 - z0) * t, 16, 64 if snap else 16)
                if snap and 0.0 < t < 1.0:
                    overlap = max(overlap, -g)
                else:
                    worst = min(worst, g)
        return worst, overlap

    # --------------------------------------------------------------- info
    def info(self) -> dict:
        p, mz = self.p, self.maze
        bv, bf = self.body_mesh()
        cv, cf = self.cap_mesh()
        if self.vase:
            # printed volume = one line width over the side walls + solid bottoms
            def shell(v, f, bottom):
                n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
                area = 0.5 * np.linalg.norm(n, axis=1)
                side = np.abs(n[:, 2]) < 0.95 * np.linalg.norm(n, axis=1)
                return (area[side].sum() * self.w_v + math.pi * self.R_out ** 2 * bottom) / 1000.0
            vb = shell(bv, bf, p.bottom_thickness)
            vc = shell(cv, cf, p.cap_top)
            bore = f"Ø{2 * (self.r_floor - self.w_v):.1f} (ribbed)"
            vol_label, fill = "Volume (printed)", 1.0
        else:
            vb = mesh_volume(bv, bf) / 1000.0
            vc = mesh_volume(cv, cf) / 1000.0
            bore = f"Ø{2 * self.r_cav:.1f}"
            vol_label, fill = "Volume (solid)", 0.55
        # usable length when closed: body cavity + the cap's extension past the rim
        storage = self.z0 + self.H_ci - p.bottom_thickness
        maze_sec = self.H_body - self.z0
        cavity = f"{bore} × {storage:.1f} mm"
        if self.cap_ext > self.top_gap + 1e-6:
            cavity += f" (cap adds {self.cap_ext:.0f})"
        return {
            "Outer diameter": f"{2 * self.R_out:.1f} mm",
            "Closed length": f"{self.z0 + self.H_cap:.1f} mm",
            "Body length": f"{self.H_body:.1f} mm (maze {maze_sec:.1f}, plain {self.z0:.1f})",
            "Cap length": f"{self.H_cap:.1f} mm",
            "Storage inside": cavity,
            "Maze grid": f"{self.N} columns × {self.M} rows ({self.Ns} per nub)",
            "Path width": f"{self.path_arc_top:.2f} mm (floor {self.path_arc_floor:.2f} mm)",
            "Nub size": f"{self.nub_arc:.2f} × {self.zn1 - self.zn0:.2f} mm, {self.r_cap_in - self.r_tip:.2f} deep",
            "Home detent": (f"{self.detent:.2f} mm snap (ridge {self.detent_h:.2f} mm high)"
                            if self.detent_h > 0 else "off"),
            "Solution": f"{len(mz.moves)} moves, {mz.turns()} turns",
            "Dead ends": f"{mz.dead_ends()} per sector",
            "Triangles": f"body {len(bf):,} / cap {len(cf):,}",
            vol_label: f"body {vb:.1f} cm³ / cap {vc:.1f} cm³",
            "PLA estimate": (f"~{(vb + vc) * 1.24:.0f} g (spiral vase, {self.w_v:.2f} mm wall)"
                             if self.vase else f"~{(vb + vc) * 1.24 * fill:.0f} g (at ~40% infill)"),
        }


# ---------------------------------------------------------------------------
# mesh helpers
# ---------------------------------------------------------------------------

def _sweep(th, r, z, z_start, z_end):
    """Sweep a theta-dependent profile around the z axis.

    th: (T,) angles, r: (T, K) radii, z: (K,) heights.  The profile runs
    from a pole on the axis at z_start, through the K points, to a second
    pole at z_end.  Returns (vertices (V,3), faces (F,3)), outward oriented.
    """
    T, K = r.shape
    x = r * np.cos(th)[:, None]
    y = r * np.sin(th)[:, None]
    zz = np.broadcast_to(z[None, :], (T, K))
    verts = np.empty((T * K + 2, 3))
    verts[:-2, 0] = x.ravel()
    verts[:-2, 1] = y.ravel()
    verts[:-2, 2] = zz.ravel()
    vs, ve = T * K, T * K + 1
    verts[vs] = (0, 0, z_start)
    verts[ve] = (0, 0, z_end)

    i = np.arange(T)
    i2 = (i + 1) % T
    k = np.arange(K - 1)
    a = (i[:, None] * K + k[None, :]).ravel()
    b = (i2[:, None] * K + k[None, :]).ravel()
    c = (i2[:, None] * K + k[None, :] + 1).ravel()
    d = (i[:, None] * K + k[None, :] + 1).ravel()
    quads1 = np.stack([a, b, c], 1)
    quads2 = np.stack([a, c, d], 1)
    fan_s = np.stack([np.full(T, vs), i2 * K, i * K], 1)
    fan_e = np.stack([np.full(T, ve), i * K + K - 1, i2 * K + K - 1], 1)
    faces = np.concatenate([fan_s, quads1, quads2, fan_e]).astype(np.int64)
    if mesh_volume(verts, faces) < 0:
        faces = faces[:, ::-1].copy()
    return verts, faces


def mesh_volume(verts, faces) -> float:
    v0, v1, v2 = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    return float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6.0)


def transform(verts, rot_z=0.0, flip_x=False, offset=(0.0, 0.0, 0.0)):
    """Rotate about z, optionally flip upside-down (rotate 180° about x), translate."""
    v = verts.copy()
    if rot_z:
        c, s = math.cos(rot_z), math.sin(rot_z)
        x, y = v[:, 0].copy(), v[:, 1].copy()
        v[:, 0] = c * x - s * y
        v[:, 1] = s * x + c * y
    if flip_x:
        v[:, 1] = -v[:, 1]
        v[:, 2] = -v[:, 2]
    v += np.asarray(offset, dtype=float)
    return v


def print_plate(design: TubeDesign, gap: float = 10.0):
    """Body upright + cap flipped (open side up) side by side, on z = 0."""
    bv, bf = design.body_mesh()
    cv, cf = design.cap_mesh()
    dx = design.R_out + gap * 0.5
    bv2 = transform(bv, offset=(-dx, 0, 0))
    cv2 = transform(cv, flip_x=True, offset=(dx, 0, design.H_cap))
    verts = np.concatenate([bv2, cv2])
    faces = np.concatenate([bf, cf + len(bv2)])
    return verts, faces


def edge_check(faces) -> tuple[bool, str]:
    """Verify the mesh is closed and consistently oriented."""
    e = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    # every directed edge must appear exactly once, and its reverse once
    key = e[:, 0].astype(np.int64) * (faces.max() + 1) + e[:, 1]
    rkey = e[:, 1].astype(np.int64) * (faces.max() + 1) + e[:, 0]
    uniq, counts = np.unique(key, return_counts=True)
    if np.any(counts != 1):
        return False, f"{int(np.sum(counts != 1))} duplicated directed edges"
    missing = ~np.isin(rkey, uniq)
    if np.any(missing):
        return False, f"{int(missing.sum())} boundary edges"
    return True, "closed, oriented 2-manifold"
