"""3MF export: every part as its own object, laid out on one plate.

A 3MF file is a zip archive holding an XML model.  Unlike STL it keeps the
parts as separate named objects (so the slicer can move or colour them
individually), stores indexed vertices, and records the unit (millimetres).

A piece with flush second-colour lettering is written as one object made of
two components - the piece and its letters.  Slicers open that as a single
object with two parts, so the letters can be given their own filament.

For the Bambu Studio / OrcaSlicer family (Flash Studio, Orca-Flashforge, ...)
the file also carries ``Metadata/model_settings.config``, the list those
slicers read part names and filament numbers from: the letters arrive already
set to filament 2.  It holds no printer or print settings, so nothing in the
slicer is changed by opening the file.
"""

from __future__ import annotations

import zipfile

import numpy as np

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
    ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
    ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
    ' <Default Extension="config" ContentType="text/xml"/>\n'
    '</Types>\n')

_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
    ' <Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
    '</Relationships>\n')

_NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _hex(rgb) -> str:
    return "#" + "".join(f"{int(round(max(0.0, min(1.0, c)) * 255)):02X}" for c in rgb)


def _object_xml(obj_id: int, name: str, verts: np.ndarray, faces: np.ndarray,
                material: str = "") -> str:
    v = verts.astype(np.float64)
    # numpy string formatting is much faster than a Python loop for big meshes
    vs = np.char.add(np.char.add('<vertex x="', np.char.mod("%.5f", v[:, 0])), '" y="')
    vs = np.char.add(np.char.add(vs, np.char.mod("%.5f", v[:, 1])), '" z="')
    vs = np.char.add(np.char.add(vs, np.char.mod("%.5f", v[:, 2])), '"/>')
    ts = np.char.add(np.char.add('<triangle v1="', np.char.mod("%d", faces[:, 0])), '" v2="')
    ts = np.char.add(np.char.add(ts, np.char.mod("%d", faces[:, 1])), '" v3="')
    ts = np.char.add(np.char.add(ts, np.char.mod("%d", faces[:, 2])), '"/>')
    return (f' <object id="{obj_id}" type="model" name="{_xml_escape(name)}"{material}>\n'
            f'  <mesh>\n   <vertices>\n' + "\n".join(vs.tolist()) + '\n   </vertices>\n'
            f'   <triangles>\n' + "\n".join(ts.tolist()) + '\n   </triangles>\n  </mesh>\n'
            f' </object>\n')


def write_3mf(path: str, objects, title: str = "Maze tube",
              colors=((0.24, 0.58, 0.88), (0.96, 0.95, 0.90))):
    """Write a 3MF file.

    objects: list of (name, verts (V,3), faces (F,3), (x, y, z) position) with an
    optional fifth item: a list of (name, verts, faces) meshes that belong to
    the object and are printed in a second colour (flush lettering).
    Vertices are stored as given; the position goes into the build transform,
    so each part sits at its place on the plate when the file is opened.
    ``colors``: display colours (r, g, b in 0..1) of the pieces and of the lettering.
    """
    two_colour = any(len(obj) > 4 and obj[4] for obj in objects)
    resources, build, next_id = [], [], 1
    settings = []                       # per-object part list for Orca-family slicers
    mat = ["", ""]
    if two_colour:
        resources.append(
            f' <basematerials id="{next_id}">\n'
            f'  <base name="Tube" displaycolor="{_hex(colors[0])}"/>\n'
            f'  <base name="Lettering" displaycolor="{_hex(colors[1])}"/>\n'
            f' </basematerials>\n')
        mat = [f' pid="{next_id}" pindex="0"', f' pid="{next_id}" pindex="1"']
        next_id += 1
    for obj in objects:
        name, verts, faces, pos = obj[:4]
        extras = obj[4] if len(obj) > 4 and obj[4] else []
        ids = []
        for k, (nm, v, f) in enumerate([(name, verts, faces)] + list(extras)):
            resources.append(_object_xml(next_id, nm, np.asarray(v), np.asarray(f),
                                         mat[1 if k else 0]))
            ids.append(next_id)
            next_id += 1
        item = ids[0]
        if extras:                      # one object with the piece and its letters as parts
            comps = "".join(f'   <component objectid="{i}"/>\n' for i in ids)
            resources.append(f' <object id="{next_id}" type="model" name="{_xml_escape(name)}">\n'
                             f'  <components>\n{comps}  </components>\n </object>\n')
            item = next_id
            next_id += 1
            parts = "".join(
                f'  <part id="{i}" subtype="normal_part">\n'
                f'   <metadata key="name" value="{_xml_escape(nm)}"/>\n'
                f'   <metadata key="extruder" value="{2 if k else 1}"/>\n'
                f'  </part>\n'
                for k, (i, nm) in enumerate(zip(ids, [name] + [e[0] for e in extras])))
            settings.append(f' <object id="{item}">\n'
                            f'  <metadata key="name" value="{_xml_escape(name)}"/>\n'
                            f'  <metadata key="extruder" value="1"/>\n{parts} </object>\n')
        x, y, z = (float(c) for c in pos)
        build.append(f'  <item objectid="{item}" '
                     f'transform="1 0 0 0 1 0 0 0 1 {x:.4f} {y:.4f} {z:.4f}"/>\n')
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
        if settings:
            zf.writestr("Metadata/model_settings.config",
                        '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n'
                        + "".join(settings) + '</config>\n')


def read_3mf(path: str):
    """Read back the meshes of a 3MF written by :func:`write_3mf` (used by the tests).

    Returns a list of (name, verts, faces, (x, y, z)), one per mesh in build
    order; an object made of components gives one entry per component.
    """
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("3D/3dmodel.model"))
    assert root.get("unit") == "millimeter"
    objs = {}
    for obj in root.find("m:resources", _NS).findall("m:object", _NS):
        mesh = obj.find("m:mesh", _NS)
        if mesh is None:
            comps = obj.find("m:components", _NS).findall("m:component", _NS)
            objs[obj.get("id")] = [m for c in comps for m in objs[c.get("objectid")]]
            continue
        v = np.array([[float(e.get(a)) for a in "xyz"]
                      for e in mesh.find("m:vertices", _NS).findall("m:vertex", _NS)])
        f = np.array([[int(e.get(a)) for a in ("v1", "v2", "v3")]
                      for e in mesh.find("m:triangles", _NS).findall("m:triangle", _NS)])
        objs[obj.get("id")] = [(obj.get("name"), v, f)]
    out = []
    for item in root.find("m:build", _NS).findall("m:item", _NS):
        t = [float(x) for x in item.get("transform").split()]
        for name, v, f in objs[item.get("objectid")]:
            out.append((name, v, f, tuple(t[9:12])))
    return out
