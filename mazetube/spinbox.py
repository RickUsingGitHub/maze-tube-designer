"""Number boxes that behave the way people expect.

Qt's own spin boxes have three habits that catch people out:

* clicking to the right of the suffix ("200 mm") puts the caret after the "mm",
  where nothing you type is accepted, so the box looks broken;
* a number past the maximum is refused key by key, so you cannot type 99 in a
  box that stops at 40 even when you would rather be corrected afterwards;
* the mouse wheel changes the value anywhere over the box, so a number that
  happens to slide under the pointer while you scroll the page is changed by
  accident.

These classes fix all three: clicking picks the number out so typing replaces
it and the caret never lands in the suffix, an out-of-range number can be typed
in full and is brought into range when you press Enter or leave the box, and
the wheel only works over the up and down arrows.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox, QStyle, QStyleOptionSpinBox


class _Behaviour:
    """Shared by both boxes below; _parse says how to read the typed number."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pick_out = False

    # -- reading what was typed -----------------------------------------
    def _body(self, text: str) -> str:
        """The text with any prefix and suffix taken off."""
        t = text.strip()
        pre, suf = self.prefix().strip(), self.suffix().strip()
        if pre and t.startswith(pre):
            t = t[len(pre):]
        if suf and t.endswith(suf):
            t = t[:-len(suf)]
        return t.strip()

    def _commit(self) -> bool:
        """Read the typed number and bring it into range. False if it isn't a number."""
        try:
            value = self._parse(self._body(self.lineEdit().text()))
        except ValueError:
            return False
        self.setValue(max(self.minimum(), min(self.maximum(), value)))
        return True

    def validate(self, text, pos):
        state, txt, p = super().validate(text, pos)
        if state == QValidator.Invalid:
            body = self._body(text)
            if body in ("", "-", "+", ".", "-.", "+."):
                return QValidator.Intermediate, text, pos
            try:
                self._parse(body)
            except ValueError:
                return state, txt, p
            # A real number, just outside the range: let it be typed, correct it later.
            return QValidator.Intermediate, text, pos
        return state, txt, p

    # Correcting on Enter and on the way out. _commit first rewrites the text to a
    # number in range, so Qt's own handling afterwards sees something it accepts.
    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._commit()
        super().keyPressEvent(ev)

    def focusOutEvent(self, ev):
        self._commit()
        super().focusOutEvent(ev)

    # -- the caret stays inside the number ------------------------------
    def _clamp_caret(self):
        le = self.lineEdit()
        end = len(le.text()) - len(self.suffix())
        if le.cursorPosition() > end:
            le.setCursorPosition(max(0, end))

    def mousePressEvent(self, ev):
        super().mousePressEvent(ev)
        self._clamp_caret()

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        # Qt gives the box focus before the click lands and the click then clears any
        # selection, so the click that brings you in picks the number out here instead.
        if self._pick_out:
            self._pick_out = False
            self.selectAll()
        elif not self.lineEdit().hasSelectedText():
            self._clamp_caret()

    def focusInEvent(self, ev):
        super().focusInEvent(ev)
        self._pick_out = ev.reason() == Qt.MouseFocusReason
        if ev.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason):
            self.selectAll()

    # -- the wheel only works over the arrows ---------------------------
    def wheelEvent(self, ev):
        opt = QStyleOptionSpinBox()
        self.initStyleOption(opt)
        pos = ev.position().toPoint()
        st = self.style()
        up = st.subControlRect(QStyle.CC_SpinBox, opt, QStyle.SC_SpinBoxUp, self)
        down = st.subControlRect(QStyle.CC_SpinBox, opt, QStyle.SC_SpinBoxDown, self)
        if up.contains(pos) or down.contains(pos):
            super().wheelEvent(ev)
        else:
            ev.ignore()          # let whatever is underneath scroll the page


class SpinBox(_Behaviour, QSpinBox):
    @staticmethod
    def _parse(body: str) -> int:
        return int(body)


class DoubleSpinBox(_Behaviour, QDoubleSpinBox):
    @staticmethod
    def _parse(body: str) -> float:
        return float(body.replace(",", "."))
