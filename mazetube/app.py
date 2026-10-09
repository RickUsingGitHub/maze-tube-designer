"""Maze Tube Designer - main window."""

from __future__ import annotations

import bisect
import math
import os
import sys
import time

import numpy as np
from PySide6.QtCore import QRectF, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QIcon, QImage, QKeySequence, QPainter, QPalette,
                           QSurfaceFormat)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea,
                               QSpinBox, QSplitter, QStyle, QTextBrowser, QToolButton,
                               QVBoxLayout, QWidget)

from . import __version__
from .geometry import TubeDesign, print_plate, transform
from .maze import ALGORITHM_TIPS, ALGORITHMS
from .mazeview import MazeMapWidget
from .params import TubeParams, load_design, save_design
from .stl import write_stl
from .viewer import Viewer3D, make_transform

BODY_COLOR = (0.93, 0.56, 0.22)
CAP_COLOR = (0.24, 0.58, 0.88)
NUB_COLOR = (0.98, 0.30, 0.38)

VIEW_MODES = ["Print layout", "Assembled", "Exploded", "Body only", "Cap only"]
SPEEDS = {"0.5×": 0.5, "1×": 1.0, "2×": 2.0, "4×": 4.0}
BASE_SPEED = 22.0   # mm/s along the maze path

# (key, label, kind, min, max, step, suffix/special, tooltip)
SPEC = [
    ("Maze", [
        ("rows", "Rows", "int", 0, 200, 1, "Auto",
         "Number of maze rows along the tube axis.\n"
         "Auto = as many rows as fit in the maze length below."),
        ("maze_length", "Maze length", "float", 10.0, 1000.0, 1.0, " mm",
         "Length of the maze section when Rows is Auto (rows are fitted inside it).\n"
         "The rest of a long body is plain tube."),
        ("columns", "Columns", "int", 0, 240, 1, "Auto",
         "Total columns around the tube. Auto fits as many as path + wall width allow.\n"
         "Always rounded to a multiple of the nub count."),
        ("nubs", "Nubs", "int", 1, 6, 1, "",
         "Number of nubs on the cap. The maze repeats once per nub so all nubs move together.\n"
         "2-3 nubs give a sturdier, wobble-free fit."),
        ("path_width", "Path width", "float", 1.5, 20.0, 0.1, " mm", "Width of the maze grooves."),
        ("wall_width", "Wall width", "float", 0.8, 10.0, 0.1, " mm", "Width of the ridges between grooves."),
        ("algorithm", "Algorithm", "combo", None, None, None, "",
         "Maze generation algorithm:\n"
         + "\n".join(f"• {ALGORITHMS[k]}: {t}" for k, t in ALGORITHM_TIPS.items())),
        ("seed", "Seed", "seed", 0, 2_000_000_000, 1, "", "Random seed - same seed, same maze."),
        ("horizontal_bias", "Sideways bias", "float", 0.2, 5.0, 0.1, " ×",
         "Above 1 favours sideways (twisting) corridors, below 1 favours sliding up/down."),
        ("loops", "Extra loops", "float", 0.0, 40.0, 1.0, " %",
         "Remove this percentage of the remaining walls to add loops (multiple routes)."),
        ("longest_path", "Longest solution", "bool", None, None, None, "",
         "Place home and exit to give the longest possible solution."),
        ("notch_depth", "Home notch", "float", 0.0, 6.0, 0.1, " mm",
         "Extra drop at the home position so the cap only closes flush there (0 = off).\n"
         "Deepened automatically when a detent is used."),
        ("detent", "Home detent", "float", 0.0, 1.0, 0.05, " mm",
         "Snap ridge in the home notch that the nub clicks over, holding the cap shut.\n"
         "The value is the interference the cap wall must flex past:\n"
         "0.2-0.35 mm for 2 nubs, 0.15-0.25 mm for 3+ nubs, 0 = off."),
    ]),
    ("Body (inner tube)", [
        ("maze_diameter", "Maze diameter", "float", 12.0, 250.0, 0.5, " mm",
         "Outside diameter of the inner tube, measured over the maze ridges."),
        ("groove_depth", "Groove depth", "float", 0.6, 6.0, 0.1, " mm", "Depth of the maze grooves (= nub height)."),
        ("core_wall", "Core wall", "float", 0.6, 10.0, 0.1, " mm", "Wall thickness under the groove floor."),
        ("body_length", "Body length", "float", 0.0, 1000.0, 1.0, " mm|Auto",
         "Total length of the body. The maze sits at the top (where the cap goes on),\n"
         "the rest is plain tube. Auto = grip band + maze only."),
        ("base_height", "Grip band", "float", 1.0, 200.0, 0.5, " mm",
         "Minimum plain section below the maze (the part you hold when closed).\n"
         "A longer Body length extends this section."),
        ("plain_wall", "Plain wall", "float", 0.0, 10.0, 0.1, " mm|Same bore",
         "Wall thickness of the plain section below the maze. Thinner than the maze\n"
         "section saves a lot of filament on long tubes (45° taper, no supports).\n"
         "Same bore = keep the maze-section bore all the way down."),
        ("bottom_thickness", "Bottom", "float", 0.6, 20.0, 0.1, " mm", "Thickness of the body's floor."),
    ]),
    ("Cap (outer tube)", [
        ("cap_wall", "Cap wall", "float", 0.8, 10.0, 0.1, " mm", "Wall thickness of the cap."),
        ("cap_top", "Cap top", "float", 0.6, 20.0, 0.1, " mm", "Thickness of the cap's closed end."),
        ("clearance", "Clearance", "float", 0.05, 1.5, 0.05, " mm",
         "Gap between the parts (radial and around the nubs). 0.25-0.35 mm suits most FDM printers."),
        ("cap_length", "Cap length", "float", 0.0, 1000.0, 1.0, " mm|Auto",
         "Total length of the cap. Auto = just long enough to cover the maze.\n"
         "A longer cap extends past the body's rim and adds storage space."),
    ]),
    ("Print && finish", [
        ("chamfer_ceilings", "45° ceilings", "bool", None, None, None, "",
         "Slope groove ceilings and nub tops at 45° so both parts print without supports."),
        ("edge_chamfer", "Edge chamfer", "float", 0.0, 3.0, 0.1, " mm", "Chamfer on the outer bottom/top edges."),
        ("grip_flutes", "Grip flutes", "int", 0, 120, 1, "",
         "Number of grip flutes around the outside (0 = smooth)."),
        ("grip_depth", "Flute depth", "float", 0.1, 3.0, 0.1, " mm", "Depth of the grip flutes."),
        ("resolution", "Facet size", "float", 0.1, 5.0, 0.1, " mm",
         "Maximum facet length around the circumference (smaller = smoother, bigger files)."),
    ]),
    ("Spiral vase printing", [
        ("vase_mode", "Spiral vase mode", "bool", None, None, None, "",
         "Make both parts printable in spiral vase (spiralize) mode: one continuous wall.\n"
         "All groove edges and nub faces become 45° slopes, the parts are exported solid\n"
         "(the slicer hollows them) and the nubs are dents in the cap's wall.\n"
         "Paths are widened automatically if they are too narrow for the slopes."),
        ("vase_wall", "Line width", "float", 0.3, 1.5, 0.05, " mm",
         "The slicer's outer wall line width in vase mode - the printed wall thickness.\n"
         "Set exactly the same value in the slicer: the cap's fit depends on it.\n"
         "0.6-0.8 mm with a 0.4 mm nozzle gives sturdy parts."),
    ]),
]


def glyph_icon(kind: str) -> QIcon:
    """Small light line icons that read well on the dark theme."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPen, QPixmap

    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(225, 230, 236), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    if kind == "export":
        p.drawLine(QPointF(32, 8), QPointF(32, 40))
        p.drawPolyline([QPointF(19, 28), QPointF(32, 41), QPointF(45, 28)])
        p.drawPolyline([QPointF(10, 40), QPointF(10, 54), QPointF(54, 54), QPointF(54, 40)])
    elif kind == "map":
        p.drawRoundedRect(QRectF(8, 12, 48, 40), 4, 4)
        pen.setColor(QColor(240, 163, 86))
        p.setPen(pen)
        p.drawPolyline([QPointF(18, 52), QPointF(18, 32), QPointF(34, 32),
                        QPointF(34, 22), QPointF(46, 22), QPointF(46, 12)])
    p.end()
    return QIcon(pm)


def make_app_icon() -> QIcon:
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "assets", "icon.png"),
                 os.path.join(getattr(sys, "_MEIPASS", here), "assets", "icon.png")):
        if os.path.exists(cand):
            return QIcon(cand)
    return QIcon()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("MazeTube", "MazeTubeDesigner")
        self.params = TubeParams()
        self.design: TubeDesign | None = None
        self.maze = None
        self.maze_key = None
        self.current_file = None
        self.cap_pose = (0.0, 0.0)
        self.anim = None
        self._fitted_bounds = None
        self._dirty = False
        self.widgets = {}

        self.regen_timer = QTimer(self)
        self.regen_timer.setSingleShot(True)
        self.regen_timer.setInterval(220)
        self.regen_timer.timeout.connect(self.regenerate)
        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(16)
        self.anim_timer.timeout.connect(self._anim_tick)

        self.setWindowIcon(make_app_icon())
        self._build_ui()
        self._build_actions()
        self.resize(1440, 920)
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self.regenerate()

    # ================================================================== UI
    def _build_ui(self):
        # ---------------- parameter panel
        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(8, 8, 8, 8)
        pl.setSpacing(8)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name"))
        self.name_edit = QLineEdit(self.params.name)
        self.name_edit.setToolTip("Design name - used for exported file names.")
        self.name_edit.textChanged.connect(self._on_name)
        name_row.addWidget(self.name_edit)
        pl.addLayout(name_row)

        self.new_maze_btn = QPushButton("  New random maze")
        self.new_maze_btn.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.new_maze_btn.setToolTip("Pick a new random seed (Ctrl+R)")
        self.new_maze_btn.clicked.connect(self.randomize_seed)
        self.new_maze_btn.setMinimumHeight(30)
        pl.addWidget(self.new_maze_btn)

        for title, items in SPEC:
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.setContentsMargins(8, 12, 8, 8)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(5)
            for key, label, kind, lo, hi, step, extra, tip in items:
                w = self._make_widget(key, kind, lo, hi, step, extra)
                lab = QLabel(label)
                lab.setToolTip(tip)
                w.setToolTip(tip)
                form.addRow(lab, w)
            pl.addWidget(box)
        pl.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(320)
        scroll.setMaximumWidth(420)

        # ---------------- viewer + map + info
        self.viewer = Viewer3D()
        self.map = MazeMapWidget()
        self.info = QTextBrowser()
        self.info.setOpenExternalLinks(False)
        self.info.setMinimumWidth(270)

        bottom = QSplitter(Qt.Horizontal)
        bottom.addWidget(self.map)
        bottom.addWidget(self.info)
        bottom.setStretchFactor(0, 3)
        bottom.setStretchFactor(1, 1)
        bottom.setSizes([800, 330])

        right = QSplitter(Qt.Vertical)
        right.addWidget(self._make_view_bar())
        right.addWidget(self.viewer)
        right.addWidget(bottom)
        right.setStretchFactor(1, 3)
        right.setStretchFactor(2, 2)
        right.setSizes([36, 560, 300])
        right.setCollapsible(0, False)
        right.handle(1).setEnabled(False)

        main = QSplitter(Qt.Horizontal)
        main.addWidget(scroll)
        main.addWidget(right)
        main.setStretchFactor(1, 1)
        main.setSizes([350, 1090])
        self.setCentralWidget(main)
        self.statusBar().showMessage("Ready")
        self._refresh_widgets()

    def _make_view_bar(self):
        bar = QWidget()
        bar.setMaximumHeight(40)
        h = QHBoxLayout(bar)
        h.setContentsMargins(6, 4, 6, 4)
        h.addWidget(QLabel("View"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(VIEW_MODES)
        self.mode_combo.currentIndexChanged.connect(lambda _: self.apply_view_mode(fit=True))
        h.addWidget(self.mode_combo)
        self.xray_chk = QCheckBox("X-ray cap")
        self.xray_chk.setChecked(True)
        self.xray_chk.setToolTip("Show the cap semi-transparent in assembled / exploded views")
        self.xray_chk.toggled.connect(lambda _: self.apply_view_mode())
        h.addWidget(self.xray_chk)
        self.section_chk = QCheckBox("Section")
        self.section_chk.setToolTip("Cut away the front half to look inside")
        self.section_chk.toggled.connect(self.viewer.set_section)
        h.addWidget(self.section_chk)
        self.solution_chk = QCheckBox("Show solution")
        self.solution_chk.setChecked(True)
        self.solution_chk.toggled.connect(self.map.set_show_solution)
        h.addWidget(self.solution_chk)
        h.addSpacing(12)
        self.play_btn = QPushButton("  Play solution")
        self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_btn.setToolTip("Animate the cap travelling through the maze (F5)")
        self.play_btn.clicked.connect(self.toggle_play)
        h.addWidget(self.play_btn)
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(list(SPEEDS))
        self.speed_combo.setCurrentText("1×")
        self.speed_combo.setToolTip("Animation speed")
        h.addWidget(self.speed_combo)
        h.addStretch(1)
        fit = QToolButton()
        fit.setText("Fit view")
        fit.setToolTip("Reset the camera (double-click the view or press F)")
        fit.clicked.connect(lambda: self.viewer.fit_view(reset_angles=True))
        h.addWidget(fit)
        return bar

    def _make_widget(self, key, kind, lo, hi, step, extra):
        val = getattr(self.params, key)
        if kind == "int":
            w = QSpinBox()
            w.setRange(lo, hi)
            w.setSingleStep(step)
            if extra == "Auto":
                w.setSpecialValueText("Auto")
            w.setValue(val)
            w.valueChanged.connect(lambda v, k=key: self._on_param(k, int(v)))
        elif kind == "float":
            w = QDoubleSpinBox()
            w.setDecimals(2)
            w.setRange(lo, hi)
            w.setSingleStep(step)
            suffix, _, special = extra.partition("|")
            w.setSuffix(suffix)
            if special:
                w.setSpecialValueText(special)      # shown at the minimum (0)
            w.setValue(val)
            w.setKeyboardTracking(False)
            w.valueChanged.connect(lambda v, k=key: self._on_param(k, float(v)))
        elif kind == "bool":
            w = QCheckBox()
            w.setChecked(val)
            w.toggled.connect(lambda v, k=key: self._on_param(k, bool(v)))
        elif kind == "combo":
            w = QComboBox()
            for i, (k, desc) in enumerate(ALGORITHMS.items()):
                w.addItem(desc, k)
                w.setItemData(i, ALGORITHM_TIPS[k], Qt.ToolTipRole)
            w.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            w.setMinimumContentsLength(12)
            w.setCurrentIndex(max(0, w.findData(val)))
            w.currentIndexChanged.connect(lambda i, k=key, ww=w: self._on_param(k, ww.itemData(i)))
        elif kind == "seed":
            box = QWidget()
            hl = QHBoxLayout(box)
            hl.setContentsMargins(0, 0, 0, 0)
            sp = QSpinBox()
            sp.setRange(lo, hi)
            sp.setValue(val)
            sp.setKeyboardTracking(False)
            sp.valueChanged.connect(lambda v, k=key: self._on_param(k, int(v)))
            dice = QToolButton()
            dice.setText("🎲")
            dice.setToolTip("Random seed")
            dice.clicked.connect(self.randomize_seed)
            hl.addWidget(sp, 1)
            hl.addWidget(dice)
            self.widgets[key] = sp
            return box
        else:
            raise ValueError(kind)
        if isinstance(w, (QSpinBox, QDoubleSpinBox)):
            w.setKeyboardTracking(False)
            w.setMinimumWidth(110)
        self.widgets[key] = w
        return w

    def _refresh_widgets(self):
        """Push self.params into the widgets without triggering updates."""
        for key, w in self.widgets.items():
            val = getattr(self.params, key)
            w.blockSignals(True)
            if isinstance(w, QComboBox):
                w.setCurrentIndex(max(0, w.findData(val)))
            elif isinstance(w, QCheckBox):
                w.setChecked(bool(val))
            else:
                w.setValue(val)
            w.blockSignals(False)
        self.name_edit.blockSignals(True)
        self.name_edit.setText(self.params.name)
        self.name_edit.blockSignals(False)
        self._update_title()
        self._sync_enabled()

    def _sync_enabled(self):
        """Grey out settings that don't apply to the current choices."""
        p = self.params
        rules = {
            "maze_length": p.rows == 0,
            "plain_wall": not p.vase_mode,
            "cap_wall": not p.vase_mode,
            "core_wall": not p.vase_mode,
            "grip_flutes": not p.vase_mode,
            "grip_depth": not p.vase_mode and p.grip_flutes > 0,
            "chamfer_ceilings": not p.vase_mode,     # always on in vase mode
            "vase_wall": p.vase_mode,
        }
        for key, on in rules.items():
            if key in self.widgets:
                self.widgets[key].setEnabled(on)

    def _build_actions(self):
        st = self.style()
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        def act(text, slot, icon=None, shortcut=None, tip=None):
            a = QAction(text, self)
            if icon is not None:
                a.setIcon(st.standardIcon(icon))
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setToolTip(tip)
                a.setStatusTip(tip)
            a.triggered.connect(slot)
            return a

        self.a_new = act("New", self.new_design, QStyle.SP_FileIcon, "Ctrl+N", "Reset to default parameters")
        self.a_open = act("Open…", self.open_design, QStyle.SP_DialogOpenButton, "Ctrl+O",
                          "Open a saved maze tube design")
        self.a_save = act("Save", self.save_design, QStyle.SP_DialogSaveButton, "Ctrl+S",
                          "Save the design (parameters + maze)")
        self.a_save_as = act("Save As…", lambda: self.save_design(as_new=True), None, "Ctrl+Shift+S")
        self.a_exp_both = act("Export STLs…", lambda: self.export_stl("both"), None,
                              "Ctrl+E", "Export body and cap as two STL files, ready to print")
        self.a_exp_both.setIcon(glyph_icon("export"))
        self.a_exp_body = act("Export body STL…", lambda: self.export_stl("body"))
        self.a_exp_cap = act("Export cap STL…", lambda: self.export_stl("cap"))
        self.a_exp_plate = act("Export print plate STL…", lambda: self.export_stl("plate"),
                               tip="Both parts side by side in one STL, in print orientation")
        self.a_exp_map = act("Export maze map…", self.export_map, None,
                             "Ctrl+M", "Save the unrolled maze (with solution) as PNG or SVG")
        self.a_exp_map.setIcon(glyph_icon("map"))
        self.a_snap = act("Save 3D snapshot…", self.save_snapshot, None, "Ctrl+P")
        self.a_random = act("New random maze", self.randomize_seed, None, "Ctrl+R")
        self.a_play = act("Play / stop solution", self.toggle_play, None, "F5")
        self.a_quit = act("Exit", self.close, None, "Ctrl+Q")
        self.a_about = act("About", self.about)

        tb.addAction(self.a_new)
        tb.addAction(self.a_open)
        tb.addAction(self.a_save)
        tb.addSeparator()
        exp_btn = QToolButton()
        exp_btn.setDefaultAction(self.a_exp_both)
        exp_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        exp_btn.setPopupMode(QToolButton.MenuButtonPopup)
        m = QMenu(exp_btn)
        for a in (self.a_exp_both, self.a_exp_body, self.a_exp_cap, self.a_exp_plate):
            m.addAction(a)
        exp_btn.setMenu(m)
        tb.addWidget(exp_btn)
        tb.addAction(self.a_exp_map)

        mb = self.menuBar()
        fm = mb.addMenu("&File")
        for a in (self.a_new, self.a_open, self.a_save, self.a_save_as):
            fm.addAction(a)
        fm.addSeparator()
        for a in (self.a_exp_both, self.a_exp_body, self.a_exp_cap, self.a_exp_plate,
                  self.a_exp_map, self.a_snap):
            fm.addAction(a)
        fm.addSeparator()
        fm.addAction(self.a_quit)
        mm = mb.addMenu("&Maze")
        mm.addAction(self.a_random)
        mm.addAction(self.a_play)
        vm = mb.addMenu("&View")
        for i, name in enumerate(VIEW_MODES):
            a = QAction(name, self)
            a.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            a.triggered.connect(lambda _=False, i=i: self.mode_combo.setCurrentIndex(i))
            vm.addAction(a)
        hm = mb.addMenu("&Help")
        hm.addAction(self.a_about)
        for a in (self.a_random, self.a_play):
            self.addAction(a)

    # ============================================================ params
    def _on_param(self, key, value):
        setattr(self.params, key, value)
        if key == "vase_mode" and value:
            p = self.params
            need = math.ceil((2 * p.groove_depth + 2 * p.clearance + 0.6) * 10 - 1e-6) / 10.0
            if p.path_width < need:
                p.path_width = need
                self._refresh_widgets()
                self.statusBar().showMessage(
                    f"Spiral vase mode: path width set to {need:.1f} mm so the nubs fit "
                    f"between the 45° groove slopes.", 10000)
        self._sync_enabled()
        self._dirty = True
        self._update_title()
        self.regen_timer.start()

    def _on_name(self, text):
        self.params.name = text.strip() or "maze_tube"
        self._dirty = True
        self._update_title()

    def randomize_seed(self):
        import random
        self.params.seed = random.randint(1, 999_999)
        self._refresh_widgets()
        self._dirty = True
        self.regenerate()

    def _update_title(self):
        f = os.path.basename(self.current_file) if self.current_file else "unsaved"
        star = " •" if self._dirty else ""
        self.setWindowTitle(f"Maze Tube Designer — {self.params.name} ({f}){star}")

    # ============================================================ build
    def regenerate(self):
        self.stop_anim()
        t0 = time.perf_counter()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            d = TubeDesign(self.params, self.maze, self.maze_key)
            if d.errors:
                self._show_info(None, d.errors, d.warnings)
                self.statusBar().showMessage("Invalid parameters - see the panel on the right")
                return
            (bv, bf), (cv, cf) = d.display_meshes()   # vase mode: shown as printed shells
            fit_gap, snap = d.check_solution_fit(step=0.5)
        except Exception as exc:   # pragma: no cover - safety net for the GUI
            self._show_info(None, [f"Internal error: {exc}"], [])
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.design = d
        self.maze = d.maze
        self.maze_key = d.maze_key()
        self.viewer.set_mesh("body", bv, bf, BODY_COLOR)
        self.viewer.set_mesh("cap", cv, cf, CAP_COLOR)
        r = np.hypot(cv[:, 0], cv[:, 1])
        in_band = ((cv[:, 2] > d.zn0 - 0.2) & (cv[:, 2] < d.zn1 + 0.2)
                   & (r > d.r_tip + d.w_v - 0.01))
        on_nub = in_band & (r < d.r_cap_in + d.w_v - 1e-3)
        nub_f = cf[on_nub[cf].any(axis=1) & in_band[cf].all(axis=1)]
        self.viewer.set_mesh("nubs", cv, nub_f, NUB_COLOR)
        self.viewer.configure("nubs", overlay=True)
        self.map.set_design(d)
        self.apply_view_mode(fit=None)
        dt = time.perf_counter() - t0
        self._show_info(d, [], d.warnings, fit_gap, snap)
        self.statusBar().showMessage(
            f"Generated in {dt * 1000:.0f} ms — {len(bf) + len(cf):,} triangles", 6000)

    def _show_info(self, d, errors, warnings, fit_gap=None, snap=0.0):
        css = ("<style>td{padding:2px 6px;} .k{color:#9aa3ad;} .e{color:#ff6b6b;} "
               ".w{color:#f2c14e;} .ok{color:#6fdc8c;} h3{margin:4px 0 6px 0;}</style>")
        html = [css]
        if errors:
            html.append("<h3 class='e'>Cannot build this design</h3><ul>")
            html += [f"<li class='e'>{e}</li>" for e in errors]
            html.append("</ul>")
        if d is not None:
            html.append("<h3>Dimensions</h3><table>")
            for k, v in d.info().items():
                html.append(f"<tr><td class='k'>{k}</td><td>{v}</td></tr>")
            html.append("</table>")
            if fit_gap is not None:
                if fit_gap > 0.5 * d.c:
                    html.append(f"<p class='ok'>✔ Fit check: the cap slides along the whole "
                                f"solution with {fit_gap:.2f} mm clearance.</p>")
                else:
                    html.append(f"<p class='e'>✘ Fit check failed (gap {fit_gap:.2f} mm).</p>")
                if d.detent_h > 0:
                    html.append(f"<p class='ok'>✔ Home detent: the nub snaps over a "
                                f"{d.detent_h:.2f} mm ridge ({snap:.2f} mm radial push) into "
                                f"a free pocket at the closed position.</p>")
            for note in d.notes:
                html.append(f"<p class='k'>ℹ {note}</p>")
        if warnings:
            html.append("<h3 class='w'>Warnings</h3><ul>")
            html += [f"<li class='w'>{w}</li>" for w in warnings]
            html.append("</ul>")
        if d is not None:
            html.append("<p class='k'>Print the body upright and the cap upside down "
                        "(open end up) — no supports needed"
                        + (" with 45° ceilings." if d.dk > 0 else ".") + "</p>")
            if d.vase:
                html.append(
                    f"<p class='k'><b>Slicer (spiral vase):</b> turn on Spiral vase / Spiralize, set "
                    f"the outer wall line width to <b>{d.w_v:.2f} mm</b> (the cap's fit depends on it), "
                    f"and use bottom layers of about {d.p.bottom_thickness:.1f} mm for the body and at most "
                    f"{d.p.cap_top:.1f} mm for the cap. These exports are solid on purpose: don't "
                    f"slice them normally.</p>")
            else:
                html.append("<p class='k'><b>Slicer:</b> use at least 3 wall loops, 4–5 top layers, "
                            "3–4 bottom layers and 15–20% infill so the maze ridges, floor and cap "
                            "end print solid.</p>")
        self.info.setHtml("".join(html))

    # ============================================================ view
    def apply_view_mode(self, fit=False):
        d = self.design
        if d is None:
            return
        mode = self.mode_combo.currentText()
        xray = self.xray_chk.isChecked()
        v = self.viewer
        dx = d.R_out + 6.0
        body_m = np.identity(4)
        cap_m = np.identity(4)
        body_vis = cap_vis = True
        alpha = 1.0
        if mode == "Print layout":
            body_m = make_transform(offset=(-dx, 0, 0))
            cap_m = make_transform(flip=True, offset=(dx, 0, d.H_cap))
        elif mode == "Assembled":
            phi, Z = self.cap_pose
            cap_m = make_transform(rot_z=phi, offset=(0, 0, d.z0 + Z))
            alpha = 0.30 if xray else 1.0
        elif mode == "Exploded":
            cap_m = make_transform(offset=(0, 0, d.H_body + 14.0))
            alpha = 0.30 if xray else 1.0
        elif mode == "Body only":
            cap_vis = False
        elif mode == "Cap only":
            body_vis = False
            cap_m = make_transform(flip=True, offset=(0, 0, d.H_cap))
        v.configure("body", model=body_m, visible=body_vis)
        v.configure("cap", model=cap_m, visible=cap_vis, alpha=alpha)
        v.configure("nubs", model=cap_m, visible=cap_vis)
        lo, hi = v.scene_bounds()
        if fit is None:   # auto: refit only if the scene size changed noticeably
            if self._fitted_bounds is None:
                fit = True
            else:
                flo, fhi = self._fitted_bounds
                size, fsize = np.linalg.norm(hi - lo), np.linalg.norm(fhi - flo)
                fit = abs(size - fsize) > 0.15 * fsize
        if fit:
            v.fit_view(reset_angles=self._fitted_bounds is None)
            self._fitted_bounds = (lo, hi)

    # ============================================================ animation
    def toggle_play(self):
        if self.anim is not None:
            self.stop_anim()
            return
        d = self.design
        if d is None or not d.maze.moves:
            return
        frames = d.solution_keyframes(lift=18.0)
        cum = [0.0]
        for (p0, z0), (p1, z1) in zip(frames, frames[1:]):
            cum.append(cum[-1] + math.hypot((p1 - p0) * d.r_maze, z1 - z0))
        self.anim = dict(frames=frames, cum=cum, t0=time.perf_counter(), last=None, dist=0.0)
        if self.mode_combo.currentText() != "Assembled":
            self.mode_combo.setCurrentText("Assembled")
        if not self.xray_chk.isChecked():
            self.xray_chk.setChecked(True)
        self.play_btn.setText("  Stop")
        self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.anim_timer.start()

    def stop_anim(self):
        if self.anim is None:
            return
        self.anim_timer.stop()
        self.anim = None
        self.cap_pose = (0.0, 0.0)
        self.map.set_marker(None)
        self.play_btn.setText("  Play solution")
        self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.apply_view_mode()

    def _anim_tick(self):
        a = self.anim
        if a is None:
            return
        now = time.perf_counter()
        speed = BASE_SPEED * SPEEDS.get(self.speed_combo.currentText(), 1.0)
        if a["last"] is not None:
            a["dist"] += (now - a["last"]) * speed
        a["last"] = now
        cum, frames = a["cum"], a["frames"]
        total = cum[-1]
        if a["dist"] > total + speed * 1.2:      # short hold at the end
            self.stop_anim()
            return
        dist = min(a["dist"], total)
        i = min(max(bisect.bisect_right(cum, dist) - 1, 0), len(frames) - 2)
        seg = cum[i + 1] - cum[i]
        t = 0.0 if seg <= 0 else (dist - cum[i]) / seg
        (p0, z0), (p1, z1) = frames[i], frames[i + 1]
        self.set_cap_pose(p0 + (p1 - p0) * t, z0 + (z1 - z0) * t)

    def set_cap_pose(self, phi, Z):
        self.cap_pose = (phi, Z)
        self.map.set_marker((phi, Z))
        d = self.design
        if d is not None and self.mode_combo.currentText() == "Assembled":
            m = make_transform(rot_z=phi, offset=(0, 0, d.z0 + Z))
            self.viewer.configure("cap", model=m)
            self.viewer.configure("nubs", model=m)

    # ============================================================ files
    def _last_dir(self):
        return self.settings.value("last_dir", os.path.join(os.path.expanduser("~"), "Documents"))

    def _remember_dir(self, path):
        self.settings.setValue("last_dir", os.path.dirname(path))

    def _safe_name(self):
        name = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in self.params.name).strip()
        return name or "maze_tube"

    def new_design(self):
        if not self._confirm_discard():
            return
        self.params = TubeParams()
        self.maze = self.maze_key = None
        self.current_file = None
        self._dirty = False
        self._refresh_widgets()
        self.regenerate()

    def _confirm_discard(self):
        if not self._dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes",
                                 "Discard unsaved changes to the current design?",
                                 QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
        return r == QMessageBox.Discard

    def open_design(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open design", self._last_dir(),
                                              "Maze tube designs (*.mazetube *.json);;All files (*)")
        if not path:
            return
        self.load_file(path)

    def load_file(self, path):
        try:
            params, maze, key = load_design(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", f"Could not open the design:\n{exc}")
            return
        self._remember_dir(path)
        self.params = params
        self.maze, self.maze_key = maze, key
        self.current_file = path
        self._dirty = False
        self._refresh_widgets()
        self.regenerate()
        self.statusBar().showMessage(f"Opened {path}", 6000)

    def save_design(self, as_new=False):
        path = self.current_file
        if as_new or not path:
            default = os.path.join(self._last_dir(), self._safe_name() + ".mazetube")
            path, _ = QFileDialog.getSaveFileName(self, "Save design", default,
                                                  "Maze tube designs (*.mazetube)")
            if not path:
                return
            if not os.path.splitext(path)[1]:
                path += ".mazetube"
        try:
            save_design(path, self.params, self.maze, self.maze_key)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._remember_dir(path)
        self.current_file = path
        self._dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Saved {path}", 6000)

    def export_stl(self, kind):
        d = self.design
        if d is None:
            QMessageBox.warning(self, "Nothing to export", "The current parameters are not valid.")
            return
        base = self._safe_name()
        if d.vase and not base.endswith("_vase"):
            base += "_vase"       # make it obvious these are solid spiral-vase models
        suffix = {"both": "", "body": "_body", "cap": "_cap", "plate": "_plate"}[kind]
        default = os.path.join(self._last_dir(), base + suffix + ".stl")
        title = {"both": "Export STLs (writes _body.stl and _cap.stl)", "body": "Export body STL",
                 "cap": "Export cap STL", "plate": "Export print plate STL"}[kind]
        path, _ = QFileDialog.getSaveFileName(self, title, default, "STL files (*.stl)")
        if not path:
            return
        if not path.lower().endswith(".stl"):
            path += ".stl"
        root = path[:-4]
        bv, bf = d.body_mesh()
        cv, cf = d.cap_mesh()
        cap_print = transform(cv, flip_x=True, offset=(0, 0, d.H_cap))
        written = []
        try:
            if kind == "both":
                for tag in ("_body", "_cap"):
                    if root.endswith(tag):
                        root = root[: -len(tag)]
                write_stl(root + "_body.stl", bv, bf, base + " body")
                write_stl(root + "_cap.stl", cap_print, cf, base + " cap")
                written = [root + "_body.stl", root + "_cap.stl"]
            elif kind == "body":
                write_stl(path, bv, bf, base + " body")
                written = [path]
            elif kind == "cap":
                write_stl(path, cap_print, cf, base + " cap")
                written = [path]
            else:
                pv, pf = print_plate(d)
                write_stl(path, pv, pf, base + " plate")
                written = [path]
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self._remember_dir(path)
        names = ", ".join(os.path.basename(w) for w in written)
        self.statusBar().showMessage(f"Exported {names} to {os.path.dirname(path)}", 10000)

    def export_map(self):
        d = self.design
        if d is None:
            return
        default = os.path.join(self._last_dir(), self._safe_name() + "_maze.png")
        path, flt = QFileDialog.getSaveFileName(self, "Export maze map", default,
                                                "PNG image (*.png);;SVG drawing (*.svg)")
        if not path:
            return
        title = f"{self.params.name} — {d.N}×{d.M} maze, {len(d.maze.moves)} moves"
        w = 2400
        h = int((w - 170) * (d.H_body - d.z0) / (2 * math.pi * d.r_maze)) + 300
        self.map.dark = False
        try:
            if path.lower().endswith(".svg"):
                try:
                    from PySide6.QtSvg import QSvgGenerator
                except ImportError:
                    QMessageBox.warning(self, "SVG", "SVG support is not available; use PNG.")
                    return
                gen = QSvgGenerator()
                gen.setFileName(path)
                gen.setSize(QSize(w, h))
                gen.setViewBox(QRectF(0, 0, w, h))
                gen.setTitle(title)
                p = QPainter(gen)
                self.map.render_map(p, QRectF(0, 0, w, h), title)
                p.end()
            else:
                if not path.lower().endswith(".png"):
                    path += ".png"
                img = QImage(w, h, QImage.Format_ARGB32)
                img.fill(QColor("white"))
                p = QPainter(img)
                p.setRenderHint(QPainter.Antialiasing)
                self.map.render_map(p, QRectF(0, 0, w, h), title)
                p.end()
                img.save(path)
        finally:
            self.map.dark = True
            self.map.update()
        self._remember_dir(path)
        self.statusBar().showMessage(f"Saved maze map to {path}", 8000)

    def save_snapshot(self):
        default = os.path.join(self._last_dir(), self._safe_name() + "_3d.png")
        path, _ = QFileDialog.getSaveFileName(self, "Save 3D snapshot", default, "PNG image (*.png)")
        if not path:
            return
        self.viewer.grabFramebuffer().save(path)
        self._remember_dir(path)
        self.statusBar().showMessage(f"Saved snapshot to {path}", 8000)

    def about(self):
        QMessageBox.about(
            self, "About Maze Tube Designer",
            f"<h3>Maze Tube Designer {__version__}</h3>"
            "<p>Designs printable cylinder maze puzzle boxes: an inner tube with a maze "
            "carved into its surface and a cap whose nubs must be guided through the maze "
            "before it comes off.</p>"
            "<p><b>3D view:</b> left-drag rotate · right-drag / shift-drag pan · wheel zoom · "
            "double-click or F to fit.</p>"
            "<p>Exported STLs are in millimetres, already oriented for printing.</p>")

    def closeEvent(self, ev):
        if not self._confirm_discard():
            ev.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(ev)


# ============================================================== bootstrap
def apply_dark_theme(app: QApplication):
    app.setStyle("Fusion")
    pal = QPalette()
    c = QColor
    pal.setColor(QPalette.Window, c(36, 39, 45))
    pal.setColor(QPalette.WindowText, c(222, 225, 230))
    pal.setColor(QPalette.Base, c(26, 28, 33))
    pal.setColor(QPalette.AlternateBase, c(44, 47, 54))
    pal.setColor(QPalette.ToolTipBase, c(52, 56, 64))
    pal.setColor(QPalette.ToolTipText, c(230, 230, 230))
    pal.setColor(QPalette.Text, c(222, 225, 230))
    pal.setColor(QPalette.Button, c(50, 54, 62))
    pal.setColor(QPalette.ButtonText, c(222, 225, 230))
    pal.setColor(QPalette.BrightText, c(255, 90, 90))
    pal.setColor(QPalette.Highlight, c(56, 132, 214))
    pal.setColor(QPalette.HighlightedText, c(255, 255, 255))
    pal.setColor(QPalette.Link, c(110, 180, 255))
    pal.setColor(QPalette.PlaceholderText, c(130, 135, 145))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        pal.setColor(QPalette.Disabled, role, c(120, 124, 132))
    app.setPalette(pal)
    app.setStyleSheet("""
        QGroupBox { font-weight: 600; border: 1px solid #3b4049; border-radius: 6px;
                    margin-top: 12px; padding-top: 4px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #f0a356; }
        QTextBrowser { border: none; }
        QToolBar { border: none; spacing: 4px; padding: 3px; }
        QSplitter::handle { background: #2b2e34; }
    """)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    fmt = QSurfaceFormat()
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
    QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(argv)
    app.setApplicationName("Maze Tube Designer")
    app.setOrganizationName("MazeTube")
    apply_dark_theme(app)
    win = MainWindow()
    win.show()

    # optional: open a design given on the command line
    files = [a for a in argv[1:] if not a.startswith("--") and os.path.isfile(a)]
    if files:
        win.load_file(files[0])

    # developer hook: --screenshot out.png [--mode N] [--section] [--pose t]
    if "--screenshot" in argv:
        out = argv[argv.index("--screenshot") + 1]
        if "--mode" in argv:
            win.mode_combo.setCurrentIndex(int(argv[argv.index("--mode") + 1]))
        if "--section" in argv:
            win.section_chk.setChecked(True)
        if "--pose" in argv:
            frac = float(argv[argv.index("--pose") + 1])
            win.toggle_play()
            a = win.anim
            a["dist"] = a["cum"][-1] * frac
            a["last"] = None
            win.anim_timer.stop()
            win._anim_tick()
            win.anim_timer.stop()

        def snap():
            for flag, attr in (("--yaw", "yaw"), ("--pitch", "pitch")):
                if flag in argv:
                    setattr(win.viewer, attr, float(argv[argv.index(flag) + 1]))
            if "--zoom" in argv:
                win.viewer.dist /= float(argv[argv.index("--zoom") + 1])
            if "--target" in argv:
                win.viewer.target = np.array(
                    [float(v) for v in argv[argv.index("--target") + 1].split(",")])
            win.viewer.repaint()
            win.grab().save(out)
            app.quit()
        QTimer.singleShot(1800, snap)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
