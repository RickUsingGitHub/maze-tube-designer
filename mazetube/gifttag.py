"""Printable gift tag: a quarter-fold card with the solution hidden inside.

One sheet, printed on one side.  The bottom half carries the back and front
covers; the top half is the inside spread with the solution map, printed
upside down.  Fold the top half back, then fold in half: the solution ends up
inside and stays out of sight until the card is opened - no peeking.

    +-----------------------------+
    |   inside spread (upside     |   <- solution map
    |   down on the sheet)        |
    +- - - - - fold 1 - - - - - - +
    |  back cover  |  front cover |
    +--------------+--------------+
                 fold 2
"""

from __future__ import annotations

from PySide6.QtCore import QLocale, QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPageSize, QPainter, QPdfWriter, QPen

from .mazeview import MazeMapWidget

INK = QColor(35, 38, 45)
SOFT = QColor(120, 126, 136)
ACCENT = QColor(214, 96, 32)
RED = QColor(200, 30, 60)


def default_page_size() -> QPageSize:
    """Letter where that is the custom, A4 everywhere else."""
    imperial = QLocale().measurementSystem() == QLocale.ImperialUSSystem
    return QPageSize(QPageSize.Letter if imperial else QPageSize.A4)


def _font(painter: QPainter, mm_height: float, mm: float, bold=False) -> QFont:
    f = QFont("Segoe UI")
    f.setStyleHint(QFont.SansSerif)
    f.setBold(bold)
    f.setPixelSize(max(6, int(round(mm_height * mm))))
    painter.setFont(f)
    return f


def _text(painter, rect, text, mm_height, mm, color=INK, bold=False,
          flags=Qt.AlignHCenter | Qt.AlignTop):
    _font(painter, mm_height, mm, bold)
    painter.setPen(color)
    painter.drawText(rect, int(flags | Qt.TextWordWrap), text)


def render_gift_tag(painter: QPainter, page: QRectF, design, name: str = "",
                    greeting: str = ""):
    """Draw the whole sheet into ``page`` (device units)."""
    mm = painter.device().logicalDpiX() / 25.4          # device units per millimetre
    W, H = page.width(), page.height()
    half_h, half_w = H / 2.0, W / 2.0
    m = 9 * mm                                         # panel margin
    painter.fillRect(page, QColor("white"))
    maze_map = MazeMapWidget()
    maze_map.dark = False
    maze_map.set_design(design)
    moves = len(design.maze.moves)
    greeting = (greeting or "").strip() or "A puzzle for you!"

    # ------------------------------------------------------------ front cover
    front = QRectF(page.left() + half_w, page.top() + half_h, half_w, half_h)
    r = front.adjusted(m, m, -m, -m)
    _text(painter, QRectF(r.left(), r.top() + 4 * mm, r.width(), 30 * mm), greeting, 9.5, mm,
          ACCENT, bold=True)
    art = QRectF(r.left(), r.top() + 38 * mm, r.width(), 46 * mm)
    maze_map.show_solution = False                     # the real maze, without the answer
    painter.save()
    painter.setClipRect(art)
    maze_map.render_map(painter, art)
    painter.restore()
    painter.setPen(QPen(SOFT, 0.25 * mm))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(art, 2 * mm, 2 * mm)
    _text(painter, QRectF(r.left(), art.bottom() + 5 * mm, r.width(), 22 * mm),
          "Your gift is locked in a maze.\nTwist and slide the cap to find the way out.",
          4.2, mm)
    y = r.bottom() - 16 * mm
    painter.setPen(QPen(SOFT, 0.3 * mm))
    for label in ("To", "From"):
        _text(painter, QRectF(r.left(), y - 1 * mm, 16 * mm, 7 * mm), label + ":", 4.2, mm,
              SOFT, flags=Qt.AlignLeft | Qt.AlignVCenter)
        painter.setPen(QPen(SOFT, 0.3 * mm))
        painter.drawLine(QPointF(r.left() + 16 * mm, y + 5 * mm), QPointF(r.right(), y + 5 * mm))
        y += 9 * mm

    # ------------------------------------------------------------- back cover
    back = QRectF(page.left(), page.top() + half_h, half_w, half_h)
    r = back.adjusted(m, m, -m, -m)
    _text(painter, QRectF(r.left(), r.top() + 4 * mm, r.width(), 10 * mm), "How it works",
          6.0, mm, INK, bold=True)
    steps = ("1.  Pegs inside the cap run in a maze hidden underneath it.\n\n"
             "2.  Twist and slide the cap to feel your way through. Dead ends are part of the fun.\n\n"
             "3.  When the pegs reach the top, the cap lifts off.\n\n"
             "4.  To lock it again, guide the cap back down"
             + (", then turn it until it " + ("clicks." if design.detent_h > 0 else "stops.")
                if design.lock_cols else " and push it home."))
    _text(painter, QRectF(r.left(), r.top() + 17 * mm, r.width(), 70 * mm), steps, 3.9, mm,
          flags=Qt.AlignLeft | Qt.AlignTop)
    box = QRectF(r.left(), r.bottom() - 40 * mm, r.width(), 26 * mm)
    painter.setPen(QPen(RED, 0.5 * mm, Qt.DashLine))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(box, 3 * mm, 3 * mm)
    _text(painter, box.adjusted(3 * mm, 3 * mm, -3 * mm, -13 * mm), "Stuck?", 5.0, mm, RED, bold=True)
    _text(painter, box.adjusted(3 * mm, 11 * mm, -3 * mm, -2 * mm),
          "The solution is inside this card.\nNO PEEKING!", 4.2, mm, RED)
    foot = f"{name}  ·  " if name else ""
    _text(painter, QRectF(r.left(), r.bottom() - 9 * mm, r.width(), 9 * mm),
          f"{foot}{design.N} × {design.M} maze, {moves} moves\nMade with Maze Tube Designer",
          2.8, mm, SOFT)

    # ---------------------------------------------- inside spread (upside down)
    painter.save()
    painter.translate(page.left() + W, page.top() + half_h)
    painter.rotate(180)                                # now (0,0)-(W,half_h) reads upright inside
    r = QRectF(m, m, W - 2 * m, half_h - 2 * m)
    _text(painter, QRectF(r.left(), r.top(), r.width(), 9 * mm), "The solution  –  no peeking!",
          6.0, mm, RED, bold=True)
    maze_map.show_solution = True
    maze_map.render_map(painter, QRectF(r.left(), r.top() + 11 * mm, r.width(), r.height() - 27 * mm))
    _text(painter, QRectF(r.left(), r.bottom() - 14 * mm, r.width(), 14 * mm),
          "This is the maze unrolled, seen from the outside. With the cap fully on, the pegs sit at "
          "HOME. Follow the line to EXIT: sideways on the map means twist the cap, up means pull it. "
          "Every peg follows the same route.", 3.3, mm, INK)
    painter.restore()

    # ------------------------------------------------------------- fold guides
    painter.setPen(QPen(QColor(185, 190, 198), 0.2 * mm, Qt.DashLine))
    painter.drawLine(QPointF(page.left(), page.top() + half_h), QPointF(page.right(), page.top() + half_h))
    painter.drawLine(QPointF(page.left() + half_w, page.top() + half_h), QPointF(page.left() + half_w, page.bottom()))
    _text(painter, QRectF(page.left() + 3 * mm, page.top() + half_h + 0.8 * mm, 80 * mm, 4 * mm),
          "① fold the top half back along this line", 2.3, mm, SOFT, flags=Qt.AlignLeft | Qt.AlignTop)
    painter.save()
    painter.translate(page.left() + half_w - 0.8 * mm, page.bottom() - 3 * mm)
    painter.rotate(-90)
    _text(painter, QRectF(0, -4 * mm, 80 * mm, 4 * mm), "② then fold in half, covers outside",
          2.3, mm, SOFT, flags=Qt.AlignLeft | Qt.AlignBottom)
    painter.restore()


def save_gift_tag(path: str, design, name: str = "", greeting: str = "", page: QPageSize | None = None):
    """Save the gift tag as a PDF (or a PNG when the path ends in .png)."""
    page = page or default_page_size()
    if path.lower().endswith(".png"):
        size = page.sizePixels(200)
        img = QImage(size, QImage.Format_RGB32)
        img.setDotsPerMeterX(int(200 / 0.0254))
        img.setDotsPerMeterY(int(200 / 0.0254))
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing)
        render_gift_tag(painter, QRectF(0, 0, size.width(), size.height()), design, name, greeting)
        painter.end()
        return img.save(path)
    writer = QPdfWriter(path)
    writer.setPageSize(page)
    writer.setResolution(300)
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    writer.setTitle(f"{name or 'Maze tube'} - gift tag")
    writer.setCreator("Maze Tube Designer")
    painter = QPainter(writer)
    painter.setRenderHint(QPainter.Antialiasing)
    render_gift_tag(painter, QRectF(0, 0, writer.width(), writer.height()), design, name, greeting)
    painter.end()
    return True
