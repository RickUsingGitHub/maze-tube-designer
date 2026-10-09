"""Render the application icon to assets/icon.png and assets/icon.ico."""

import os
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0
    p.scale(s, s)

    bg = QLinearGradient(0, 0, 0, 256)
    bg.setColorAt(0, QColor(52, 60, 74))
    bg.setColorAt(1, QColor(22, 25, 31))
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 44, 44)

    # body (orange cylinder with maze grooves)
    body = QLinearGradient(64, 0, 192, 0)
    body.setColorAt(0, QColor(196, 104, 30))
    body.setColorAt(0.45, QColor(255, 170, 80))
    body.setColorAt(1, QColor(170, 86, 22))
    p.setBrush(body)
    p.drawRoundedRect(QRectF(66, 96, 124, 132), 10, 10)
    pen = QPen(QColor(92, 46, 14), 9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    maze = [
        [(96, 104), (96, 128), (150, 128), (150, 152), (82, 152)],
        [(82, 152), (82, 178), (126, 178), (126, 204), (172, 204)],
        [(172, 128), (172, 178), (150, 178)],
    ]
    for line in maze:
        for a, b in zip(line, line[1:]):
            p.drawLine(QPointF(*a), QPointF(*b))

    # cap (blue), lifted
    p.setPen(Qt.NoPen)
    cap = QLinearGradient(58, 0, 198, 0)
    cap.setColorAt(0, QColor(30, 92, 160))
    cap.setColorAt(0.45, QColor(90, 170, 240))
    cap.setColorAt(1, QColor(24, 80, 140))
    p.setBrush(cap)
    p.drawRoundedRect(QRectF(58, 30, 140, 58), 12, 12)
    p.setBrush(QColor(255, 80, 96))
    p.drawEllipse(QPointF(96, 92), 8, 8)
    p.end()
    return img


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841
    out = os.path.join(ROOT, "assets")
    os.makedirs(out, exist_ok=True)
    render(256).save(os.path.join(out, "icon.png"))
    render(256).save(os.path.join(out, "icon.ico"))
    print("icon written to", out)


if __name__ == "__main__":
    main()
