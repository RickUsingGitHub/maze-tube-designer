"""2D unrolled view of the maze surface (as seen from outside the tube)."""

from __future__ import annotations

import math

import numpy as np

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .geometry import LOCK_TURN

TWO_PI = 2.0 * math.pi


class MazeMapWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.design = None
        self.show_solution = True
        self.marker = None          # (phi, Z) cap pose to mark nubs during animation
        self.dark = True
        self.setMinimumHeight(160)

    def set_design(self, design):
        self.design = design
        self.update()

    def set_marker(self, pose):
        self.marker = pose
        self.update()

    def set_show_solution(self, on):
        self.show_solution = on
        self.update()

    # ------------------------------------------------------------- colours
    def _colors(self):
        if self.dark:
            return dict(bg=QColor(30, 33, 38), ridge=QColor(236, 150, 68), groove=QColor(64, 40, 22),
                        text=QColor(205, 210, 218), faint=QColor(110, 116, 126),
                        path=QColor(80, 200, 255), path2=QColor(80, 200, 255, 90),
                        home=QColor(120, 230, 120), exit=QColor(255, 90, 110), nub=QColor(255, 255, 255))
        return dict(bg=QColor(255, 255, 255), ridge=QColor(240, 170, 90), groove=QColor(90, 55, 30),
                    text=QColor(30, 30, 30), faint=QColor(150, 150, 150),
                    path=QColor(0, 120, 220), path2=QColor(0, 120, 220, 90),
                    home=QColor(0, 150, 60), exit=QColor(210, 30, 60), nub=QColor(0, 0, 0))

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self.render_map(p, QRectF(self.rect()))
        p.end()

    # --------------------------------------------------------------- paint
    def render_map(self, p: QPainter, rect: QRectF, title: str | None = None):
        col = self._colors()
        p.fillRect(rect, col["bg"])
        d = self.design
        if d is None or d.maze is None:
            p.setPen(col["text"])
            p.drawText(rect, Qt.AlignCenter, "No valid design")
            return
        # everything scales with the output size (widget or export image)
        fpx = max(11.0, min(40.0, rect.height() / 26.0, rect.width() / 70.0))
        fh = fpx * 1.3
        font = QFont(p.font())
        font.setPixelSize(int(round(fpx)))
        p.setFont(font)
        a = fpx * 0.7                                  # exit arrow size
        title_h = fh * 1.6 if title else 0.0
        top_pad = title_h + 2.2 * a + fh + 6
        pad_l = pad_r = fh * 1.8
        pad_b = 2 * fh + 10
        wide = d.map_window()[1] > d.N                 # more than one turn is shown
        if wide:
            pad_b += fh * 1.3                          # room for a line explaining why
        avail = QRectF(rect.left() + pad_l, rect.top() + top_pad,
                       rect.width() - pad_l - pad_r, rect.height() - top_pad - pad_b)
        if avail.width() < 20 or avail.height() < 20:
            return
        r = d.r_maze
        z_lo, z_hi = d.z0, d.H_body
        # The map is the tube unrolled.  Its window of columns is chosen from the
        # solution, so the route stays on the page instead of running off one
        # edge and back in at the other (see TubeDesign.map_window).
        col0, n_cols = d.map_window()
        N, Ns, pitch = d.N, d.Ns, d.P_th
        span = n_cols * pitch                          # angle shown (2π unless the route winds further)
        w_mm = span * r
        h_mm = z_hi - z_lo
        s = min(avail.width() / w_mm, avail.height() / h_mm)
        ox = avail.left() + (avail.width() - w_mm * s) / 2.0
        oy = avail.top() + (avail.height() - h_mm * s) / 2.0 + h_mm * s
        tw = fpx * 7                                   # label box width

        def X(th):                                     # angle measured from the left edge
            return ox + th * r * s

        def XC(column):                                # centre of a cell column (not wrapped)
            return X((column - col0) * pitch + d.w_th + 0.5 * (pitch - d.w_th))

        def Y(z):
            return oy - (z - z_lo) * s

        if title:
            f2 = QFont(font)
            f2.setBold(True)
            f2.setPixelSize(int(round(fpx * 1.25)))
            p.setFont(f2)
            p.setPen(col["text"])
            p.drawText(QRectF(rect.left(), rect.top() + 4, rect.width(), title_h), Qt.AlignCenter, title)
            p.setFont(font)

        maze_rect = QRectF(X(0), Y(z_hi), w_mm * s, h_mm * s)
        p.fillRect(maze_rect, col["ridge"])
        p.setPen(Qt.NoPen)
        p.setBrush(col["groove"])
        z_e = d.z_edges
        nbt = 2 * n_cols                               # blocks across the window: wall, cell, wall, ...
        op = d.open[:, (np.arange(nbt) + 2 * col0) % (2 * N)]
        edge = lambda i: (i // 2) * pitch + (i % 2) * d.w_th
        for bz in range(op.shape[0]):
            y0, y1 = Y(z_e[bz + 1]), Y(z_e[bz])
            bt = 0
            while bt < nbt:            # merge runs of open blocks per row
                if not op[bz, bt]:
                    bt += 1
                    continue
                start = bt
                while bt < nbt and op[bz, bt]:
                    bt += 1
                x0, x1 = X(edge(start)), X(edge(bt))
                p.drawRect(QRectF(x0, y0, x1 - x0 + 0.6, y1 - y0 + 0.6))

        columns = range(col0, col0 + n_cols)
        lock = LOCK_TURN * getattr(d, "lock_cols", 0)  # closed = in the lock pocket, this far round
        home_cols = [c for c in columns if (c - d.maze.home - lock) % Ns == 0]
        exit_cols = [c for c in columns if (c - d.maze.entry) % Ns == 0]
        route_home = d.maze.home + lock                # the highlighted route, not wrapped
        route_exit = d.maze.home + sum(dc for dc, _ in d.maze.moves)

        # the click ridge: across one lock slot only
        if getattr(d, "detent_h", 0) > 0:
            p.setPen(Qt.NoPen)
            rc = QColor(col["ridge"])
            rc.setAlpha(210)
            p.setBrush(rc)
            left = (d.th_ridge - d.a_lo - col0 * pitch) % TWO_PI
            y0, y1 = Y(d.z_notch_floor + d.wp), Y(d.z_notch_floor)
            turn = 0
            while left + turn * TWO_PI < span:
                x0, x1 = X(left + turn * TWO_PI), X(left + turn * TWO_PI + d.a_lo + d.a_hi)
                p.drawRect(QRectF(x0, y0, max(1.5, x1 - x0), y1 - y0))
                turn += 1

        # sector boundaries, and the one-turn mark when more than a turn is shown
        if d.n > 1:
            p.setPen(QPen(col["faint"], 1, Qt.DashLine))
            for j in range(1, (n_cols - 1) // Ns + 1):
                x = X(j * Ns * pitch)
                p.drawLine(QPointF(x, Y(z_hi) - 6), QPointF(x, Y(z_lo) + 6))

        # solution path
        if self.show_solution and d.maze.moves is not None:
            frames = d.solution_keyframes(lift=0.0)
            shift = -col0 * pitch                      # tube angle -> angle from the left edge
            pts = [(th + shift, z) for th, z in (d.nub_position(phi, Z) for phi, Z in frames)]
            p.save()
            p.setClipRect(QRectF(maze_rect.left(), maze_rect.top() - a * 1.2,
                                 maze_rect.width(), maze_rect.height() + a * 1.2))
            sector = TWO_PI / d.n
            lo_th = min(th for th, _ in pts)
            hi_th = max(th for th, _ in pts)
            # every nub follows the same route one sector along: faint copies, then the real one
            copies = [m for m in range(-int(hi_th // sector) - 1, int((span - lo_th) // sector) + 2)
                      if m != 0] + [0]
            for m in copies:
                pen = QPen(col["path"] if m == 0 else col["path2"], max(1.5, d.wp * s * 0.22))
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                path = QPainterPath()
                for i, (th, z) in enumerate(pts):
                    point = QPointF(X(th + m * sector), Y(min(z, z_hi + 3.0)))
                    path.moveTo(point) if i == 0 else path.lineTo(point)
                p.drawPath(path)
            p.restore()

        # home + exit markers
        hz = d.z_notch_floor + 0.5 * d.wp
        rad = max(3.0, min(d.wp * s * 0.28, fpx * 0.5))
        for c in home_cols:
            x = XC(c)
            p.setPen(QPen(col["home"], 2))
            p.setBrush(col["home"])
            p.drawEllipse(QPointF(x, Y(hz)), rad, rad)
            p.setPen(col["home"])
            nub = (c - route_home) // Ns % d.n       # 0 = the highlighted nub, seen again a turn on
            label = "HOME" if c == route_home else "(HOME)" if nub == 0 else f"home {nub + 1}"
            p.drawText(QRectF(x - tw / 2, Y(z_lo) + 2, tw, fh), Qt.AlignHCenter | Qt.AlignTop, label)
        for c in exit_cols:
            ex, ey = XC(c), Y(z_hi)                    # exit arrow above the rim
            p.setPen(Qt.NoPen)
            p.setBrush(col["exit"])
            p.drawPolygon(QPolygonF([QPointF(ex, ey - a * 2.2), QPointF(ex - a, ey - a * 0.6),
                                     QPointF(ex + a, ey - a * 0.6)]))
            p.setPen(col["exit"])
            nub = (c - route_exit) // Ns % d.n
            label = "EXIT" if c == route_exit else "(EXIT)" if nub == 0 else f"exit {nub + 1}"
            p.drawText(QRectF(ex - tw / 2, ey - a * 2.2 - fh, tw, fh), Qt.AlignHCenter | Qt.AlignBottom,
                       label)

        # live nub markers
        if self.marker is not None:
            phi, Z = self.marker
            th0, z = d.nub_position(phi, Z)
            p.setPen(QPen(col["bg"], 2))
            p.setBrush(col["nub"])
            zz = min(z, z_hi + 4)
            w = d.nub_arc * s
            h = (d.zn1 - d.zn0) * s
            sector = TWO_PI / d.n
            first = (th0 - col0 * pitch) % sector      # leftmost nub on the page
            for m in range(int((span - first) // sector) + 1):
                p.drawRoundedRect(QRectF(X(first + m * sector) - w / 2, Y(zz) - h / 2, w, h), 2, 2)

        # axis ticks: how far round, measured from the left edge
        p.setPen(col["faint"])
        for deg in range(0, int(round(math.degrees(span))) + 1, 45):
            x = X(math.radians(deg))
            p.drawLine(QPointF(x, Y(z_lo) + fh + 2), QPointF(x, Y(z_lo) + fh + 6))
            p.drawText(QRectF(x - tw / 2, Y(z_lo) + fh + 5, tw, fh), Qt.AlignHCenter | Qt.AlignTop, f"{deg}°")
        if wide:
            p.drawText(QRectF(maze_rect.left(), Y(z_lo) + 2 * fh + 7, maze_rect.width(), fh * 1.2),
                       Qt.AlignHCenter | Qt.AlignTop,
                       f"This route winds more than once round the tube, so the map shows "
                       f"{math.degrees(span):.0f}°. Past 360° the maze repeats.")
        p.save()
        p.translate(maze_rect.left() - fh * 0.4, maze_rect.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-tw, -fh, 2 * tw, fh), Qt.AlignCenter, "base  ⟶  rim")
        p.restore()
