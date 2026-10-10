"""Design parameters for a maze tube, with JSON persistence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields

FILE_FORMAT = "mazetube"
FILE_VERSION = 1


@dataclass
class TubeParams:
    name: str = "maze_tube"

    # --- contents & printer (what the tube is sized for) -------------------
    auto_size: bool = True        # derive diameter and lengths from the contents below
    preset: str = ""              # name of the chosen contents preset ("" = custom)
    content_diameter: float = 30.0  # widest part of what goes inside (mm)
    content_length: float = 60.0    # length of what goes inside (mm)
    fit_slack: float = 2.0        # spare room around / above the contents (mm)
    printer_height: float = 250.0   # tallest part the printer can make (mm)
    bed_x: float = 256.0          # printer bed width (mm)
    bed_y: float = 256.0          # printer bed depth (mm)
    pieces: int = 0               # 0 = automatic, 2 = body + cap, 3 = body + extension + cap
    joint_length: float = 10.0    # overlap of the glued extension/cap joint (mm)

    # --- text ----------------------------------------------------------------
    cap_text: str = ""            # round the cap; may have several lines, long text spirals
    base_text: str = ""           # round the body's grip band, likewise
    text_height: float = 6.0      # height of capital letters (mm)
    text_depth: float = 0.6       # how far the text stands out / is cut in (mm)
    text_engrave: bool = False    # False = raised (embossed), True = cut in (engraved)

    # --- maze ------------------------------------------------------------
    rows: int = 0                 # maze rows along the tube axis (0 = auto from maze_length)
    maze_length: float = 100.0    # longest maze section allowed when rows = auto (mm)
    columns: int = 0              # total columns around (0 = automatic)
    nubs: int = 3                 # number of nubs (maze repeats per nub)
    path_width: float = 4.0       # groove width (mm)
    wall_width: float = 2.0       # ridge width between grooves (mm)
    algorithm: str = "backtracker"
    seed: int = 2026
    horizontal_bias: float = 1.0  # >1 favours sideways corridors
    loops: float = 0.0            # % of extra walls removed (adds loops)
    longest_path: bool = True     # place home/exit for the longest solution
    notch_depth: float = 1.5      # extra drop at the home position (0 = off)
    detent_level: int = 1         # click at the closed position: 0 off, 1 light, 2 medium, 3 firm
    detent: float = 0.0           # its interference in mm, set by hand (0 = sized from the level)

    # --- body (inner tube) ------------------------------------------------
    maze_diameter: float = 40.0   # diameter over the maze ridges (mm)
    groove_depth: float = 1.8     # depth of the maze grooves (mm)
    core_wall: float = 1.6        # wall under the groove floor (mm)
    base_height: float = 8.0      # grip band below the maze - minimum plain section (mm)
    bottom_thickness: float = 2.0
    body_length: float = 0.0      # total body length (0 = grip band + maze only)
    plain_wall: float = 2.4       # wall of the plain section below the maze (0 = same bore)

    # --- cap (outer tube) ---------------------------------------------------
    cap_wall: float = 2.0
    cap_top: float = 2.0
    clearance: float = 0.3        # fit clearance, radial and sideways (mm)
    cap_length: float = 0.0       # total cap length (0 = just covers the maze)

    # --- finishing / printing ---------------------------------------------
    chamfer_ceilings: bool = True  # 45 deg groove ceilings + nub tops (no overhangs)
    edge_chamfer: float = 0.6
    grip_flutes: int = 0          # number of grip flutes (0 = smooth)
    grip_depth: float = 0.6
    resolution: float = 0.8       # max facet length around the circumference (mm)
    vase_mode: bool = False       # export solid models for spiral vase printing
    vase_wall: float = 0.6        # slicer outer-wall line width used in vase mode (mm)

    # ---------------------------------------------------------------- utils
    def copy(self) -> "TubeParams":
        return TubeParams(**asdict(self))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TubeParams":
        p = cls()
        types = {f.name: f.type for f in fields(cls)}
        for k, v in d.items():
            if k not in types:
                continue
            cur = getattr(p, k)
            try:
                if isinstance(cur, bool):
                    v = bool(v)
                elif isinstance(cur, int):
                    v = int(v)
                elif isinstance(cur, float):
                    v = float(v)
                else:
                    v = str(v)
            except (TypeError, ValueError):
                continue
            setattr(p, k, v)
        if "detent_level" not in d:
            # older designs stored a per-nub interference that locked far too hard;
            # keep only whether they had a click at all, at the gentle setting
            p.detent_level = 0 if float(d.get("detent", 0.3) or 0.0) < 0.05 else 1
            p.detent = 0.0
        if "auto_size" not in d:
            # designs saved before contents-driven sizing carry explicit dimensions
            p.auto_size = False
            if "rows" not in d:
                p.rows = 8
        return p


def save_design(path: str, params: TubeParams, maze=None, maze_key=None):
    data = {
        "format": FILE_FORMAT,
        "version": FILE_VERSION,
        "params": params.to_dict(),
    }
    if maze is not None:
        data["maze"] = maze.to_dict()
        data["maze_key"] = list(maze_key) if maze_key is not None else None
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def load_design(path: str):
    """Return (params, maze_or_None, maze_key_or_None)."""
    from .maze import Maze

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("format") != FILE_FORMAT:
        raise ValueError("Not a maze tube design file")
    params = TubeParams.from_dict(data.get("params", {}))
    maze = key = None
    if data.get("maze"):
        try:
            maze = Maze.from_dict(data["maze"])
            key = tuple(data["maze_key"]) if data.get("maze_key") else None
        except Exception:
            maze = key = None
    return params, maze, key
