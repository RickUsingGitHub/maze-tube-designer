"""Text for the tube walls: rasterise it and wrap it round a cylinder.

The text is rendered with Qt into coverage masks.  A band places a mask on
the outer wall of a part and reports the radial offset (raised or cut in) at
any grid point, so the sweep mesher can simply add it to the radius.

Two layouts, chosen from the text itself (letters are never shrunk to fit
round the tube):

* **rings** - every line fits round the tube: the lines are stacked, centred,
  each one level.
* **spiral** - a line is longer than the circumference: the whole text runs
  round and round the tube in one continuous helix, dropping one line per
  turn.  A new line starts back at the starting meridian on the next turn,
  so a whole poem can be written on a tube.
"""

from __future__ import annotations

import math

import numpy as np

TWO_PI = 2.0 * math.pi
RING_FRACTION = 0.92      # a line wider than this share of the circumference spirals
LEADING = 1.10            # line pitch / font line height
MARGIN = 1.3              # clear wall above and below the text (mm)
_app = None               # keeps a QApplication alive when none exists (tests, scripts)


def _ensure_app():
    from PySide6.QtGui import QGuiApplication
    global _app
    if QGuiApplication.instance() is None:
        # outside the app (scripts, tests): Qt needs an application object to
        # use fonts.  A full QApplication, so widgets can still be made later.
        from PySide6.QtWidgets import QApplication
        _app = QApplication([])


def _make_font(pixel_size: float):
    from PySide6.QtGui import QFont
    f = QFont("Arial")
    f.setBold(True)
    f.setPixelSize(max(4, int(round(pixel_size))))
    return f


def _render_line(text: str, font, fm, height: int) -> np.ndarray:
    """One line of text as a uint8 coverage strip (height px tall, baseline fixed)."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QImage, QPainter

    if not text:
        return np.zeros((height, 0), dtype=np.uint8)
    # Qt images are limited in width, so long lines are drawn in chunks at spaces
    chunks, cur = [], ""
    for word in text.split(" "):
        nxt = word if not cur else cur + " " + word
        if cur and len(nxt) > 60:
            chunks.append(cur + " ")
            cur = word
        else:
            cur = nxt
    chunks.append(cur)
    parts = []
    for chunk in chunks:
        w = int(math.ceil(fm.horizontalAdvance(chunk)))
        if w <= 0:
            continue
        img = QImage(w + 4, height, QImage.Format_Grayscale8)
        img.fill(QColor(0, 0, 0))
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(QPointF(0, fm.ascent()), chunk)
        painter.end()
        buf = np.frombuffer(img.constBits(), dtype=np.uint8)
        parts.append(buf.reshape(height, img.bytesPerLine())[:, :w].copy())
    if not parts:
        return np.zeros((height, 0), dtype=np.uint8)
    return np.concatenate(parts, axis=1)


class TextLayout:
    """Text laid out for a tube of a given circumference, not yet placed on a wall."""

    def __init__(self, text: str, cap_height: float, circumference: float, px: float):
        _ensure_app()
        from PySide6.QtGui import QFontMetricsF

        self.text = text
        self.px = px
        self.circumference = circumference
        lines = [ln.strip() for ln in text.replace("\r", "").split("\n")]
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        self.lines = lines

        probe = QFontMetricsF(_make_font(200))
        ratio = max(probe.capHeight(), 1.0) / 200.0          # cap height per font pixel
        font = _make_font(cap_height / px / ratio)
        fm = QFontMetricsF(font)
        self.cap_height = fm.capHeight() * px                # what the font really gives
        self.line_px = int(math.ceil(fm.ascent() + fm.descent()))
        self.pitch = self.line_px * px * LEADING             # mm from one line to the next
        strips = [_render_line(ln, font, fm, self.line_px) for ln in lines]
        widths = [s.shape[1] * px for s in strips]
        self.max_line = max(widths) if widths else 0.0
        self.empty = not any(s.any() for s in strips)
        self.spiral = self.max_line > RING_FRACTION * circumference

        if self.spiral:
            # one long strip along the helix; a new line starts at the next turn
            turn_px = circumference / px
            starts, s = [], 0.0
            for strip in strips:
                starts.append(int(round(s)))
                if strip.shape[1] == 0:                      # blank line: leave a turn empty
                    s += turn_px
                    continue
                end = s + strip.shape[1]
                s = (math.floor(end / turn_px - 1e-9) + 1) * turn_px
            self.length = (starts[-1] + strips[-1].shape[1]) * px      # mm of helix used
            total = starts[-1] + strips[-1].shape[1] + 2
            self.strip = np.zeros((self.line_px, total), dtype=np.uint8)
            for st, strip in zip(starts, strips):
                self.strip[:, st:st + strip.shape[1]] = strip
            self.line_starts = [st * px for st in starts]
            self.turns = self.length / circumference
            self.height = self.pitch * (self.turns + 1.0) + 2 * MARGIN
        else:
            # lines stacked and centred in one block
            pad = max(3, int(math.ceil(MARGIN / px)))
            row = int(round(self.pitch / px))
            w = max((s.shape[1] for s in strips), default=0) + 2 * pad
            h = row * len(strips) + 2 * pad
            block = np.zeros((h, w), dtype=np.float32)
            for i, strip in enumerate(strips):
                x = (w - strip.shape[1]) // 2
                y = pad + i * row + (row - self.line_px) // 2
                block[y:y + self.line_px, x:x + strip.shape[1]] = strip / 255.0
            self.block = block
            self.turns = 0.0
            self.height = h * px

    def describe(self) -> str:
        n = len(self.lines)
        what = f"{n} lines" if n > 1 else "1 line"
        if self.spiral:
            return f"{what}, spiral of {self.turns:.1f} turns"
        return what

    def make_band(self, r_ref, z_low, depth, pitch, centre_angle, start_angle):
        if self.spiral:
            return SpiralBand(self, r_ref, z_low, depth, pitch, start_angle)
        return TextBand(self.block, self.px, centre_angle, r_ref, z_low, depth, pitch)


def layout_text(text: str, cap_height: float, circumference: float, px: float):
    """Lay text out for a tube; None when there is nothing to draw."""
    if not (text or "").strip():
        return None
    layout = TextLayout(text, cap_height, circumference, px)
    return None if layout.empty else layout


def _finish(cov, depth, z, limit_slope):
    """Coverage (0..1) -> radial offset, optionally limited to 45° along z."""
    # sharpen the anti-aliased edge a little so strokes keep crisp walls
    out = np.clip((cov - 0.25) / 0.5, 0.0, 1.0) * abs(depth)
    if limit_slope and out.shape[1] > 1:
        dz = np.abs(np.diff(z))
        for k in range(1, out.shape[1]):
            out[:, k] = np.maximum(out[:, k], out[:, k - 1] - dz[k - 1])
        for k in range(out.shape[1] - 2, -1, -1):
            out[:, k] = np.maximum(out[:, k], out[:, k + 1] - dz[k])
    return out if depth >= 0 else -out


def _bilinear(img, u, v, valid):
    """Sample img[v, u] (arrays of the same shape) with bilinear interpolation."""
    h, w = img.shape
    u0 = np.clip(np.floor(u).astype(np.int64), 0, max(w - 2, 0))
    v0 = np.clip(np.floor(v).astype(np.int64), 0, max(h - 2, 0))
    fu = np.clip(u - u0, 0.0, 1.0)
    fv = np.clip(v - v0, 0.0, 1.0)
    u1 = np.minimum(u0 + 1, w - 1)
    v1 = np.minimum(v0 + 1, h - 1)
    a = img[v0, u0].astype(np.float32)
    b = img[v0, u1].astype(np.float32)
    c = img[v1, u0].astype(np.float32)
    e = img[v1, u1].astype(np.float32)
    cov = (a * (1 - fu) + b * fu) * (1 - fv) + (c * (1 - fu) + e * fu) * fv
    return np.where(valid, cov, 0.0)


class TextBand:
    """A block of text (one or more level lines) on a cylinder wall, centred on an angle."""

    spiral = False

    def __init__(self, mask, px, theta_centre, r_ref, z_low, depth, pitch):
        self.mask = mask
        self.px = px                      # mask pixel size (mm)
        self.pitch = pitch                # mesh grid spacing inside the band (mm)
        self.r_ref = r_ref
        self.depth = depth                # +raised / -cut in (mm)
        self.width = mask.shape[1] * px   # mm along the circumference
        self.height = mask.shape[0] * px
        self.theta_c = theta_centre
        self.half_angle = 0.5 * self.width / r_ref
        self.z0 = z_low
        self.z1 = z_low + self.height

    def z_lines(self):
        n = max(2, int(math.ceil(self.height / self.pitch)) + 1)
        return np.linspace(self.z0, self.z1, n)

    def theta_lines(self):
        n = max(2, int(math.ceil(self.width / self.pitch)) + 1)
        return np.mod(np.linspace(self.theta_c - self.half_angle,
                                  self.theta_c + self.half_angle, n), TWO_PI)

    def covers(self, th):
        d = np.mod(th - self.theta_c + math.pi, TWO_PI) - math.pi
        return np.abs(d) <= self.half_angle

    def sample(self, th, z, limit_slope=False):
        """Radial offset at angles th (T,) and heights z (K,): array (T, K).

        With limit_slope the offset never changes faster than 45° along z
        (needed for spiral vase printing); strokes get slightly wider.
        """
        th = np.asarray(th, dtype=float)
        z = np.asarray(z, dtype=float)
        d = np.mod(th - self.theta_c + math.pi, TWO_PI) - math.pi
        u = (d * self.r_ref + 0.5 * self.width) / self.px - 0.5          # column
        v = (self.z1 - z) / self.px - 0.5                                # row (0 = top)
        h, w = self.mask.shape
        U, V = np.broadcast_arrays(u[:, None], v[None, :])
        valid = (U >= 0) & (U <= w - 1) & (V >= 0) & (V <= h - 1)
        return _finish(_bilinear(self.mask, U, V, valid), self.depth, z, limit_slope)


class SpiralBand:
    """Text running round and round a cylinder wall in one continuous helix.

    Seen from outside the text reads left to right (increasing angle) and
    drops by one line pitch per turn, so the next turn sits just below.
    Letters stay upright; only the baseline slopes.
    """

    spiral = True

    def __init__(self, layout: TextLayout, r_ref, z_low, depth, pitch, start_angle):
        self.strip = layout.strip
        self.px = layout.px
        self.pitch = pitch                        # mesh grid spacing (mm)
        self.r_ref = r_ref
        self.depth = depth
        self.turn = TWO_PI * r_ref                # mm of text per turn
        self.line_pitch = layout.pitch            # mm dropped per turn
        self.length = layout.length
        self.turns = layout.turns
        self.width = self.turn                    # the band goes all the way round
        self.height = layout.height
        self.theta0 = start_angle                 # meridian where every new line starts
        self.z0 = z_low
        self.z1 = z_low + self.height
        self.z_text = self.z1 - MARGIN            # top of the first line at the start meridian

    def z_lines(self):
        n = max(2, int(math.ceil(self.height / self.pitch)) + 1)
        return np.linspace(self.z0, self.z1, n)

    def theta_lines(self):
        n = max(8, int(math.ceil(self.turn / self.pitch)))
        return np.mod(self.theta0 + np.arange(n) * (TWO_PI / n), TWO_PI)

    def covers(self, th):
        return np.ones(np.shape(th), dtype=bool)

    def sample(self, th, z, limit_slope=False):
        th = np.asarray(th, dtype=float)
        z = np.asarray(z, dtype=float)
        x = np.mod(th - self.theta0, TWO_PI) * self.r_ref          # mm round from the start meridian
        top = self.z_text - self.line_pitch * x / self.turn        # top of turn 0 at this angle
        q = (top[:, None] - z[None, :]) / self.line_pitch          # which turn, and how far down it
        t = np.floor(q)
        h, w = self.strip.shape
        lead = (self.line_pitch / self.px - h) / 2.0               # leading above the glyph row
        v = (q - t) * self.line_pitch / self.px - lead - 0.5
        u = (x[:, None] + t * self.turn) / self.px - 0.5
        valid = (t >= 0) & (u >= 0) & (u <= w - 1) & (v >= 0) & (v <= h - 1)
        cov = _bilinear(self.strip, u, v, valid) / 255.0
        return _finish(cov, self.depth, z, limit_slope)
