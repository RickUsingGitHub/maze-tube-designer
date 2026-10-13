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
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea,
                               QSpinBox, QSplitter, QStyle, QTextBrowser, QToolButton,
                               QVBoxLayout, QWidget)

from . import __version__
from .geometry import TubeDesign, print_plate, print_plate_text
from .gifttag import save_gift_tag
from .maze import ALGORITHM_TIPS, ALGORITHMS
from .mazeview import MazeMapWidget
from .params import TubeParams, load_design, save_design
from .presets import PRESETS, slug
from .stl import write_stl
from .threemf import write_3mf
from .viewer import Viewer3D, make_transform

BODY_COLOR = (0.93, 0.56, 0.22)
CAP_COLOR = (0.24, 0.58, 0.88)
NUB_COLOR = (0.98, 0.30, 0.38)
PART_COLORS = {"body": BODY_COLOR, "cap": CAP_COLOR, "extension": (0.20, 0.72, 0.70)}
TEXT_COLOR = (0.96, 0.95, 0.90)     # second-colour lettering

VIEW_MODES = ["Print layout", "Assembled", "Exploded", "Body only", "Cap only"]
SPEEDS = {"0.5×": 0.5, "1×": 1.0, "2×": 2.0, "4×": 4.0}
BASE_SPEED = 22.0   # mm/s along the maze path

# Settings shown in Simple mode; everything else appears only in Detailed mode.
SIMPLE_KEYS = {"content_diameter", "content_length", "printer_height", "bed_x", "bed_y",
               "clearance", "vase_mode", "vase_wall", "nubs", "maze_length", "detent_level",
               "cap_text", "base_text", "text_height", "text_style"}

# (key, label, kind, min, max, step, suffix/special, tooltip)
SPEC = [
    ("Contents", [
        ("auto_size", "Size from contents", "bool", None, None, None, "",
         "Work out the tube's diameter and lengths from what goes inside and the printer height.\n"
         "Turn off to set the maze diameter, body length and cap length yourself."),
        ("content_diameter", "Diameter", "float", 3.0, 240.0, 1.0, " mm",
         "Widest part of what goes inside. The bore is made this wide plus the fit slack."),
        ("content_length", "Length", "float", 3.0, 1900.0, 1.0, " mm",
         "Length of what goes inside. Short things sit in the body; long things are shared\n"
         "between body and cap so each part still fits the printer."),
        ("fit_slack", "Fit slack", "float", 0.0, 20.0, 0.5, " mm",
         "Spare room added around and above the contents."),
    ]),
    ("Printer", [
        ("printer_height", "Printer height", "float", 30.0, 1000.0, 5.0, " mm",
         "Tallest part your printer can make (Z height). Parts are kept within it:\n"
         "for long contents the maze overlap is shortened automatically."),
        ("bed_x", "Bed width", "float", 30.0, 2000.0, 5.0, " mm",
         "Width of the printer bed. The tube's diameter is checked against the bed, and the\n"
         "plate / 3MF export lays the parts out to fit it."),
        ("bed_y", "Bed depth", "float", 30.0, 2000.0, 5.0, " mm", "Depth of the printer bed."),
        ("clearance", "Clearance", "float", 0.05, 1.5, 0.05, " mm",
         "Gap between the parts (radial and around the nubs). 0.25-0.35 mm suits most FDM\n"
         "printers. Raise it by 0.05 mm if the cap is tight, lower it if it rattles."),
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
    ("Text", [
        ("cap_text", "On the cap", "text", None, None, None, "e.g. Happy Birthday\n(or a whole poem)",
         "Text round the cap. Leave empty for none. Press Enter for a new line.\n"
         "Lines that fit round the tube are stacked and centred. Anything longer runs round\n"
         "and round in a spiral, and the cap is made longer to hold it. The letters keep their size."),
        ("base_text", "On the base", "text", None, None, None, "e.g. a name",
         "Text round the body's grip band (below the maze). Several lines are fine,\n"
         "and long text spirals. The grip band is made taller to hold it."),
        ("text_height", "Letter height", "float", 2.5, 60.0, 0.5, " mm",
         "Height of the capital letters. It is kept as set: long text spirals rather than\n"
         "shrinks. (Only if the printer height cannot hold the text are letters made smaller.)"),
        ("text_style", "Lettering", "choice", [(0, "Raised"), (1, "Engraved"),
                                               (2, "Second colour, flush")], None, None, "",
         "Raised letters stand out from the wall; engraved letters are cut into it.\n"
         "Second colour: the letters sit flush with the wall as separate pieces, to print in\n"
         "another filament on a multi-colour printer. Export the 3MF and give the “text”\n"
         "part of each piece your second filament.\n"
         "In spiral vase mode the text is raised (or engraved on the base)."),
        ("text_depth", "Depth", "float", 0.2, 1.5, 0.1, " mm",
         "How far the letters stand out from the wall (or are cut into it).\n"
         "Second-colour letters go at least 0.8 mm deep, so the colour prints solid."),
    ]),
    ("Maze", [
        ("nubs", "Nubs", "int", 1, 6, 1, "",
         "Number of nubs on the cap. The maze repeats once per nub so all nubs move together.\n"
         "3 nubs keep the cap centred; fewer nubs give a bigger maze per nub."),
        ("maze_length", "Maze length", "float", 10.0, 1000.0, 5.0, " mm",
         "Longest maze section allowed (rows are fitted inside it). A longer maze is harder.\n"
         "It is shortened automatically when the parts would not fit the printer."),
        ("rows", "Rows", "int", 0, 200, 1, "Auto",
         "Number of maze rows along the tube axis.\n"
         "Auto = as many rows as fit in the maze length."),
        ("columns", "Columns", "int", 0, 240, 1, "Auto",
         "Total columns around the tube. Auto fits as many as path + wall width allow.\n"
         "Always rounded to a multiple of the nub count."),
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
         "Used when Click shut is Off; the twist lock has its own row instead."),
        ("detent_level", "Click shut", "choice", [(0, "Off"), (1, "Light"), (2, "Medium"),
                                                  (3, "Firm")], None, None, "",
         "A twist lock at the closed position. One extra row below the maze: push the cap\n"
         "down, then turn it clockwise (seen from its closed end) until it clicks into a\n"
         "pocket. Locked, it cannot be pulled off; turn it anticlockwise to release it.\n"
         "The landing is flat. Just before it comes to rest, one nub passes a small bump: a\n"
         "thin line with 45° sides. Light, Medium and Firm make it 0.15, 0.25 and 0.40 mm\n"
         "higher than the nub's clearance. A tight print feels firmer, so start with Light.\n"
         "Off: no lock row, the cap just drops into the home notch."),
        ("detent", "Click bump", "float", 0.0, 1.5, 0.05, " mm|Automatic",
         "How far the click bump stands above the nub's clearance. Automatic takes it from\n"
         "the setting above. Set a value to tune it by hand; a cap is stiff, so keep it small."),
    ]),
    ("Body (inner tube)", [
        ("maze_diameter", "Maze diameter", "float", 12.0, 250.0, 0.5, " mm",
         "Outside diameter of the inner tube, measured over the maze ridges.\n"
         "Set automatically while 'Size from contents' is on."),
        ("groove_depth", "Groove depth", "float", 0.6, 6.0, 0.1, " mm", "Depth of the maze grooves (= nub height)."),
        ("core_wall", "Core wall", "float", 0.6, 10.0, 0.1, " mm", "Wall thickness under the groove floor."),
        ("body_length", "Body length", "float", 0.0, 2000.0, 1.0, " mm|Auto",
         "Total length of the body. The maze sits at the top (where the cap goes on),\n"
         "the rest is plain tube. Auto = grip band + maze only.\n"
         "Set automatically while 'Size from contents' is on."),
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
        ("cap_length", "Cap length", "float", 0.0, 2000.0, 1.0, " mm|Auto",
         "Total length of the cap. Auto = just long enough to cover the maze.\n"
         "A longer cap extends past the body's rim and adds storage space.\n"
         "Set automatically while 'Size from contents' is on."),
        ("pieces", "Pieces", "choice", [(0, "Automatic"), (2, "Two: body + cap"),
                                        (3, "Three: + extension")], None, None, "",
         "Three pieces split the cap into an extension (the tube that runs in the maze) and a\n"
         "cap end, glued together - for contents too long for two printable parts.\n"
         "Automatic uses three only when two would leave almost no maze."),
        ("joint_length", "Joint length", "float", 4.0, 40.0, 1.0, " mm",
         "Overlap of the glued joint between the extension and the cap end."),
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
]

# Rows of the dimensions panel shown in Simple mode
SIMPLE_INFO = {"Contents", "Layout", "Tallest part", "Printer bed", "Outer diameter", "Home click",
               "Closed length", "Body length", "Extension length", "Cap length",
               "Storage inside", "Text", "Solution", "PLA estimate"}


def write_part_stls(root: str, part, title: str) -> list:
    """Write a piece as root.stl, plus root_text.stl if it has second-colour letters.

    Both are in print orientation and share coordinates, so the letters sit in
    their cut-outs when the two files are loaded together.
    """
    write_stl(root + ".stl", part.print_verts(), part.faces, title)
    written = [root + ".stl"]
    if part.inlay is not None:
        write_stl(root + "_text.stl", part.inlay_print_verts(), part.inlay[1], title + " text")
        written.append(root + "_text.stl")
    return written


def plate_objects(d, base: str, origin=(0.0, 0.0)):
    """Every piece for a 3MF, laid out round ``origin``: (objects for write_3mf, all fit the bed)."""
    positions, fits = d.plate_layout()
    objects = []
    for pt, (x, y) in zip(d.parts(), positions):
        text = ([] if pt.inlay is None
                else [(f"{base} {pt.name} text", pt.inlay_print_verts(), pt.inlay[1])])
        objects.append((f"{base} {pt.name}", pt.print_verts(), pt.faces,
                        (x + origin[0], y + origin[1], 0.0), text))
    return objects, fits


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
    elif kind == "tag":
        p.drawPolygon([QPointF(10, 30), QPointF(26, 14), QPointF(54, 14), QPointF(54, 50),
                       QPointF(26, 50)])
        pen.setColor(QColor(240, 163, 86))
        p.setPen(pen)
        p.drawEllipse(QPointF(27, 32), 4, 4)
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
        self.simple_mode = self.settings.value("simple_mode", True, type=bool)
        self.params = self._fresh_params()
        self.design: TubeDesign | None = None
        self.maze = None
        self.maze_key = None
        self.current_file = None
        self.cap_pose = (0.0, 0.0)
        self.anim = None
        self._fitted_bounds = None
        self._dirty = False
        self._last_fit = (None, 0.0)
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

        # Simple / Detailed switch
        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)
        self.simple_btn = QPushButton("Simple")
        self.detail_btn = QPushButton("Detailed")
        self.simple_btn.setToolTip("Just the essentials: what goes inside, your printer and the maze size.")
        self.detail_btn.setToolTip("Every setting.")
        for b in (self.simple_btn, self.detail_btn):
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setMinimumHeight(28)
            b.setObjectName("modeButton")
            mode_row.addWidget(b)
        (self.simple_btn if self.simple_mode else self.detail_btn).setChecked(True)
        self.simple_btn.clicked.connect(lambda: self.set_simple_mode(True))
        self.detail_btn.clicked.connect(lambda: self.set_simple_mode(False))
        pl.addLayout(mode_row)

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

        self._rows = {}       # key -> (form, field widget)
        self._groups = []     # (group box, [keys])
        for title, items in SPEC:
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.setContentsMargins(8, 12, 8, 8)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(5)
            if title == "Contents":
                self.preset_btn = QPushButton()
                self.preset_btn.setMinimumHeight(28)
                self.preset_btn.setToolTip("Pick a standard size for what the tube should hold.\n"
                                           "Sizes are typical - measure yours if the fit matters.")
                self.preset_btn.setMenu(self._build_preset_menu())
                form.addRow(self.preset_btn)
            for key, label, kind, lo, hi, step, extra, tip in items:
                w = self._make_widget(key, kind, lo, hi, step, extra)
                lab = QLabel(label)
                lab.setToolTip(tip)
                w.setToolTip(tip)
                form.addRow(lab, w)
                self._rows[key] = (form, w)
            self._groups.append((box, [it[0] for it in items]))
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
        elif kind == "text":
            w = QPlainTextEdit(str(val))
            w.setPlaceholderText(extra)
            w.setTabChangesFocus(True)
            w.setFixedHeight(62)                  # about three lines; scrolls for a poem
            w.textChanged.connect(lambda k=key, ww=w: self._on_param(k, ww.toPlainText()))
        elif kind == "choice":
            w = QComboBox()
            for value, label in lo:
                w.addItem(label, value)
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
            elif isinstance(w, QPlainTextEdit):
                if w.toPlainText() != str(val):
                    w.setPlainText(str(val))
            else:
                w.setValue(val)
            w.blockSignals(False)
        self.name_edit.blockSignals(True)
        self.name_edit.setText(self.params.name)
        self.name_edit.blockSignals(False)
        self._update_title()
        self._sync_enabled()

    def _fresh_params(self) -> TubeParams:
        """Default design, using the printer settings remembered from last time."""
        p = TubeParams()
        for key in ("printer_height", "bed_x", "bed_y", "clearance"):
            try:
                v = self.settings.value(f"default_{key}")
                if v is not None:
                    setattr(p, key, float(v))
            except (TypeError, ValueError):
                pass
        return p

    def _build_preset_menu(self) -> QMenu:
        menu = QMenu(self)
        for category, items in PRESETS:
            sub = menu.addMenu(category)
            for item in items:
                label, dia, length = item[0], float(item[1]), float(item[2])
                act = sub.addAction(f"{label}    Ø{dia:g} × {length:g} mm")
                if len(item) > 3:
                    act.setToolTip(item[3])
                act.triggered.connect(
                    lambda _=False, a=label, b=dia, c=length: self.apply_preset(a, b, c))
            sub.setToolTipsVisible(True)
        menu.addSeparator()
        note = menu.addAction("Sizes are typical – measure yours if the fit matters")
        note.setEnabled(False)
        return menu

    def apply_preset(self, label: str, dia: float, length: float):
        p = self.params
        generic = {"maze_tube", ""} | {slug(it[0]) for _, items in PRESETS for it in items}
        if p.name in generic:
            p.name = slug(label)
        p.content_diameter, p.content_length, p.preset = dia, length, label
        self._set_auto(True)
        self._dirty = True
        self._refresh_widgets()
        self.regenerate()
        self.statusBar().showMessage(f"Sized for: {label} (Ø{dia:g} × {length:g} mm)", 8000)

    def _set_auto(self, on: bool):
        """Switch between sizing from the contents and explicit dimensions."""
        p = self.params
        p.auto_size = on
        if on:
            p.rows = 0
        else:
            d = self.design
            if d is not None and d.auto and d.maze is not None:
                # keep exactly the current tube, now as explicit numbers
                p.rows = d.M
                p.maze_diameter = round(2 * d.r_maze, 2)
                p.body_length = round(d.H_body, 1)
                p.cap_length = round(d.H_cap, 1)

    def set_simple_mode(self, simple: bool, remember: bool = True):
        self.simple_mode = simple
        (self.simple_btn if simple else self.detail_btn).setChecked(True)
        if remember:
            self.settings.setValue("simple_mode", simple)
        self._apply_mode()
        if self.design is not None:
            self._show_info(self.design, [], self.design.warnings, *self._last_fit)

    def _apply_mode(self):
        """Show only the essential settings in Simple mode."""
        p = self.params
        for key, (form, field) in self._rows.items():
            show = (not self.simple_mode) or key in SIMPLE_KEYS
            if key == "vase_wall" and self.simple_mode:
                show = p.vase_mode
            form.setRowVisible(field, show)
        for box, keys in self._groups:
            box.setVisible((not self.simple_mode) or any(k in SIMPLE_KEYS for k in keys))

    def _sync_enabled(self):
        """Grey out settings that don't apply to the current choices."""
        p = self.params
        self.preset_btn.setText(p.preset if (p.preset and p.auto_size) else "Choose what goes inside…")
        self._apply_mode()
        rules = {
            "maze_length": p.rows == 0 or p.auto_size,
            "rows": not p.auto_size,
            "maze_diameter": not p.auto_size,
            "body_length": not p.auto_size,
            "cap_length": not p.auto_size,
            "fit_slack": p.auto_size,
            "text_height": bool(p.cap_text.strip() or p.base_text.strip()),
            "text_depth": bool(p.cap_text.strip() or p.base_text.strip()),
            "text_style": bool(p.cap_text.strip() or p.base_text.strip()),
            "joint_length": p.pieces != 2,
            "detent": p.detent_level > 0,
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
        self.a_exp_both = act("Export STLs…", lambda: self.export_stl("all"), None,
                              "Ctrl+E", "Export every piece as its own STL file, ready to print")
        self.a_exp_both.setIcon(glyph_icon("export"))
        self.a_exp_3mf = act("Export 3MF (all parts, one plate)…", self.export_3mf, None,
                             "Ctrl+Shift+E",
                             "One 3MF file with every piece as a separate object, laid out on the bed")
        self.a_exp_body = act("Export body STL…", lambda: self.export_stl("body"))
        self.a_exp_ext = act("Export extension STL…", lambda: self.export_stl("extension"))
        self.a_exp_cap = act("Export cap STL…", lambda: self.export_stl("cap"))
        self.a_exp_plate = act("Export print plate STL…", lambda: self.export_stl("plate"),
                               tip="All pieces side by side in one STL, in print orientation")
        self.a_exp_tag = act("Export gift tag…", self.export_gift_tag, None, "Ctrl+G",
                             "Printable folding card with the solution hidden inside (PDF or PNG)")
        self.a_exp_tag.setIcon(glyph_icon("tag"))
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
        for a in (self.a_exp_both, self.a_exp_3mf, self.a_exp_plate, None,
                  self.a_exp_body, self.a_exp_ext, self.a_exp_cap):
            m.addAction(a) if a is not None else m.addSeparator()
        exp_btn.setMenu(m)
        tb.addWidget(exp_btn)
        tb.addAction(self.a_exp_map)
        tb.addAction(self.a_exp_tag)

        mb = self.menuBar()
        fm = mb.addMenu("&File")
        for a in (self.a_new, self.a_open, self.a_save, self.a_save_as):
            fm.addAction(a)
        fm.addSeparator()
        for a in (self.a_exp_both, self.a_exp_3mf, self.a_exp_plate, self.a_exp_body,
                  self.a_exp_ext, self.a_exp_cap, None, self.a_exp_map, self.a_exp_tag,
                  self.a_snap):
            fm.addAction(a) if a is not None else fm.addSeparator()
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
        p = self.params
        if key in ("content_diameter", "content_length"):
            p.preset = ""                       # a custom size now
            if not p.auto_size:                 # editing the contents means "size it for me"
                self._set_auto(True)
                self._refresh_widgets()
        elif key == "auto_size":
            self._set_auto(bool(value))
            self._refresh_widgets()
        if key in ("printer_height", "bed_x", "bed_y", "clearance"):
            self.settings.setValue(f"default_{key}", float(value))
        if key == "vase_mode" and value:
            # room for the 45° slopes plus a nub tip of about 1 mm
            need = math.ceil((2 * p.groove_depth + 2 * p.clearance + 1.0) * 10 - 1e-6) / 10.0
            if p.path_width < need:
                p.path_width = need
                self._refresh_widgets()
                self.statusBar().showMessage(
                    f"Spiral vase mode: path width set to {need:.1f} mm so the nubs fit "
                    f"between the 45° groove slopes.", 10000)
        self._sync_enabled()
        self._dirty = True
        self._update_title()
        self.regen_timer.start(900 if key in ("cap_text", "base_text") else 220)

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
            dparts = d.display_parts()                # vase mode: shown as printed shells
            fit_gap, snap = d.check_solution_fit(step=0.5)
        except Exception as exc:   # pragma: no cover - safety net for the GUI
            self._show_info(None, [f"Internal error: {exc}"], [])
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.design = d
        self.maze = d.maze
        self.maze_key = d.maze_key()
        names = [pt.name for pt in dparts]
        for old in PART_COLORS:
            if old not in names:
                self.viewer.remove_mesh(old)
                self.viewer.remove_mesh(old + "_text")
        for pt in dparts:
            self.viewer.set_mesh(pt.name, pt.verts, pt.faces, PART_COLORS[pt.name])
            if pt.inlay is not None:               # flush lettering in the second colour
                self.viewer.set_mesh(pt.name + "_text", *pt.inlay, TEXT_COLOR)
            else:
                self.viewer.remove_mesh(pt.name + "_text")
        self.a_exp_ext.setEnabled(d.three)
        nub_part = next(pt for pt in dparts if pt.name == ("extension" if d.three else "cap"))
        cv, cf = nub_part.verts, nub_part.faces      # highlight the nubs on the piece that has them
        r = np.hypot(cv[:, 0], cv[:, 1])
        in_band = ((cv[:, 2] > d.zn0 - 0.2) & (cv[:, 2] < d.zn1 + 0.2)
                   & (r > d.r_tip + d.w_v - 0.01))
        on_nub = in_band & (r < d.r_cap_in + d.w_v - 1e-3)
        nub_f = cf[on_nub[cf].any(axis=1) & in_band[cf].all(axis=1)]
        self.viewer.set_mesh("nubs", cv, nub_f, NUB_COLOR)
        self.viewer.configure("nubs", overlay=True)
        self.map.set_design(d)
        self.viewer.set_bed(d.p.bed_x, d.p.bed_y)
        self.apply_view_mode(fit=None)
        dt = time.perf_counter() - t0
        self._last_fit = (fit_gap, snap)
        self._write_back(d)
        self._show_info(d, [], d.warnings, fit_gap, snap)
        self.statusBar().showMessage(
            f"Generated in {dt * 1000:.0f} ms — "
            f"{sum(len(pt.faces) for pt in dparts):,} triangles", 6000)

    def _write_back(self, d):
        """Keep both ways of sizing in step, so switching between them changes nothing.

        Sized from contents: the resulting diameter and lengths go into the
        (greyed-out) explicit fields.  Explicit sizes: the contents fields show
        what actually fits inside.
        """
        p = self.params
        if d.auto:
            vals = {"maze_diameter": round(2 * d.r_maze, 2),
                    "body_length": round(d.H_body, 1), "cap_length": round(d.H_cap, 1)}
        else:
            bore = 2 * ((d.r_floor - d.w_v) if d.vase else d.r_cav)
            storage = d.z0 + d.H_ci - p.bottom_thickness
            vals = {"content_diameter": max(3.0, math.floor((bore - p.fit_slack) * 10) / 10),
                    "content_length": max(3.0, math.floor((storage - p.fit_slack) * 10) / 10)}
            p.preset = ""
        for key, v in vals.items():
            setattr(p, key, v)
            w = self.widgets.get(key)
            if w is not None:
                w.blockSignals(True)
                w.setValue(v)
                w.blockSignals(False)
        self._sync_enabled()

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
                if self.simple_mode and k not in SIMPLE_INFO:
                    continue
                html.append(f"<tr><td class='k'>{k}</td><td>{v}</td></tr>")
            html.append("</table>")
            if fit_gap is not None:
                if fit_gap > 0.5 * d.c:
                    html.append(f"<p class='ok'>✔ Fit check: the cap slides along the whole "
                                f"solution with {fit_gap:.2f} mm clearance.</p>")
                else:
                    html.append(f"<p class='e'>✘ Fit check failed (gap {fit_gap:.2f} mm).</p>")
                if d.lock_cols:
                    click = (f" Just before it comes to rest, one nub passes a small "
                             f"{d.detent_h:.2f} mm bump." if d.detent_h > 0 else "")
                    html.append(f"<p class='ok'>✔ Twist lock: push the cap down, then turn it "
                                f"{d.lock_way} (seen from its closed end) into its pocket.{click} "
                                f"Locked, it cannot be pulled off: turn it {d.unlock_way} first.</p>")
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
            if d.inlays:
                html.append(
                    "<p class='k'><b>Second-colour text:</b> the letters are separate pieces that "
                    "sit flush in the wall. Export the <b>3MF</b>: each lettered piece opens as one "
                    "object with two parts, and the “text” part is set to filament 2 "
                    "(Flash Studio and OrcaSlicer read that; in other slicers, set it "
                    "yourself). With STLs, load a piece and its “_text” file together as "
                    "one object with multiple parts.</p>")
            html.append(
                "<p class='k'><b>3MF in Flash Studio / OrcaSlicer:</b> use <b>File → Import</b> "
                "(Ctrl+I). Opening it as a project instead shows “loading geometry data "
                "only”. That notice is harmless: the file holds the shapes, and your "
                "printer and filament settings stay as they are.</p>")
        self.info.setHtml("".join(html))

    # ============================================================ view
    def apply_view_mode(self, fit=False):
        d = self.design
        if d is None:
            return
        mode = self.mode_combo.currentText()
        xray = self.xray_chk.isChecked()
        v = self.viewer
        parts = d.parts()
        pos, _ = d.plate_layout()
        phi, Z = self.cap_pose
        cap_alpha = 0.30 if (xray and mode in ("Assembled", "Exploded")) else 1.0
        cap_pieces = [pt.name for pt in parts if pt.frame == "cap"]
        models = {}
        for i, pt in enumerate(parts):
            is_body = pt.frame == "body"
            vis = True
            if mode == "Print layout":             # as exported: caps upside down, on the bed
                x, y = pos[i]
                m = (make_transform(offset=(x, y, 0)) if is_body
                     else make_transform(flip=True, offset=(x, y, pt.top)))
            elif mode == "Assembled":
                m = np.identity(4) if is_body else make_transform(rot_z=phi, offset=(0, 0, d.z0 + Z))
            elif mode == "Exploded":
                lift = d.H_body + 14.0 + (16.0 if (d.three and pt.name == "cap") else 0.0)
                m = np.identity(4) if is_body else make_transform(offset=(0, 0, lift))
            elif mode == "Body only":
                m, vis = np.identity(4), is_body
            else:                                  # Cap only: the cap pieces as they print
                vis = not is_body
                k = cap_pieces.index(pt.name) if not is_body else 0
                x = (k - (len(cap_pieces) - 1) / 2.0) * (2 * d.R_max + 8.0)
                m = make_transform(flip=True, offset=(x, 0, pt.top))
            for name in (pt.name, pt.name + "_text"):
                v.configure(name, model=m, visible=vis, alpha=1.0 if is_body else cap_alpha)
            models[pt.name] = (m, vis)
        m, vis = models["extension" if d.three else "cap"]
        v.configure("nubs", model=m, visible=vis)
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
            for name in ("cap", "cap_text", "extension", "nubs"):
                self.viewer.configure(name, model=m)

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
        self.params = self._fresh_params()
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

    def _export_base(self, d):
        base = self._safe_name()
        if d.vase and not base.endswith("_vase"):
            base += "_vase"       # make it obvious these are solid spiral-vase models
        return base

    def export_stl(self, kind):
        """kind: "all" (one STL per piece), "plate", or a piece name."""
        d = self.design
        if d is None:
            QMessageBox.warning(self, "Nothing to export", "The current parameters are not valid.")
            return
        parts = {pt.name: pt for pt in d.parts()}
        if kind not in ("all", "plate") and kind not in parts:
            QMessageBox.information(self, "No such piece",
                                    "This design has no extension - it is made of two pieces.")
            return
        base = self._export_base(d)
        suffix = "" if kind == "all" else "_" + kind
        default = os.path.join(self._last_dir(), base + suffix + ".stl")
        tags = ", ".join(f"_{n}.stl" for n in parts)
        if d.inlays:
            tags += ", and _text.stl for the second-colour letters"
        title = {"all": f"Export STLs (writes {tags})",
                 "plate": "Export print plate STL"}.get(kind, f"Export {kind} STL")
        path, _ = QFileDialog.getSaveFileName(self, title, default, "STL files (*.stl)")
        if not path:
            return
        if not path.lower().endswith(".stl"):
            path += ".stl"
        root = path[:-4]
        written = []
        try:
            if kind == "all":
                for tag in ("_body", "_cap", "_extension"):
                    if root.endswith(tag):
                        root = root[: -len(tag)]
                for name, pt in parts.items():
                    written += write_part_stls(f"{root}_{name}", pt, f"{base} {name}")
            elif kind == "plate":
                pv, pf = print_plate(d)
                write_stl(path, pv, pf, base + " plate")
                written = [path]
                text = print_plate_text(d)
                if text is not None:
                    write_stl(root + "_text.stl", *text, base + " plate text")
                    written.append(root + "_text.stl")
            else:
                written = write_part_stls(root, parts[kind], f"{base} {kind}")
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self._remember_dir(path)
        names = ", ".join(os.path.basename(w) for w in written)
        msg = f"Exported {names} to {os.path.dirname(path)}"
        if any(w.endswith("_text.stl") for w in written):
            msg += " — load each piece with its _text file as one object with multiple parts"
        self.statusBar().showMessage(msg, 12000)

    def export_3mf(self):
        """All pieces as separate objects in one 3MF, laid out on the printer bed."""
        d = self.design
        if d is None:
            QMessageBox.warning(self, "Nothing to export", "The current parameters are not valid.")
            return
        base = self._export_base(d)
        path, _ = QFileDialog.getSaveFileName(self, "Export 3MF (all parts on one plate)",
                                              os.path.join(self._last_dir(), base + ".3mf"),
                                              "3MF files (*.3mf)")
        if not path:
            return
        if not path.lower().endswith(".3mf"):
            path += ".3mf"
        objects, fits = plate_objects(d, base, (d.p.bed_x / 2.0, d.p.bed_y / 2.0))
        try:
            write_3mf(path, objects, base, colors=(CAP_COLOR, TEXT_COLOR))
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self._remember_dir(path)
        msg = f"Exported {os.path.basename(path)} ({len(objects)} parts) to {os.path.dirname(path)}"
        msg += " — in Flash Studio / OrcaSlicer load it with File → Import (Ctrl+I)"
        if d.inlays:
            msg += "; the “text” parts are set to filament 2"
        if not fits:
            msg += " — the parts don't all fit the bed at once, so arrange or split them in the slicer"
        self.statusBar().showMessage(msg, 12000)

    def _greeting(self) -> str:
        """First line of the cap text, if it is short enough to head the gift tag."""
        lines = [ln.strip() for ln in self.params.cap_text.splitlines() if ln.strip()]
        return lines[0] if lines and len(lines[0]) <= 32 else ""

    def export_gift_tag(self):
        """Printable quarter-fold card with the solution hidden inside."""
        d = self.design
        if d is None:
            return
        default = os.path.join(self._last_dir(), self._safe_name() + "_gift_tag.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "Export gift tag", default,
                                              "PDF document (*.pdf);;PNG image (*.png)")
        if not path:
            return
        if not path.lower().endswith((".pdf", ".png")):
            path += ".pdf"
        try:
            save_gift_tag(path, d, self.params.name, self._greeting())
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self._remember_dir(path)
        self.statusBar().showMessage(
            f"Saved gift tag to {path} — print it, fold the top half back, then fold in half", 12000)

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
        shown = d.map_window()[1] * d.P_th            # angle the map covers (a turn, or more)
        h = int((w - 170) * (d.H_body - d.z0) / (shown * d.r_maze)) + 340
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
        QPushButton#modeButton { border: 1px solid #3b4049; padding: 4px 10px; background: #2a2d33; }
        QPushButton#modeButton:checked { background: #3884d6; color: white; border-color: #3884d6; }
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
    valued = ("--screenshot", "--mode", "--export-test", "--pose", "--yaw", "--pitch", "--zoom",
              "--target")                      # developer options that take a value
    files = [a for i, a in enumerate(argv) if i > 0 and not a.startswith("--")
             and argv[i - 1] not in valued and os.path.isfile(a)]
    if files:
        win.load_file(files[0])

    # developer hook: --screenshot out.png [--mode N] [--section] [--pose t]
    if "--screenshot" in argv:
        out = argv[argv.index("--screenshot") + 1]
        if "--mode" in argv:
            win.mode_combo.setCurrentIndex(int(argv[argv.index("--mode") + 1]))
        if "--section" in argv:
            win.section_chk.setChecked(True)
        if "--opaque" in argv:
            win.xray_chk.setChecked(False)
        if "--detailed" in argv or "--simple" in argv:
            win.set_simple_mode("--simple" in argv, remember=False)
        if "--export-test" in argv:          # write every export, without dialogs
            out_dir = argv[argv.index("--export-test") + 1]
            d = win.design
            for pt in d.parts():
                write_part_stls(os.path.join(out_dir, f"t_{pt.name}"), pt, pt.name)
            write_3mf(os.path.join(out_dir, "t.3mf"), plate_objects(d, "t")[0],
                      colors=(CAP_COLOR, TEXT_COLOR))
            save_gift_tag(os.path.join(out_dir, "t_tag.pdf"), d, win.params.name, win._greeting())
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
