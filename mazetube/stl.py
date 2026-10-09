"""Binary STL export."""

from __future__ import annotations

import numpy as np


def face_normals(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    v0, v1, v2 = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    n = np.cross(v1 - v0, v2 - v0)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    return n / ln


def write_stl(path: str, verts: np.ndarray, faces: np.ndarray, name: str = "maze_tube"):
    """Write a binary STL file (units: millimetres)."""
    tri = verts[faces].astype(np.float32)
    rec = np.zeros(len(faces), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"] = face_normals(verts, faces).astype(np.float32)
    rec["v"] = tri
    header = f"{name} - Maze Tube Designer (mm)".encode("ascii", "replace")[:80]
    with open(path, "wb") as fh:
        fh.write(header.ljust(80, b" "))
        fh.write(np.uint32(len(faces)).tobytes())
        fh.write(rec.tobytes())
