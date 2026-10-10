"""3MF export: every part as its own object, laid out on one plate.

A 3MF file is a zip archive holding an XML model.  Unlike STL it keeps the
parts as separate named objects (so the slicer can move or colour them
individually), stores indexed vertices, and records the unit (millimetres).
"""

from __future__ import annotations

import zipfile

import numpy as np

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
    ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
    ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
    '</Types>\n')

_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
    ' <Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
    '</Relationships>\n')


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _object_xml(obj_id: int, name: str, verts: np.ndarray, faces: np.ndarray) -> str:
    v = verts.astype(np.float64)
    # numpy string formatting is much faster than a Python loop for big meshes
    vs = np.char.add(np.char.add('<vertex x="', np.char.mod("%.5f", v[:, 0])), '" y="')
    vs = np.char.add(np.char.add(vs, np.char.mod("%.5f", v[:, 1])), '" z="')
    vs = np.char.add(np.char.add(vs, np.char.mod("%.5f", v[:, 2])), '"/>')
    ts = np.char.add(np.char.add('<triangle v1="', np.char.mod("%d", faces[:, 0])), '" v2="')
    ts = np.char.add(np.char.add(ts, np.char.mod("%d", faces[:, 1])), '" v3="')
    ts = np.char.add(np.char.add(ts, np.char.mod("%d", faces[:, 2])), '"/>')
    return (f' <object id="{obj_id}" type="model" name="{_xml_escape(name)}">\n'
            f'  <mesh>\n   <vertices>\n' + "\n".join(vs.tolist()) + '\n   </vertices>\n'
            f'   <triangles>\n' + "\n".join(ts.tolist()) + '\n   </triangles>\n  </mesh>\n'
            f' </object>\n')


def write_3mf(path: str, objects, title: str = "Maze tube"):
    """Write a 3MF file.

    objects: list of (name, verts (V,3), faces (F,3), (x, y, z) position).
    Vertices are stored as given; the position goes into the build transform,
    so each part sits at its place on the plate when the file is opened.
    """
    resources, build = [], []
    for i, (name, verts, faces, pos) in enumerate(objects, start=1):
        resources.append(_object_xml(i, name, np.asarray(verts), np.asarray(faces)))
        x, y, z = (float(c) for c in pos)
        build.append(f'  <item objectid="{i}" transform="1 0 0 0 1 0 0 0 1 {x:.4f} {y:.4f} {z:.4f}"/>\n')
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
        f' <metadata name="Title">{_xml_escape(title)}</metadata>\n'
        ' <metadata name="Application">Maze Tube Designer</metadata>\n'
        ' <resources>\n' + "".join(resources) + ' </resources>\n'
        ' <build>\n' + "".join(build) + ' </build>\n'
        '</model>\n')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("3D/3dmodel.model", model)


def read_3mf(path: str):
    """Read back the meshes of a 3MF written by :func:`write_3mf` (used by the tests).

    Returns a list of (name, verts, faces, (x, y, z)).
    """
    import xml.etree.ElementTree as ET

    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("3D/3dmodel.model"))
    assert root.get("unit") == "millimeter"
    objs = {}
    for obj in root.find("m:resources", ns).findall("m:object", ns):
        mesh = obj.find("m:mesh", ns)
        v = np.array([[float(e.get(a)) for a in "xyz"]
                      for e in mesh.find("m:vertices", ns).findall("m:vertex", ns)])
        f = np.array([[int(e.get(a)) for a in ("v1", "v2", "v3")]
                      for e in mesh.find("m:triangles", ns).findall("m:triangle", ns)])
        objs[obj.get("id")] = (obj.get("name"), v, f)
    out = []
    for item in root.find("m:build", ns).findall("m:item", ns):
        t = [float(x) for x in item.get("transform").split()]
        name, v, f = objs[item.get("objectid")]
        out.append((name, v, f, tuple(t[9:12])))
    return out
