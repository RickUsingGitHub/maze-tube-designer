"""2D unrolled view of the maze surface (as seen from outside the tube)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

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
        avail = QRectF(rect.left() + pad_l, rect.top() + top_pad,
                       rect.width() - pad_l - pad_r, rect.height() - top_pad - pad_b)
        if avail.width() < 20 or avail.height() < 20:
            return
        r = d.r_maze
        z_lo, z_hi = d.z0, d.H_body
        w_mm = TWO_PI * r
        h_mm = z_hi - z_lo
        s = min(avail.width() / w_mm, avail.height() / h_mm)
        ox = avail.left() + (avail.width() - w_mm * s) / 2.0
        oy = avail.top() + (avail.height() - h_mm * s) / 2.0 + h_mm * s
        tw = fpx * 7                                   # label box width

        def X(th):
            return ox + th * r * s

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
        th_e, z_e, op = d.th_edges, d.z_edges, d.open
        for bz in range(op.shape[0]):
            y0, y1 = Y(z_e[bz + 1]), Y(z_e[bz])
            bt = 0
            nbt = op.shape[1]
            while bt < nbt:            # merge runs of open blocks per row
                if not op[bz, bt]:
                    bt += 1
                    continue
                start = bt
                while bt < nbt and op[bz, bt]:
                    bt += 1
                x0, x1 = X(th_e[start]), X(th_e[bt])
                p.drawRect(QRectF(x0, y0, x1 - x0 + 0.6, y1 - y0 + 0.6))

        # home detent ridges
        if getattr(d, "detent_h", 0) > 0:
            p.setPen(Qt.NoPen)
            rc = QColor(col["ridge"])
            rc.setAlpha(210)
            p.setBrush(rc)
            for j in range(d.n):
                bt = 2 * (d.maze.home + j * d.Ns) + 1
                x0, x1 = X(th_e[bt]), X(th_e[bt + 1])
                y0, y1 = Y(d.z_ridge + d.ridge_hw), Y(d.z_ridge - d.ridge_hw)
                p.drawRect(QRectF(x0, y0, x1 - x0, max(1.5, y1 - y0)))

        # sector boundaries
        if d.n > 1:
            pen = QPen(col["faint"], 1, Qt.DashLine)
            p.setPen(pen)
            for j in range(1, d.n):
                x = X(j * TWO_PI / d.n)
                p.drawLine(QPointF(x, Y(z_hi) - 6), QPointF(x, Y(z_lo) + 6))

        # solution path
        if self.show_solution and d.maze.moves is not None:
            frames = d.solution_keyframes(lift=0.0)
            pts = [d.nub_position(phi, Z) for phi, Z in frames]
            p.save()
            p.setClipRect(QRectF(maze_rect.left(), maze_rect.top() - a * 1.2,
                                 maze_rect.width(), maze_rect.height() + a * 1.2))
            for j in range(d.n):
                pen = QPen(col["path"] if j == 0 else col["path2"], max(1.5, d.wp * s * 0.22))
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                for k in (-1, 0, 1):
                    path = QPainterPath()
                    for i, (th, z) in enumerate(pts):
                        th2 = th + j * TWO_PI / d.n + k * TWO_PI
                        z2 = min(z, z_hi + 3.0)
                        if i == 0:
                            path.moveTo(X(th2), Y(z2))
                        else:
                            path.lineTo(X(th2), Y(z2))
                    p.drawPath(path)
            p.restore()

        # home + exit markers
        for j in range(d.n):
            th_h = d.cell_theta(d.maze.home + j * d.Ns)
            th_x = d.cell_theta(d.maze.entry + j * d.Ns)
            hz = d.z_notch_floor + 0.5 * d.wp
            p.setPen(QPen(col["home"], 2))
            p.setBrush(col["home"])
            rad = max(3.0, min(d.wp * s * 0.28, fpx * 0.5))
            p.drawEllipse(QPointF(X(th_h), Y(hz)), rad, rad)
            p.setPen(col["home"])
            p.drawText(QRectF(X(th_h) - tw / 2, Y(z_lo) + 2, tw, fh), Qt.AlignHCenter | Qt.AlignTop,
                       "HOME" if j == 0 else f"home {j + 1}")
            # exit arrow above the rim
            ex, ey = X(th_x), Y(z_hi)
            p.setPen(Qt.NoPen)
            p.setBrush(col["exit"])
            p.drawPolygon(QPolygonF([QPointF(ex, ey - a * 2.2), QPointF(ex - a, ey - a * 0.6),
                                     QPointF(ex + a, ey - a * 0.6)]))
            p.setPen(col["exit"])
            p.drawText(QRectF(ex - tw / 2, ey - a * 2.2 - fh, tw, fh), Qt.AlignHCenter | Qt.AlignBottom,
                       "EXIT" if j == 0 else f"exit {j + 1}")

        # live nub markers
        if self.marker is not None:
            phi, Z = self.marker
            th0, z = d.nub_position(phi, Z)
            p.setPen(QPen(col["bg"], 2))
            p.setBrush(col["nub"])
            for j in range(d.n):
                th = (th0 + j * TWO_PI / d.n) % TWO_PI
                zz = min(z, z_hi + 4)
                w = d.nub_arc * s
                h = (d.zn1 - d.zn0) * s
                p.drawRoundedRect(QRectF(X(th) - w / 2, Y(zz) - h / 2, w, h), 2, 2)

        # axis ticks
        p.setPen(col["faint"])
        for deg in range(0, 361, 45):
            x = X(math.radians(deg))
            p.drawLine(QPointF(x, Y(z_lo) + fh + 2), QPointF(x, Y(z_lo) + fh + 6))
            p.drawText(QRectF(x - tw / 2, Y(z_lo) + fh + 5, tw, fh), Qt.AlignHCenter | Qt.AlignTop, f"{deg}°")
        p.save()
        p.translate(maze_rect.left() - fh * 0.4, maze_rect.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-tw, -fh, 2 * tw, fh), Qt.AlignCenter, "base  ⟶  rim")
        p.restore()
