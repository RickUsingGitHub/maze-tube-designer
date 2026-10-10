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
from dataclasses import dataclass

import numpy as np

from .maze import Maze, generate
from .params import TubeParams
from .textmask import layout_text

EPS = 0.05          # width of the near-vertical step facets (mm)
PLA_E = 3000.0      # stiffness of printed PLA (MPa), for the click-force estimate
FRICTION = 0.3      # PLA sliding on PLA
CLICK_FORCE = {1: 6.0, 2: 12.0, 3: 20.0}     # pull to open (N): light, medium, firm
CLICK_NAME = {1: "light", 2: "medium", 3: "firm"}
INLAY_DEPTH = 0.8   # second-colour text goes at least this deep: two printed walls (mm)
TWO_PI = 2.0 * math.pi


class DesignError(Exception):
    pass


class TubeDesign:
    """All derived dimensions + maze for one set of parameters."""

    def __init__(self, params: TubeParams, maze: Maze | None = None, maze_key=None):
        self.p = p = params.copy()
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.maze: Maze | None = None
        self._body = self._cap = self._parts = self._display = None
        self.profiles: dict = {}      # part name -> (theta, radii, heights) of its sweep
        self.bands: dict = {}         # "cap" / "base" -> text band on that wall
        self.inlays: dict = {}        # "cap" / "base" -> (verts, faces) of second-colour letters
        self._layouts: dict = {}
        self.three = False            # body + extension + cap
        self._want3 = False

        # ----------------------------------------- size from the contents
        # With auto sizing the tube is derived from what goes inside: the bore
        # from the contents' diameter here, the lengths later (they depend on
        # the maze layout and the printer height).  self.p then holds the
        # resulting explicit dimensions.
        self.auto = bool(p.auto_size)
        self.split = False            # True when the contents are shared by body and cap
        if self.auto:
            inner = p.vase_wall if p.vase_mode else p.core_wall
            p.maze_diameter = (p.content_diameter + p.fit_slack
                               + 2.0 * (p.groove_depth + inner))

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
        self.detent = 0.0             # click: how far the cap rim has to flex (mm)
        self.detent_h = 0.0           # height of the ridge that makes it
        # glued joint of a three-piece tube (extension + cap end)
        self.j = max(4.0, p.joint_length)
        self.cj = 0.5 * self.c                              # joint clearance: a glue fit
        self.lap = self.j if self.vase else self.j - 0.3    # length the two cap pieces share
        mid = self.r_cap_in + 0.5 * p.cap_wall
        self.R_sk, self.R_sp = mid + 0.5 * self.cj, mid - 0.5 * self.cj   # socket / spigot
        self.R_fl = self.r_cap_in + 2 * self.w_v + self.cj                # vase collar
        self.grip = p.base_height                           # plain section below the maze

        self._validate_basic()
        if self.errors:
            return
        self._plan_text()

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
        self._plan_detent()
        if self.errors:
            return

        # ------------------------------------------------- rows (auto = fill length)
        if self.auto:
            self._auto_lengths()
            if self.errors:
                return
        elif p.rows <= 0:
            # maze section = margin + notch + M cells + (M-1) walls + top wall
            fit = self._rows_for(p.maze_length)
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

    # ------------------------------------------------------- auto sizing
    def _rows_for(self, length: float) -> int:
        """Rows that fit in a maze section of the given length."""
        return int((length - self.notch - self.ww + 1e-9) // (self.wp + self.ww))

    def _maze_sec(self, rows: int) -> float:
        """Length of the maze section: margin + notch + cells + walls + top wall."""
        return self.notch + rows * (self.wp + self.ww) + self.ww

    def _auto_lengths(self):
        """Choose rows, the number of pieces and the part lengths from the contents.

        Short contents live entirely in the body and the cap just covers the
        maze.  Contents longer than one printable part are shared between body
        and cap (equal lengths).  If two parts would be taller than the printer
        allows, the maze overlap is shortened row by row - and when that would
        leave only a token maze, the cap is split into an extension and a cap
        end (three pieces of equal length, glued joint).
        """
        p = self.p
        H = p.printer_height
        need = p.content_length + p.fit_slack
        tops = p.cap_top + p.bottom_thickness
        grip = self.grip
        want_rows = max(1, min(200, self._rows_for(p.maze_length)))
        up = lambda v: math.ceil(v * 10.0 - 1e-6) / 10.0          # round up to 0.1 mm

        body_alone = need + p.bottom_thickness
        if body_alone <= H and p.pieces != 3:
            # rows as if there were no text; base text then lengthens the tube a little
            rows = min(want_rows, self._rows_for(body_alone - p.base_height))
            rows = max(rows, 1)
            body = max(body_alone, grip + self._maze_sec(rows))
            while body > H + 1e-9 and rows > 1:      # a tall block of base text: give up rows
                rows -= 1
                body = max(body_alone, grip + self._maze_sec(rows))
            if body > H + 1e-9:
                self.errors.append(f"Printer height ({H:.0f} mm) is too small for even a "
                                   f"one-row maze tube ({body:.0f} mm).")
                return
            self.M = rows
            p.body_length, p.cap_length = min(up(body), max(body, H)), 0.0
            return

        def fit(n_parts):
            """Most rows for which n equal parts fit the printer: (rows, part length)."""
            lap = self.lap if n_parts == 3 else 0.0
            rows = want_rows
            while rows >= 1:
                sec = self._maze_sec(rows)
                x = (need + tops + sec + lap) / n_parts
                if x <= H + 1e-9 and x - sec >= grip - 1e-9:
                    return rows, x
                rows -= 1
            return 0, 0.0

        rows2, x2 = fit(2)
        rows3, x3 = fit(3)
        if p.pieces == 2:
            use3 = False
        elif p.pieces == 3:
            use3 = rows3 >= 1
        else:       # automatic: two parts unless that leaves only a token maze
            use3 = rows2 < min(want_rows, 4) and rows3 > rows2
        rows, x = (rows3, x3) if use3 else (rows2, x2)
        if rows < 1:
            n = 3 if p.pieces != 2 else 2
            most = n * H - tops - self._maze_sec(1) - p.fit_slack - (self.lap if n == 3 else 0)
            self.errors.append(
                f"These contents ({p.content_length:.0f} mm) are too long for "
                f"{'three' if n == 3 else 'two'} parts on a {H:.0f} mm tall printer: the most "
                f"that fits is about {most:.0f} mm.")
            return
        if rows < want_rows:
            self.notes.append(
                f"Maze shortened to {rows} row{'s' if rows != 1 else ''} "
                f"({self._maze_sec(rows):.0f} mm) so every part fits the {H:.0f} mm printer height.")
        if rows <= 2:
            self.warnings.append("The maze is very short - it will be easy to solve. A taller "
                                 "printer or shorter contents leaves room for more rows.")
        self.M = rows
        self.split = True
        x = min(up(x), max(x, H))
        if use3:
            self._want3 = True
            p.body_length, p.cap_length = x, 2 * x - self.lap
        else:
            p.body_length = p.cap_length = x

    # ------------------------------------------------------------------ keys
    def maze_key(self):
        p = self.p
        return (self.M, self.Ns, p.algorithm, int(p.seed), float(p.horizontal_bias),
                float(p.loops), bool(p.longest_path))

    # ------------------------------------------------------------ validation
    def _validate_basic(self):
        P = self.p
        if self.auto:
            if P.content_diameter < 3 or P.content_length < 3:
                self.errors.append("Contents must be at least 3 mm in diameter and length.")
            if P.fit_slack < 0:
                self.errors.append("Fit slack cannot be negative.")
        if P.printer_height < 30:
            self.errors.append("Printer height must be at least 30 mm.")
        if P.bed_x < 30 or P.bed_y < 30:
            self.errors.append("Printer bed must be at least 30 × 30 mm.")
        if P.pieces not in (0, 2, 3):
            self.errors.append("Pieces must be automatic, 2 or 3.")
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
        for part, h in self.part_heights().items():
            if h > P.printer_height + 0.05:
                self.warnings.append(f"The {part} ({h:.0f} mm) is taller than the printer height "
                                     f"({P.printer_height:.0f} mm).")
        dia = 2 * self.R_max
        if dia > min(P.bed_x, P.bed_y):
            self.warnings.append(f"The tube (Ø{dia:.0f} mm) is wider than the printer bed "
                                 f"({P.bed_x:g} × {P.bed_y:g} mm).")
        elif not self.plate_layout()[1]:
            self.notes.append("The parts don't all fit on the bed at once - print them in "
                              "separate runs.")
        if self.three:
            self.notes.append(f"Three pieces: glue the cap onto the extension "
                              f"({self.lap:.0f} mm joint). Thin superglue works well.")
            if self.vase:
                self.notes.append("Spiral vase: slice the extension with 0 bottom layers - "
                                  "it is an open tube.")
        if self.nub_arc < 1.5:
            self.warnings.append(f"Nubs are narrow ({self.nub_arc:.2f} mm) and may snap.")
        if self.detent_h > 0 and self.open_force > 30:
            self.warnings.append(f"The click may need about {self.open_force:.0f} N "
                                 f"({self.open_force / 9.81:.1f} kg) of pull to open - hard to "
                                 f"do by hand. Use a gentler setting.")

    # ---------------------------------------------------------------- detent
    def _plan_detent(self):
        """Size the click that holds the cap at the closed position.

        A ramped ridge crosses the floor of ONE home notch.  Closing pushes
        that nub over it and into the pocket below; opening pushes it back.
        To pass, the cap first shifts sideways by the fit clearance, then its
        rim flexes into a slight oval - a soft, predictable spring.  (A ridge
        under every nub would lock the rim into a far stiffer shape: with
        three nubs that takes roughly thirty times the force.)

        The ridge height comes from the pull wanted to open the cap and an
        estimate of the rim's stiffness, so the click feels similar on any
        tube.  The side the nub climbs when opening is the gentler one,
        because pulling a smooth cap is harder than pushing it.  The notch is
        deepened so the nub clears the ridge in the pocket and in row 0.
        """
        p = self.p
        level = int(p.detent_level)
        if level <= 0:
            return
        # rim of the cap as a ring bent by two opposite loads
        t = self.w_v if self.vase else p.cap_wall
        r_mid = self.r_cap_in + 0.5 * t
        width = 1.5 * math.sqrt(r_mid * t)                  # length of rim that flexes with the nub
        self.rim_give = 0.149 * r_mid ** 3 / (PLA_E * width * t ** 3 / 12.0)   # mm of oval per N
        up, down = 1.2, 2.0                                 # ramp lengths / ridge height
        phi = math.atan(FRICTION)
        k_close = math.tan(math.atan(1.0 / up) + phi) + FRICTION    # axial push per N of spring
        k_open = math.tan(math.atan(1.0 / down) + phi) + FRICTION
        most = self.d - 0.3 - 2 * self.c                    # ridge must stay inside the groove
        if p.detent >= 0.05:
            flex = p.detent
            if flex > most + 1e-9:
                self.errors.append("The click interference is too large for this groove depth - "
                                   "reduce it or deepen the grooves.")
                return
        else:
            if most < 0.15:
                self.notes.append("The grooves are too shallow for a click at the closed "
                                  "position - left off.")
                return
            want = CLICK_FORCE.get(level, 6.0)
            flex = min(max(self.rim_give * want / k_open, 0.15), most)
            got = flex / self.rim_give * k_open
            if got < 0.6 * want:
                self.notes.append(
                    f"The click is as firm as this cap allows: about {got:.1f} N to open (the "
                    f"{CLICK_NAME.get(level, 'chosen')} setting aims for {want:.0f} N). A thicker "
                    f"cap wall or deeper grooves would make it firmer.")
        self.detent = flex
        self.detent_h = 2 * self.c + flex                   # clearance at the tip + sideways shift
        self.ridge_up = up * self.detent_h                  # closing side, above the peak (~40°)
        self.ridge_dn = down * self.detent_h                # opening side, towards the pocket (~27°)
        self.open_force = flex / self.rim_give * k_open
        self.close_force = flex / self.rim_give * k_close
        tip_h = self.wp - 2 * self.c - self.dk              # flat face of the nub tip
        gap_pocket, gap_cell = 0.05, 0.2
        self.ridge_off = self.c + tip_h + gap_pocket        # lower foot, above the notch floor
        need = tip_h + gap_pocket + self.ridge_dn + self.ridge_up + gap_cell
        if self.notch < need:
            self.notch = math.ceil(need * 10.0) / 10.0

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
        self.z0 = self.grip
        if p.body_length > 0:
            self.z0 = p.body_length - maze_sec
            if self.z0 < self.grip - 1e-9:
                self.z0 = self.grip
                self.notes.append(f"Body length raised to {self.z0 + maze_sec:.1f} mm "
                                  f"(maze {maze_sec:.1f} mm + grip band {self.grip:.1f} mm).")
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
        self.ridge_cols = np.arange(Bt) == 2 * mz.home + 1          # the click: one notch only
        if self.detent_h > 0:
            self.z_ridge = self.z_notch_floor + self.ridge_off + self.ridge_dn      # its peak

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
        will_split = p.pieces == 3 or self._want3 or (
            p.pieces == 0 and not self.auto and self.H_cap > p.printer_height + 0.05)
        if not will_split:
            self._grow_cap_for_text()
        self._plan_split()
        self._place_text()

    # ------------------------------------------------------------------ text
    def _plan_text(self):
        """Lay out the cap / base text and make room for it.

        Letters keep the requested height.  Lines that fit round the tube are
        stacked; anything longer spirals round and round.  The part grows to
        hold the text (taller grip band, longer cap); letters are only made
        smaller as a last resort, when the printer height cannot hold it.
        """
        p = self.p
        self._layouts = {}
        texts = {"cap": p.cap_text, "base": p.base_text}
        if not any((t or "").strip() for t in texts.values()):
            return
        if p.text_height < 2.5:
            self.warnings.append("Letter height is below 2.5 mm - too small to print, text left off.")
            return
        self._text_grid = min(0.6, max(0.2, p.text_height / 30.0))    # mesh spacing under text
        for key, text in texts.items():
            layout = layout_text(text, p.text_height, TWO_PI * self.R_out, self._text_grid / 2.0)
            if layout is not None:
                self._layouts[key] = layout
        if "base" in self._layouts:
            # the grip band grows to hold the text, but must leave room for a maze
            edge = (min(p.edge_chamfer, p.base_height * 0.4)
                    + (self.w_v if self.vase else 0.5) + 1.0)
            layout = self._fit_text("base", max(8.0, p.printer_height - 30.0 - edge))
            if layout is not None:
                self.grip = max(p.base_height, math.ceil(layout.height + edge))

    def _fit_text(self, key, room):
        """The layout for ``key``, with smaller letters only if ``room`` mm cannot hold it."""
        layout = self._layouts.get(key)
        if layout is None or layout.height <= room + 1e-9:
            return layout
        size = layout.cap_height
        for _ in range(12):
            # a spiral's height goes with the square of the letter size, stacked lines with the size
            ratio = room / layout.height
            size *= 0.97 * (math.sqrt(ratio) if layout.spiral else ratio)
            if size < 2.5:
                break
            layout = layout_text(layout.text, size, TWO_PI * self.R_out, self._text_grid / 2.0)
            if layout is None:
                break
            if layout.height <= room + 1e-9:
                self._layouts[key] = layout
                self.notes.append(
                    f"{key.capitalize()} text: letters reduced to {layout.cap_height:.1f} mm - at "
                    f"full size it needs more height than the printer allows.")
                return layout
        del self._layouts[key]
        self.warnings.append(f"The {key} text is too long to fit on this tube - left off.")
        return None

    def _grow_cap_for_text(self):
        """Lengthen a one-piece cap so its side can hold the cap text."""
        p = self.p
        layout = self._layouts.get("cap")
        if layout is None:
            return
        ch_cap = min(p.edge_chamfer, p.cap_top * 0.8)
        need = self.zn1 + 1.0 + layout.height + ch_cap + 0.5
        limit = max(self.H_cap, p.printer_height)          # never past the printer height
        if self.H_cap < need and min(need, limit) > self.H_cap + 1e-9:
            self.H_cap = math.ceil(min(need, limit) * 10.0 - 1e-6) / 10.0
            self.H_cap = min(self.H_cap, limit)
            self.H_ci = self.H_cap - p.cap_top
            self.cap_ext = self.H_ci - (self.H_body - self.z0)
            self.notes.append(f"Cap lengthened to {self.H_cap:.1f} mm to hold the text.")

    def _place_text(self):
        """Turn the layouts into bands on the cap side and the grip band."""
        p = self.p
        self.bands = {}
        if not self._layouts:
            return
        ch_cap = min(p.edge_chamfer, p.cap_top * 0.8)
        ch = min(p.edge_chamfer, p.base_height * 0.4)
        ch2 = min(0.5, p.base_height * 0.3)
        if self.three:
            cap_lo = self.z_sh + ((self.w_v + self.cj) if self.vase else 0.0) + 0.5
        else:
            cap_lo = self.zn1 + 1.0
        regions = {
            "cap": (cap_lo, self.H_cap - ch_cap - 0.5, p.cap_wall),
            "base": (ch + 0.5, self.z0 - (self.w_v if self.vase else ch2) - 0.5,
                     self.R_out - self.r_plain),
        }
        depth = min(1.5, max(0.2, p.text_depth))
        style = int(p.text_style)              # 0 raised, 1 engraved, 2 second colour (flush)
        if style == 2 and self.vase:
            style = 0
            self.notes.append("Second-colour text needs normal printing - in spiral vase mode "
                              "it is raised instead.")
        centre = math.radians(300.0)           # level text faces the default camera
        start = math.radians(250.0)            # a spiral starts just left of that
        for key in list(self._layouts):
            lo, hi, wall = regions[key]
            layout = self._fit_text(key, hi - lo)
            if layout is None:
                continue
            engrave, inlay, dep = style == 1, style == 2, depth
            if engrave and self.vase and key == "cap":
                engrave = False                # cutting in would eat the cap's fit clearance
                self.notes.append("Cap text is raised in spiral vase mode (engraving would "
                                  "reduce the fit clearance).")
            if inlay:
                # deep enough for two printed walls, so the tube's colour doesn't show through
                dep = min(max(dep, INLAY_DEPTH), wall - 0.8)
                if dep < 0.4:
                    self.warnings.append(f"The wall is too thin for second-colour {key} text - "
                                         f"left off.")
                    continue
            elif engrave and not self.vase:
                dep = min(dep, wall - 0.8)
                if dep < 0.2:
                    self.warnings.append(f"The wall is too thin to engrave the {key} text - left off.")
                    continue
            # keep the mesh under a big block of text to a sensible size
            width = TWO_PI * self.R_out if layout.spiral else layout.block.shape[1] * layout.px
            grid = max(self._text_grid, math.sqrt(width * layout.height / 250000.0))
            z_low = lo + 0.5 * (hi - lo - layout.height)
            # letter edges are rounded over a width the mesh can follow (and to 45° for
            # spiral vase printing), which is what keeps curves from looking blocky
            soft = max((0.46 if self.vase else 0.33) * dep, 0.85 * grid)
            if inlay:
                soft = 0.7 * grid              # flush letters: just enough to place the outline
            self.bands[key] = layout.make_band(self.R_out, z_low, -dep if (engrave or inlay) else dep,
                                               grid, centre, start, soft)
            self.bands[key].layout = layout
            self.bands[key].inlay = inlay

    # ----------------------------------------------------------------- split
    def _plan_split(self):
        """Decide whether the cap is made as an extension + cap end (three pieces)."""
        p = self.p
        self.three = False
        want = p.pieces == 3 or self._want3 or (
            p.pieces == 0 and not self.auto and self.H_cap > p.printer_height + 0.05)
        if not want:
            return
        z_sh = 0.5 * (self.H_cap + self.lap)            # extension top = cap end's shoulder
        z_s = z_sh - self.j                             # where the joint starts
        if self.vase:
            low_ok = z_s >= self.zn1 + 2.0
            high_ok = z_sh + self.w_v + self.cj + 1.0 <= self.H_ci - 1.0
        else:
            low_ok = z_s - (self.R_sk - self.r_cap_in) >= self.zn1 + 2.0
            high_ok = z_sh + 1.0 <= self.H_ci - 1.0
        if not (low_ok and high_ok):
            self.notes.append("The cap is too short to split into an extension and a cap end - "
                              "made as two pieces.")
            return
        self.three, self.z_sh, self.z_s = True, z_sh, z_s
        if not self.vase and (p.cap_wall - self.cj) / 2.0 - (p.grip_depth if p.grip_flutes else 0) < 0.6:
            self.warnings.append("The glued joint's walls are thin - use a thicker cap wall "
                                 "(2 mm or more) or shallower grip flutes.")

    def part_heights(self) -> dict:
        """Print height of every piece."""
        if self.three:
            return {"body": self.H_body, "extension": self.z_sh,
                    "cap": self.H_cap - self.z_sh + self.lap}
        return {"body": self.H_body, "cap": self.H_cap}

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
        if self.detent_h > 0:                              # click ridge in one home notch
            t = np.minimum((z - (self.z_ridge - self.ridge_dn)) / self.ridge_dn,
                           ((self.z_ridge + self.ridge_up) - z) / self.ridge_up)
            on = self.ridge_cols[bt] & inside & (t > 0)
            r = np.where(on, np.maximum(r, self.r_floor + self.detent_h * np.clip(t, 0, 1)), r)
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
    def _theta_grid(self, breaks, r_ref, bands=()):
        """Global theta samples: break pairs + uniform fill (+ fine samples under text)."""
        eps_t = EPS / r_ref
        breaks = np.asarray(breaks, dtype=float)
        lines = np.mod(np.concatenate([breaks - eps_t, breaks + eps_t]), TWO_PI)
        n_u = int(math.ceil(TWO_PI * self.R_out / self.p.resolution))
        if self.p.grip_flutes > 0:
            n_u = max(n_u, self.p.grip_flutes * 16)
        n_u = max(n_u, 48)
        uni = np.linspace(0.0, TWO_PI, n_u, endpoint=False)
        for band in bands:                       # text needs a much finer grid under it
            uni = np.concatenate([uni[~band.covers(uni)], band.theta_lines()])
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
        keep = np.concatenate([[True], np.diff(g) > 0.3 * eps_t])   # no sliver columns
        g = g[keep]
        if len(g) > 1 and g[0] + TWO_PI - g[-1] <= 0.3 * eps_t:
            g = g[:-1]
        return g

    @staticmethod
    def _uniq(vals, lo, hi):
        v = np.array(sorted(x for x in vals if lo - 1e-9 <= x <= hi + 1e-9))
        keep = np.concatenate([[True], np.diff(v) > 1e-6])
        return v[keep]

    def _text_rows(self, key, th, descending=False):
        """Heights and radial offsets (T, K) of a text band, for the wall under it."""
        band = self.bands.get(key)
        if band is None:
            return np.zeros(0), np.zeros((len(th), 0))
        z = band.z_lines()
        off = band.sample(th, z, limit_slope=self.vase)
        if band.inlay:
            off = np.zeros_like(off)           # flush: the letters are cut out after sweeping
        if descending:
            z, off = z[::-1], off[:, ::-1]
        return z, off

    def _band_list(self, key):
        return [self.bands[key]] if key in self.bands else []

    def _cut_inlay(self, key, mesh, th, k0, descending=False):
        """Cut second-colour text out of a swept part.

        ``k0`` is the profile point where the text rows start.  The letters
        themselves are kept in ``self.inlays[key]``; they fill the cut exactly.
        """
        band = self.bands.get(key)
        if band is None or not band.inlay:
            return mesh
        cover = np.abs(band.sample(th, band.z_lines())) / abs(band.depth)
        if descending:
            cover = cover[:, ::-1]
        verts, faces, letters = _inlay_cut(*mesh, len(th), k0, cover, abs(band.depth))
        if letters is not None:
            self.inlays[key] = letters
        return verts, faces

    def _nub_lines(self):
        zl = []
        if self.dkb > 0:
            zl += [self.zn0, self.zn0 + self.dkb]
        else:
            zl += [self.zn0 - EPS, self.zn0 + EPS]
        if self.dk > 0:
            zl += [self.zn1 - self.dk, self.zn1, self.zn1 + EPS]
        else:
            zl += [self.zn1 - EPS, self.zn1 + EPS]
        return zl

    def _nub_breaks(self):
        half = 0.5 * self.nub_th
        return np.concatenate([self.nub_angles - half, self.nub_angles + half])

    # ------------------------------------------------------------ meshes
    def body_mesh(self):
        if self._body is None:
            self._body = self._make_body()
        return self._body

    def cap_mesh(self):
        """The whole cap as one piece (also the assembled shape of a split cap)."""
        if self._cap is None:
            self._cap = self._make_cap()
        return self._cap

    def _make_body(self):
        p = self.p
        th = self._theta_grid(self.th_edges[:-1], self.r_maze, self._band_list("base"))
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
            zl += [self.z_ridge - self.ridge_dn, self.z_ridge, self.z_ridge + self.ridge_up]
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
        tz, toff = self._text_rows("base", th)             # text round the grip band
        k_text = len(cols_r)
        for k, z in enumerate(tz):
            col(R + toff[:, k], z)
        if not self.vase:
            col(R, self.z0 - ch2)
            col(R - ch2, self.z0)
        rz = self.body_radius(th[:, None], zg[None, :])    # (T, K)
        for k, z in enumerate(zg):
            col(rz[:, k], z)
        rr, zz = np.stack(cols_r, 1), np.array(cols_z)
        if self.vase:
            # solid: the slicer's single spiral wall follows this outer surface
            self.profiles["body"] = (th, rr, zz)
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
        self.profiles["body"] = (th, rr, zz)
        return self._cut_inlay("base", _sweep(th, rr, zz, 0.0, p.bottom_thickness), th, k_text)

    def _cap_columns(self, th):
        cols_r, cols_z = [], []

        def col(r, z):
            cols_r.append(np.broadcast_to(r, th.shape).astype(float))
            cols_z.append(float(z))
        return cols_r, cols_z, col

    def _make_cap(self):
        """One-piece cap: closed end, nubs at the rim."""
        p = self.p
        bands = [] if self.three else self._band_list("cap")   # split caps carry it on the end
        th = self._theta_grid(self._nub_breaks(), self.r_tip, bands)
        zl = [0.0, self.H_ci] + self._nub_lines()
        R = self.R_out - self.flute(th)
        ch = min(p.edge_chamfer, p.cap_top * 0.8)
        ch2 = 0.4
        cols_r, cols_z, col = self._cap_columns(th)

        if self.vase:
            # solid cap whose outer skin is the inner fit surface + one line width:
            # the nubs become inward dents of the single printed wall
            top = self.H_cap - ch if ch > 0.01 else self.H_cap
            for band in bands:
                zl += list(band.z_lines())
            zg = self._uniq(zl + [top], 0.0, top)[::-1]
            if ch > 0.01:
                col(R - ch, self.H_cap)
            rz = self.cap_radius(th[:, None], zg[None, :]) + self.w_v
            for band in bands:
                rz = rz + band.sample(th, zg, limit_slope=True)
            for k, z in enumerate(zg):
                col(rz[:, k], z)
            rr, zz = np.stack(cols_r, 1), np.array(cols_z)
            self.profiles["cap_whole"] = (th, rr, zz)
            return _sweep(th, rr, zz, self.H_cap, 0.0)

        zg = self._uniq(zl, 0.0, self.H_ci)
        if ch > 0.01:
            col(R - ch, self.H_cap)
            col(R, self.H_cap - ch)
        else:
            col(R, self.H_cap)
        k_text = len(cols_r)
        if bands:
            tz, toff = self._text_rows("cap", th, descending=True)
            for k, z in enumerate(tz):
                col(R + toff[:, k], z)
        col(R, ch2)
        col(R - ch2, 0.0)
        rz = self.cap_radius(th[:, None], zg[None, :])
        for k, z in enumerate(zg):
            col(rz[:, k], z)
        rr, zz = np.stack(cols_r, 1), np.array(cols_z)
        self.profiles["cap_whole"] = (th, rr, zz)
        mesh = _sweep(th, rr, zz, self.H_cap, self.H_ci)
        return self._cut_inlay("cap", mesh, th, k_text, descending=True) if bands else mesh

    def _make_extension(self):
        """Lower piece of a split cap: an open tube with the nubs at its rim.

        Its far end takes the cap end: a socket (normal printing; 45° inside so
        it prints joint-down without support) or a plain end the cap end's
        flared collar slips over (spiral vase).
        """
        th = self._theta_grid(self._nub_breaks(), self.r_tip)
        cols_r, cols_z, col = self._cap_columns(th)
        if self.vase:
            zg = self._uniq(self._nub_lines() + [0.0, self.z_sh], 0.0, self.z_sh)[::-1]
            rz = self.cap_radius(th[:, None], zg[None, :]) + self.w_v
            for k, z in enumerate(zg):
                col(rz[:, k], z)
            rr, zz = np.stack(cols_r, 1), np.array(cols_z)
            self.profiles["extension"] = (th, rr, zz)
            return _sweep(th, rr, zz, self.z_sh, 0.0)        # solid; printed with no bottom

        R = self.R_out - self.flute(th)
        z_c0 = self.z_s - (self.R_sk - self.r_cap_in)       # socket's 45° lead starts here
        zg = self._uniq(self._nub_lines() + [0.0, z_c0], 0.0, z_c0)
        rz = self.cap_radius(th[:, None], zg[None, :])
        for k, z in enumerate(zg):                           # inner wall, rim upwards
            col(rz[:, k], z)
        col(self.R_sk, self.z_s)
        col(self.R_sk, self.z_sh)
        chb = 0.3
        col(R - chb, self.z_sh)                              # end face (on the bed)
        col(R, self.z_sh - chb)
        col(R, 0.4)                                          # outer wall back down to the rim
        col(R - 0.4, 0.0)
        rr, zz = np.stack(cols_r, 1), np.array(cols_z)
        self.profiles["extension"] = (th, rr, zz)
        return _sweep_loop(th, rr, zz)

    def _make_cap_end(self):
        """Upper piece of a split cap: the closed end, glued onto the extension."""
        p = self.p
        bands = self._band_list("cap")
        th = self._theta_grid([], self.R_out, bands)
        R = self.R_out - self.flute(th)
        ch = min(p.edge_chamfer, p.cap_top * 0.8)
        cols_r, cols_z, col = self._cap_columns(th)
        if self.vase:
            # flared collar that slips over the extension's plain end
            top = self.H_cap - ch if ch > 0.01 else self.H_cap
            z_r1 = self.z_sh + self.w_v + self.cj            # 45° flare ends here
            zl = [top, z_r1]
            for band in bands:
                zl += list(band.z_lines())
            zg = self._uniq(zl, z_r1, top)[::-1]
            if ch > 0.01:
                col(R - ch, self.H_cap)
            rz = np.full((len(th), len(zg)), self.r_cap_in + self.w_v)
            for band in bands:
                rz = rz + band.sample(th, zg, limit_slope=True)
            for k, z in enumerate(zg):
                col(rz[:, k], z)
            col(self.R_fl, self.z_sh)
            col(self.R_fl, self.z_s)
            rr, zz = np.stack(cols_r, 1), np.array(cols_z)
            self.profiles["cap"] = (th, rr, zz)
            return _sweep(th, rr, zz, self.H_cap, self.z_s)

        z_bot = self.z_sh - self.lap                          # tip of the spigot
        lead = 0.3
        if ch > 0.01:
            col(R - ch, self.H_cap)
            col(R, self.H_cap - ch)
        else:
            col(R, self.H_cap)
        k_text = len(cols_r)
        tz, toff = self._text_rows("cap", th, descending=True)
        for k, z in enumerate(tz):
            col(R + toff[:, k], z)
        col(R, self.z_sh)                                    # shoulder that meets the extension
        col(self.R_sp, self.z_sh)
        col(self.R_sp, z_bot + lead)
        col(self.R_sp - lead, z_bot)
        col(self.r_cap_in, z_bot)
        col(self.r_cap_in, self.H_ci)
        rr, zz = np.stack(cols_r, 1), np.array(cols_z)
        self.profiles["cap"] = (th, rr, zz)
        return self._cut_inlay("cap", _sweep(th, rr, zz, self.H_cap, self.H_ci), th, k_text,
                               descending=True)

    # ------------------------------------------------------------- parts
    def parts(self):
        """The printable pieces, in assembly coordinates."""
        if self._parts is None:
            out = [Part("body", "Body", *self.body_mesh(), frame="body", top=self.H_body,
                        height=self.H_body)]
            if self.three:
                hs = self.part_heights()
                out.append(Part("extension", "Extension", *self._make_extension(), frame="cap",
                                top=self.z_sh, height=hs["extension"]))
                out.append(Part("cap", "Cap", *self._make_cap_end(), frame="cap",
                                top=self.H_cap, height=hs["cap"]))
            else:
                out.append(Part("cap", "Cap", *self.cap_mesh(), frame="cap", top=self.H_cap,
                                height=self.H_cap))
                if "cap_whole" in self.profiles:
                    self.profiles["cap"] = self.profiles["cap_whole"]
            for part, key in ((out[0], "base"), (out[-1], "cap")):
                part.inlay = self.inlays.get(key)       # second-colour letters, if any
            self._parts = out
        return self._parts

    def display_parts(self):
        """Parts for the 3D view: in vase mode, the thin shells that get printed.

        The exported vase models are solid (the slicer hollows them); here the
        inner skin is added one line width inside, the way spiral vase mode
        offsets each layer, so the section view shows what will be printed.
        """
        parts = self.parts()
        if not self.vase:
            return parts
        if self._display is None:
            w, out = self.w_v, []
            for part in parts:
                th, rr, zz = self.profiles[part.name]
                if part.name == "body":
                    v, f = _shell_closed(th, rr, zz, w, self.p.bottom_thickness)
                elif part.name == "extension":
                    v, f = _shell_open(th, rr, zz, w)
                else:
                    v, f = _shell_closed(th, rr, zz, w, self.p.cap_top)
                out.append(Part(part.name, part.label, v, f, part.frame, part.top, part.height))
            self._display = out
        return self._display

    def display_meshes(self):
        """(body, whole cap) display meshes - kept for callers that want just two."""
        d = {part.name: (part.verts, part.faces) for part in self.display_parts()}
        if self.three:
            cap = self.cap_mesh()
            if self.vase:
                cap = _shell_closed(*self.profiles["cap_whole"], self.w_v, self.p.cap_top)
            return d["body"], cap
        return d["body"], d["cap"]

    @property
    def R_max(self) -> float:
        """Largest radius of any part (for the bed check and plate layout)."""
        r = self.R_out
        if any(b.depth > 0 for b in self.bands.values()):
            r += max(b.depth for b in self.bands.values())
        if self.three and self.vase:
            r = max(r, self.R_fl)
        return r

    def plate_layout(self, gap: float = 8.0):
        """Where each part goes on the bed: ([(x, y), ...] about the centre, fits)."""
        n = 3 if self.three else 2
        D = 2.0 * self.R_max
        bx, by = self.p.bed_x, self.p.bed_y
        cols = max(1, min(n, int((bx + gap) // (D + gap))))
        rows = int(math.ceil(n / cols))
        fits = (cols * D + (cols - 1) * gap <= bx + 1e-6
                and rows * D + (rows - 1) * gap <= by + 1e-6)
        pos = [((i % cols - (cols - 1) / 2.0) * (D + gap),
                ((rows - 1) / 2.0 - i // cols) * (D + gap)) for i in range(n)]
        return pos, fits

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

    def solution_columns(self):
        """Columns (lo, hi) the first nub visits along the solution, not wrapped round."""
        c = lo = hi = self.maze.home
        for dc, _ in self.maze.moves:
            c += dc
            lo, hi = min(lo, c), max(hi, c)
        return lo, hi

    def map_window(self):
        """Which columns the unrolled maze map shows: (first column, number of columns).

        The map is the tube's surface unrolled.  Cutting it at a fixed angle
        often sends the route off one edge and back in at the other, so the
        cut is chosen from the solution instead:

        * if the route is narrow enough, no nub's route crosses the cut (each
          route sits centred in its own sector);
        * otherwise the highlighted route is kept whole, centred on the page;
        * a route that winds more than once round the tube gets a wider map -
          more than one turn is shown (the maze just repeats), so the line is
          still continuous.

        The first column is not wrapped: it lines up with solution_columns().
        """
        lo, hi = self.solution_columns()
        span = hi - lo + 1
        if span <= self.Ns:
            return lo - (self.Ns - span) // 2, self.N
        if span <= self.N:
            return lo - (self.N - span) // 2, self.N
        return lo - 1, span + 2

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
        parts = self.parts()

        def shell(part, bottom):
            # printed volume = one line width over the side walls + a solid bottom
            v, f = part.verts, part.faces
            n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
            area = 0.5 * np.linalg.norm(n, axis=1)
            side = np.abs(n[:, 2]) < 0.95 * np.linalg.norm(n, axis=1)
            return (area[side].sum() * self.w_v + math.pi * self.R_out ** 2 * bottom) / 1000.0

        vols = {}
        for part in parts:
            if self.vase:
                bottom = {"body": p.bottom_thickness, "cap": p.cap_top}.get(part.name, 0.0)
                vols[part.name] = shell(part, bottom)
            else:
                vols[part.name] = mesh_volume(part.verts, part.faces) / 1000.0
        total = sum(vols.values())
        if self.vase:
            bore = f"Ø{2 * (self.r_floor - self.w_v):.1f} (ribbed)"
            vol_label = "Volume (printed)"
            pla = f"~{total * 1.24:.0f} g (spiral vase, {self.w_v:.2f} mm wall)"
        else:
            bore = f"Ø{2 * self.r_cav:.1f}"
            vol_label = "Volume (solid)"
            pla = f"~{total * 1.24 * 0.55:.0f} g (at ~40% infill)"
        # usable length when closed: body cavity + the cap's extension past the rim
        storage = self.z0 + self.H_ci - p.bottom_thickness
        maze_sec = self.H_body - self.z0
        cavity = f"{bore} × {storage:.1f} mm"
        if self.cap_ext > self.top_gap + 1e-6:
            cavity += f" (cap adds {self.cap_ext:.0f})"

        head = {}
        if self.auto:
            what = f"Ø{p.content_diameter:g} × {p.content_length:g} mm"
            head["Contents"] = f"{p.preset}: {what}" if p.preset else what
            if self.three:
                head["Layout"] = "three pieces: body, extension and cap"
            else:
                head["Layout"] = ("shared by body and cap" if self.split
                                  else "all in the body, cap covers the maze")
        heights = self.part_heights()
        tallest = max(heights.values())
        fits = "✔" if tallest <= p.printer_height + 0.05 else "✘ too tall"
        head["Tallest part"] = f"{tallest:.1f} of {p.printer_height:g} mm printer height {fits}"
        dia = 2 * self.R_max
        _, plate_ok = self.plate_layout()
        if dia > min(p.bed_x, p.bed_y):
            bed = "✘ too wide"
        else:
            bed = "✔" if plate_ok else "✔ (not all parts at once)"
        head["Printer bed"] = f"Ø{dia:.1f} on {p.bed_x:g} × {p.bed_y:g} mm {bed}"

        out = {
            **head,
            "Outer diameter": f"{2 * self.R_out:.1f} mm",
            "Closed length": f"{self.z0 + self.H_cap:.1f} mm",
            "Body length": f"{self.H_body:.1f} mm (maze {maze_sec:.1f}, plain {self.z0:.1f})",
        }
        if self.three:
            out["Extension length"] = f"{heights['extension']:.1f} mm"
            out["Cap length"] = (f"{heights['cap']:.1f} mm (glued onto the extension: "
                                 f"{self.H_cap:.1f} mm together)")
        else:
            out["Cap length"] = f"{self.H_cap:.1f} mm"
        out["Storage inside"] = cavity
        if self.bands:
            bits = []
            for key, band in self.bands.items():
                lay = band.layout
                how = (f"second colour, flush ({abs(band.depth):.1f} mm deep)" if band.inlay
                       else f"{abs(band.depth):.1f} mm {'raised' if band.depth > 0 else 'engraved'}")
                bits.append(f"{key}: {lay.describe()}, {lay.cap_height:.1f} mm letters, {how}")
            out["Text"] = "; ".join(bits)
        out.update({
            "Maze grid": f"{self.N} columns × {self.M} rows ({self.Ns} per nub)",
            "Path width": f"{self.path_arc_top:.2f} mm (floor {self.path_arc_floor:.2f} mm)",
            "Nub size": f"{self.nub_arc:.2f} × {self.zn1 - self.zn0:.2f} mm, {self.r_cap_in - self.r_tip:.2f} deep",
            "Home notch": (f"{self.notch:.1f} mm" + (" (deepened to fit)" if self.notch > p.notch_depth + 1e-9 else "")
                           if self.notch > 0 else "off"),
            "Home click": (f"{'set by hand' if p.detent >= 0.05 else CLICK_NAME.get(int(p.detent_level), 'on')}"
                           f": about {self.open_force:.0f} N to open, {self.close_force:.0f} N to "
                           f"close (estimate for PLA); the rim flexes {self.detent:.2f} mm"
                           if self.detent_h > 0 else "off"),
            "Solution": f"{len(mz.moves)} moves, {mz.turns()} turns",
            "Dead ends": f"{mz.dead_ends()} per sector",
            "Triangles": " / ".join(f"{pt.name} {len(pt.faces):,}" for pt in parts),
            vol_label: " / ".join(f"{k} {v:.1f} cm³" for k, v in vols.items()),
            "PLA estimate": pla,
        })
        return out


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
    quads1, quads2 = _split_quads(r, a, b, c, d)
    fan_s = np.stack([np.full(T, vs), i2 * K, i * K], 1)
    fan_e = np.stack([np.full(T, ve), i * K + K - 1, i2 * K + K - 1], 1)
    faces = np.concatenate([fan_s, quads1, quads2, fan_e]).astype(np.int64)
    if mesh_volume(verts, faces) < 0:
        faces = faces[:, ::-1].copy()
    return verts, faces


def _split_quads(r, a, b, c, d):
    """Two triangles per grid quad, split along the diagonal with the smaller step.

    On a relief (text, bevels) a fixed diagonal makes slanted edges saw-toothed;
    joining the two corners that are closest in radius follows the shape instead.
    """
    rf = r.ravel()
    other = np.abs(rf[b] - rf[d]) < np.abs(rf[a] - rf[c]) - 1e-12
    t1 = np.where(other[:, None], np.stack([a, b, d], 1), np.stack([a, b, c], 1))
    t2 = np.where(other[:, None], np.stack([b, c, d], 1), np.stack([a, c, d], 1))
    return t1, t2


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


@dataclass
class Part:
    """One printable piece, in assembly coordinates.

    frame "body": z = 0 at the bottom of the body.  frame "cap": z = 0 at the
    cap's rim (the closed position puts that on the body's shoulder).  Cap-frame
    pieces are printed upside down, so ``top`` (their highest z) sits on the bed.
    """
    name: str
    label: str
    verts: np.ndarray
    faces: np.ndarray
    frame: str
    top: float
    height: float
    inlay: tuple | None = None    # (verts, faces) of flush second-colour lettering, same frame

    def _to_print(self, verts) -> np.ndarray:
        if self.frame == "body":
            return verts
        return transform(verts, flip_x=True, offset=(0, 0, self.top))

    def print_verts(self) -> np.ndarray:
        """Vertices in print orientation, standing on z = 0."""
        return self._to_print(self.verts)

    def inlay_print_verts(self) -> np.ndarray:
        """The lettering's vertices in print orientation: in place, inside the part."""
        return self._to_print(self.inlay[0])


def _sweep_loop(th, r, z):
    """Sweep a closed profile loop (a tube open at both ends) around the z axis."""
    T, K = r.shape
    x = r * np.cos(th)[:, None]
    y = r * np.sin(th)[:, None]
    zz = np.broadcast_to(z[None, :], (T, K))
    verts = np.stack([x.ravel(), y.ravel(), zz.ravel()], 1)
    i = np.arange(T)
    i2 = (i + 1) % T
    k = np.arange(K)
    k2 = (k + 1) % K
    a = (i[:, None] * K + k[None, :]).ravel()
    b = (i2[:, None] * K + k[None, :]).ravel()
    c = (i2[:, None] * K + k2[None, :]).ravel()
    d = (i[:, None] * K + k2[None, :]).ravel()
    faces = np.concatenate(_split_quads(r, a, b, c, d)).astype(np.int64)
    if mesh_volume(verts, faces) < 0:
        faces = faces[:, ::-1].copy()
    return verts, faces


def _shell_closed(th, rr, zz, w, thickness):
    """Thin shell of a solid profile: closed (``thickness``) at its first end, open at the last."""
    sign = 1.0 if zz[-1] > zz[0] else -1.0
    z_in = zz[0] + sign * thickness
    keep = sign * (zz - z_in) > 1e-6
    order = np.argsort(zz)
    r_in = np.array([np.interp(z_in, zz[order], row[order]) for row in rr]) - w
    r2 = np.concatenate([rr, rr[:, keep][:, ::-1] - w, r_in[:, None]], 1)
    z2 = np.concatenate([zz, zz[keep][::-1], [z_in]])
    return _sweep(th, r2, z2, zz[0], z_in)


def _shell_open(th, rr, zz, w):
    """Thin shell of a solid profile, open at both ends (a printed tube with no bottom)."""
    return _sweep_loop(th, np.concatenate([rr, rr[:, ::-1] - w], 1),
                       np.concatenate([zz, zz[::-1]]))


def print_plate(design: "TubeDesign", gap: float = 8.0):
    """All parts in print orientation, arranged for the bed, as one mesh on z = 0."""
    positions, _ = design.plate_layout(gap)
    verts, faces, n = [], [], 0
    for part, (x, y) in zip(design.parts(), positions):
        v = part.print_verts() + np.array([x, y, 0.0])
        verts.append(v)
        faces.append(part.faces + n)
        n += len(v)
    return np.concatenate(verts), np.concatenate(faces)


def print_plate_text(design: "TubeDesign", gap: float = 8.0):
    """The second-colour lettering of all parts, placed to match print_plate(); None if none."""
    positions, _ = design.plate_layout(gap)
    verts, faces, n = [], [], 0
    for part, (x, y) in zip(design.parts(), positions):
        if part.inlay is None:
            continue
        v = part.inlay_print_verts() + np.array([x, y, 0.0])
        verts.append(v)
        faces.append(part.inlay[1] + n)
        n += len(v)
    if not verts:
        return None
    return np.concatenate(verts), np.concatenate(faces)


# Marching squares over a grid cell with corners a b c d (slots 0-3, in the sweep's
# winding) and the points where the outline crosses its edges ab bc cd da (slots 4-7).
# For each case (bit n set = corner n inside the outline): triangles covering the part
# of the cell inside it.  No triangle edge joins two crossing points except along the
# outline itself, which is what keeps the cut-out letters a closed 2-manifold.
_MS_IN = {
    1: ((0, 4, 7),), 2: ((1, 5, 4),), 4: ((2, 6, 5),), 8: ((3, 7, 6),),
    3: ((0, 1, 5), (0, 5, 7)), 6: ((1, 2, 6), (1, 6, 4)),
    12: ((2, 3, 7), (2, 7, 5)), 9: ((3, 0, 4), (3, 4, 6)),
    14: ((2, 3, 7), (2, 7, 4), (2, 4, 1)), 13: ((3, 0, 4), (3, 4, 5), (3, 5, 2)),
    11: ((0, 1, 5), (0, 5, 6), (0, 6, 3)), 7: ((1, 2, 6), (1, 6, 7), (1, 7, 0)),
    5: ((0, 4, 7), (2, 6, 5)), 10: ((1, 5, 4), (3, 7, 6)),
    15: ((0, 1, 2), (0, 2, 3)),
}
# ... and the part outside it: the complement (two opposite corners inside leave a hexagon)
_MS_OUT = {c: _MS_IN[15 - c] for c in range(1, 15) if c not in (5, 10)}
_MS_OUT[5] = ((1, 5, 6), (1, 6, 3), (1, 3, 7), (1, 7, 4))
_MS_OUT[10] = ((0, 4, 5), (0, 5, 2), (0, 2, 6), (0, 6, 7))


def _inlay_cut(verts, faces, T, k0, cover, depth):
    """Cut flush lettering out of a swept wall, for printing in a second colour.

    verts, faces: a mesh from _sweep (vertex i*K + k is profile point k at angle
    i).  cover: (T, n) coverage 0..1 of the lettering at profile points
    k0 .. k0+n-1, which lie on an outer wall.  Where it is above 0.5 the wall is
    cut ``depth`` deep.  The outline is placed by interpolation, so it runs
    smoothly through the grid cells instead of along them (marching squares).

    Returns (verts, faces, letters): the wall with the letters cut out, and the
    letters as a closed (verts, faces) mesh that fills the cut exactly - its
    inner faces are the very triangles of the cut - or None if nothing is covered.
    """
    K = (len(verts) - 2) // T
    n = cover.shape[1]
    cover = np.array(cover, dtype=float)
    cover[:, 0] = cover[:, -1] = 0.0                      # the first and last rows stay whole
    inside = cover > 0.5
    if not inside.any():
        return verts, faces, None
    i2 = (np.arange(T) + 1) % T
    grid = np.arange(T)[:, None] * K + k0 + np.arange(n)[None, :]      # vertex ids (T, n)
    P = verts[grid]

    # where the outline crosses grid edges: those round the tube, then those along it
    def crossings(m0, m1, p0, p1):
        # coverage is S-shaped across a letter's edge; its logit is close to linear in
        # distance, so interpolating that puts the outline where it really is
        l0, l1 = (np.log(m / (1.0 - m)) for m in (np.clip(m0, 0.02, 0.98), np.clip(m1, 0.02, 0.98)))
        t = np.clip(-l0 / (l1 - l0), 0.08, 0.92)
        return p0 + t[:, None] * (p1 - p0)

    mix_t = inside != inside[i2]                                        # edge (i,j)-(i+1,j)
    mix_z = inside[:, :-1] != inside[:, 1:]                             # edge (i,j)-(i,j+1)
    cross = np.concatenate([
        crossings(cover[mix_t], cover[i2][mix_t], P[mix_t], P[i2][mix_t]),
        crossings(cover[:, :-1][mix_z], cover[:, 1:][mix_z], P[:, :-1][mix_z], P[:, 1:][mix_z])])
    n_t = int(mix_t.sum())
    id_t = np.full(mix_t.shape, -1, dtype=np.int64)
    id_t[mix_t] = np.arange(n_t)
    id_z = np.full(mix_z.shape, -1, dtype=np.int64)
    id_z[mix_z] = n_t + np.arange(int(mix_z.sum()))

    # per cell (T, n-1): its corners a b c d, their state, and the crossings on ab bc cd da
    def cells(a):
        return [a[:, :-1], a[i2][:, :-1], a[i2][:, 1:], a[:, 1:]]

    ins = cells(inside)
    case = ins[0] * 1 + ins[1] * 2 + ins[2] * 4 + ins[3] * 8
    edges = [id_t[:, :-1], id_z[i2], id_t[:, 1:], id_z]
    picks = {c: case == c for c in range(1, 16)}

    def build(slots, table):
        out = []
        for c, tris in table.items():
            sel = picks[c]
            if sel.any():
                sl = [x[sel] for x in slots]
                out += [np.stack([sl[a], sl[b], sl[d]], axis=1) for a, b, d in tris]
        return np.concatenate(out)

    # the wall: every cell the outline touches is re-made, split along the outline
    on_grid = (faces < T * K).all(axis=1)
    fi, fk = faces // K, faces % K - k0
    kmin, kmax, imin, imax = fk.min(axis=1), fk.max(axis=1), fi.min(axis=1), fi.max(axis=1)
    in_band = on_grid & (kmin >= 0) & (kmax <= n - 1) & (kmax - kmin == 1)
    col = np.where(imax - imin == 1, imin, imax)          # the last column wraps to the first
    drop = np.zeros(len(faces), dtype=bool)
    drop[in_band] = case[col[in_band], kmin[in_band]] > 0

    base = len(verts)
    slots = cells(grid) + [e + base for e in edges]
    new = np.concatenate([build(slots, _MS_IN), build(slots, _MS_OUT)])
    flat = np.concatenate([verts, cross])                 # before the cut: all still in the wall

    def outward(f):
        tri = flat[f]
        nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        return float((nrm[:, :2] * tri[:, 0, :2]).sum())

    flip = outward(new) * outward(faces[drop]) < 0        # match the winding of what it replaces
    if flip:
        new = new[:, ::-1]
    cut = flat.copy()
    gi = grid[inside]
    r = np.hypot(cut[gi, 0], cut[gi, 1])
    cut[gi, :2] *= ((r - depth) / r)[:, None]
    out_faces = np.concatenate([faces[~drop], new])

    # the letters: the covered pieces of those cells, once on the wall's surface and
    # once on the floor of the cut, joined along the outline (where the depth is zero)
    n_in = len(gi)
    idx = np.full(grid.shape, -1, dtype=np.int64)
    idx[inside] = np.arange(n_in)
    corners = cells(idx)
    ends = [e + 2 * n_in for e in edges]
    outer = build(corners + ends, _MS_IN)
    inner = build([c + n_in for c in corners] + ends, _MS_IN)
    if flip:
        outer = outer[:, ::-1]
    else:
        inner = inner[:, ::-1]
    lv = np.concatenate([flat[gi], cut[gi], cross])
    lf = np.concatenate([outer, inner])
    if mesh_volume(lv, lf) < 0:
        lf = lf[:, ::-1].copy()
    return cut, out_faces, (lv, lf)


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
