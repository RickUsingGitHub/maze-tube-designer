"""Lightweight OpenGL 3D viewer (QOpenGLWidget + PyOpenGL, GLSL 1.20)."""

from __future__ import annotations

import ctypes
import math

import numpy as np
from OpenGL import GL as gl
from PySide6.QtCore import QPointF, Qt
from PySide6.QtOpenGLWidgets import QOpenGLWidget

VERT_SRC = """
#version 120
attribute vec3 a_pos;
attribute vec3 a_nrm;
uniform mat4 u_model;
uniform mat4 u_view;
uniform mat4 u_proj;
varying vec3 v_nrm;
varying vec3 v_pos;
varying vec3 v_world;
void main() {
    vec4 w = u_model * vec4(a_pos, 1.0);
    v_world = w.xyz;
    vec4 vp = u_view * w;
    v_pos = vp.xyz;
    v_nrm = mat3(u_view) * mat3(u_model) * a_nrm;
    gl_Position = u_proj * vp;
}
"""

FRAG_SRC = """
#version 120
uniform vec3 u_color;
uniform float u_alpha;
uniform int u_mode;          // 0 unlit, 1 lit, 2 background gradient
uniform int u_clip;
uniform vec4 u_plane;
uniform vec3 u_bg_top;
uniform vec3 u_bg_bot;
uniform float u_vh;
varying vec3 v_nrm;
varying vec3 v_pos;
varying vec3 v_world;
void main() {
    if (u_mode == 2) {
        float t = clamp(gl_FragCoord.y / u_vh, 0.0, 1.0);
        gl_FragColor = vec4(mix(u_bg_bot, u_bg_top, t), 1.0);
        return;
    }
    if (u_clip == 1 && dot(vec4(v_world, 1.0), u_plane) > 0.0) discard;
    if (u_mode == 0) { gl_FragColor = vec4(u_color, u_alpha); return; }
    vec3 n = normalize(v_nrm);
    vec3 col = u_color;
    if (!gl_FrontFacing) {            // inside of a sectioned part
        n = -n;
        col = col * 0.55;
    }
    vec3 vdir = normalize(-v_pos);
    vec3 l1 = normalize(vec3(0.45, 0.75, 0.55));
    vec3 l2 = normalize(vec3(-0.7, -0.25, 0.45));
    float dif = max(dot(n, l1), 0.0) * 0.72 + max(dot(n, l2), 0.0) * 0.28;
    vec3 h = normalize(l1 + vdir);
    float spec = pow(max(dot(n, h), 0.0), 40.0) * 0.22;
    float rim = pow(1.0 - max(dot(n, vdir), 0.0), 3.0) * 0.12;
    vec3 c = col * (0.24 + dif) + vec3(spec + rim);
    gl_FragColor = vec4(c, u_alpha);
}
"""


def _perspective(fovy_deg, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fovy_deg) / 2.0)
    m = np.zeros((4, 4))
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2.0 * far * near / (near - far)
    m[3, 2] = -1.0
    return m


def _look_at(eye, target, up):
    f = target - eye
    f = f / np.linalg.norm(f)
    s = np.cross(f, up)
    s = s / np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.identity(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[0, 3], m[1, 3], m[2, 3] = -s @ eye, -u @ eye, f @ eye
    return m


def make_transform(rot_z=0.0, flip=False, offset=(0.0, 0.0, 0.0)):
    """4x4 model matrix: translate(offset) * rotZ(rot_z) * flipX."""
    m = np.identity(4)
    if flip:
        m = np.diag([1.0, -1.0, -1.0, 1.0]) @ m
    c, s = math.cos(rot_z), math.sin(rot_z)
    rz = np.identity(4)
    rz[0, 0], rz[0, 1], rz[1, 0], rz[1, 1] = c, -s, s, c
    m = rz @ m
    t = np.identity(4)
    t[:3, 3] = offset
    return t @ m


CREASE_COS = math.cos(math.radians(21.0))        # edges sharper than this are drawn crisp
CREASE_COS_FINE = math.cos(math.radians(42.0))   # ... but fine relief (lettering) may curve more
FINE_AREA = 0.15                                 # mm²: triangles smaller than this are "fine"


class _Mesh:
    def __init__(self, verts, faces, color):
        tri = verts[faces].astype(np.float32)                     # (m,3,3)
        area_n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])    # length = 2 x area
        ln = np.linalg.norm(area_n, axis=1, keepdims=True)
        area = 0.5 * ln[:, 0]
        ln[ln == 0] = 1
        n = (area_n / ln).astype(np.float32)
        # Smooth shading where the surface curves gently (the tube wall, rounded
        # lettering); flat shading at real edges (maze walls, chamfers).  A vertex
        # is smooth only if every face round it is close to the averaged normal,
        # so a sharp edge never bleeds a tilted normal into the faces beside it.
        nv = len(verts)
        flat_idx = faces.ravel()
        vn = np.stack([np.bincount(flat_idx, weights=np.repeat(area_n[:, k], 3), minlength=nv)
                       for k in range(3)], 1)
        vl = np.linalg.norm(vn, axis=1, keepdims=True)
        vl[vl == 0] = 1
        vn = (vn / vl).astype(np.float32)
        coarse = np.bincount(flat_idx, weights=np.repeat(area > FINE_AREA, 3), minlength=nv) > 0
        limit = np.where(coarse, CREASE_COS, CREASE_COS_FINE)
        corner_vn = vn[faces]                                     # (m,3,3)
        too_sharp = np.einsum("mij,mj->mi", corner_vn, n) < limit[faces]
        creased = np.bincount(flat_idx, weights=too_sharp.ravel(), minlength=nv) > 0
        smooth = ~creased[faces]
        data = np.empty((len(faces), 3, 6), dtype=np.float32)
        data[:, :, :3] = tri
        data[:, :, 3:] = np.where(smooth[:, :, None], corner_vn, n[:, None, :])
        self.data = data.reshape(-1, 6)
        self.count = len(self.data)
        self.lo = verts.min(axis=0)
        self.hi = verts.max(axis=0)
        self.color = color
        self.alpha = 1.0
        self.visible = True
        self.overlay = False      # draw with polygon offset on top of coplanar faces
        self.model = np.identity(4)
        self.vbo = None


class Viewer3D(QOpenGLWidget):
    BG_TOP = (0.20, 0.23, 0.28)
    BG_BOT = (0.08, 0.09, 0.11)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.meshes: dict[str, _Mesh] = {}
        self._stale_vbos: list[int] = []
        self.prog = None
        self.grid_vbo = None
        self.grid_count = 0
        self.bed = (256.0, 256.0)     # printer bed drawn under the parts (mm)
        self._grid_bed = None         # bed size the grid buffer was built for
        self.yaw = -60.0
        self.pitch = 22.0
        self.dist = 200.0
        self.target = np.array([0.0, 0.0, 30.0])
        self.section = False
        self._last = None
        self._gl_error = None
        self.setMinimumSize(320, 240)
        self.setFocusPolicy(Qt.StrongFocus)

    # ------------------------------------------------------------ scene API
    def set_mesh(self, name, verts, faces, color):
        old = self.meshes.get(name)
        m = _Mesh(verts, faces, color)
        if old is not None:
            m.model, m.alpha, m.visible, m.overlay = old.model, old.alpha, old.visible, old.overlay
            if old.vbo is not None:
                self._stale_vbos.append(old.vbo)
        self.meshes[name] = m
        self.update()

    def remove_mesh(self, name):
        old = self.meshes.pop(name, None)
        if old is not None and old.vbo is not None:
            self._stale_vbos.append(old.vbo)
        self.update()

    def configure(self, name, model=None, visible=None, alpha=None, overlay=None, color=None):
        m = self.meshes.get(name)
        if m is None:
            return
        if model is not None:
            m.model = model
        if visible is not None:
            m.visible = visible
        if alpha is not None:
            m.alpha = alpha
        if overlay is not None:
            m.overlay = overlay
        if color is not None:
            m.color = color
        self.update()

    def set_section(self, on: bool):
        self.section = on
        self.update()

    def scene_bounds(self):
        lo = np.array([np.inf] * 3)
        hi = -lo
        for m in self.meshes.values():
            if not m.visible:
                continue
            corners = np.array([[x, y, z] for x in (m.lo[0], m.hi[0])
                                for y in (m.lo[1], m.hi[1]) for z in (m.lo[2], m.hi[2])])
            pts = corners @ m.model[:3, :3].T + m.model[:3, 3]
            lo = np.minimum(lo, pts.min(0))
            hi = np.maximum(hi, pts.max(0))
        if not np.isfinite(lo).all():
            return np.zeros(3), np.ones(3) * 50
        return lo, hi

    def fit_view(self, reset_angles=False):
        lo, hi = self.scene_bounds()
        self.target = (lo + hi) / 2.0
        radius = max(np.linalg.norm(hi - lo) / 2.0, 10.0)
        aspect = max(self.width(), 1) / max(self.height(), 1)
        fov = math.radians(30.0) * min(1.0, aspect)
        self.dist = radius / math.sin(fov / 2.0) * 1.05
        if reset_angles:
            self.yaw, self.pitch = -60.0, 22.0
        self.update()

    # ---------------------------------------------------------------- GL
    def initializeGL(self):
        for m in self.meshes.values():
            m.vbo = None
        self._stale_vbos.clear()
        self.grid_vbo = None
        self._grid_bed = None
        try:
            self.prog = self._build_program()
            self._quad_vbo = gl.glGenBuffers(1)
            quad = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0],
                             [-1, -1, 0], [1, 1, 0], [-1, 1, 0]], dtype=np.float32)
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._quad_vbo)
            gl.glBufferData(gl.GL_ARRAY_BUFFER, quad.nbytes, quad, gl.GL_STATIC_DRAW)
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
            self._gl_error = None
        except Exception as exc:  # pragma: no cover - driver specific
            self.prog = None
            self._gl_error = str(exc)

    def _build_program(self):
        def compile_shader(src, kind):
            sh = gl.glCreateShader(kind)
            gl.glShaderSource(sh, src)
            gl.glCompileShader(sh)
            if not gl.glGetShaderiv(sh, gl.GL_COMPILE_STATUS):
                raise RuntimeError(gl.glGetShaderInfoLog(sh).decode(errors="replace"))
            return sh

        vs = compile_shader(VERT_SRC, gl.GL_VERTEX_SHADER)
        fs = compile_shader(FRAG_SRC, gl.GL_FRAGMENT_SHADER)
        prog = gl.glCreateProgram()
        gl.glAttachShader(prog, vs)
        gl.glAttachShader(prog, fs)
        gl.glBindAttribLocation(prog, 0, "a_pos")
        gl.glBindAttribLocation(prog, 1, "a_nrm")
        gl.glLinkProgram(prog)
        if not gl.glGetProgramiv(prog, gl.GL_LINK_STATUS):
            raise RuntimeError(gl.glGetProgramInfoLog(prog).decode(errors="replace"))
        gl.glDeleteShader(vs)
        gl.glDeleteShader(fs)
        self._loc = {n: gl.glGetUniformLocation(prog, n) for n in (
            "u_model", "u_view", "u_proj", "u_color", "u_alpha", "u_mode", "u_clip",
            "u_plane", "u_bg_top", "u_bg_bot", "u_vh")}
        return prog

    def _upload(self, m: _Mesh):
        m.vbo = gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, m.vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, m.data.nbytes, m.data, gl.GL_STATIC_DRAW)

    def set_bed(self, width: float, depth: float):
        """Size of the printer bed shown under the parts."""
        bed = (float(width), float(depth))
        if bed != self.bed:
            self.bed = bed
            self.update()

    def _ensure_grid(self):
        """The printer bed: a 10 mm grid of its real size, with an outline.

        It depends only on the bed size - never on the parts - so it stays
        put while the cap moves during the solution animation.
        """
        if self.grid_vbo is not None and self._grid_bed == self.bed:
            return
        if self.grid_vbo is not None:
            gl.glDeleteBuffers(1, [self.grid_vbo])
        bx, by = self.bed
        x0, y0, x1, y1 = -bx / 2.0, -by / 2.0, bx / 2.0, by / 2.0
        lines = []
        for x in x0 + np.arange(0.0, bx + 1e-6, 10.0):
            lines += [(x, y0, 0), (x, y1, 0)]
        for y in y0 + np.arange(0.0, by + 1e-6, 10.0):
            lines += [(x0, y, 0), (x1, y, 0)]
        self.grid_count = len(lines)
        lines += [(x0, y0, 0), (x1, y0, 0), (x1, y0, 0), (x1, y1, 0),      # bed outline
                  (x1, y1, 0), (x0, y1, 0), (x0, y1, 0), (x0, y0, 0)]
        arr = np.array(lines, dtype=np.float32)
        self.grid_vbo = gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self.grid_vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, arr.nbytes, arr, gl.GL_STATIC_DRAW)
        self._grid_bed = self.bed

    def _mat(self, name, m):
        gl.glUniformMatrix4fv(self._loc[name], 1, gl.GL_TRUE, m.astype(np.float32))

    def view_matrix(self):
        y, p = math.radians(self.yaw), math.radians(self.pitch)
        off = np.array([math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p)])
        eye = self.target + off * self.dist
        return _look_at(eye, self.target, np.array([0.0, 0.0, 1.0]))

    def paintGL(self):
        if self.prog is None:
            gl.glClearColor(0.1, 0.1, 0.1, 1)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            return
        try:
            self._paint()
        except Exception as exc:  # pragma: no cover
            self._gl_error = str(exc)

    def _paint(self):
        if self._stale_vbos:
            gl.glDeleteBuffers(len(self._stale_vbos), self._stale_vbos)
            self._stale_vbos.clear()
        w, h = max(self.width(), 1), max(self.height(), 1)
        dpr = self.devicePixelRatioF()
        gl.glClearColor(*self.BG_BOT, 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        gl.glUseProgram(self.prog)
        L = self._loc

        # background gradient
        gl.glDisable(gl.GL_DEPTH_TEST)
        gl.glDisable(gl.GL_CULL_FACE)
        eye4 = np.identity(4)
        self._mat("u_model", eye4)
        self._mat("u_view", eye4)
        self._mat("u_proj", eye4)
        gl.glUniform1i(L["u_mode"], 2)
        gl.glUniform1i(L["u_clip"], 0)
        gl.glUniform3f(L["u_bg_top"], *self.BG_TOP)
        gl.glUniform3f(L["u_bg_bot"], *self.BG_BOT)
        gl.glUniform1f(L["u_vh"], h * dpr)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._quad_vbo)
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, 12, ctypes.c_void_p(0))
        gl.glDisableVertexAttribArray(1)
        gl.glVertexAttrib3f(1, 0.0, 0.0, 1.0)
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)

        gl.glEnable(gl.GL_DEPTH_TEST)
        gl.glDepthFunc(gl.GL_LEQUAL)
        view = self.view_matrix()
        lo, hi = self.scene_bounds()
        far = self.dist + np.linalg.norm(hi - lo) * 4 + 500 + math.hypot(*self.bed)
        proj = _perspective(30.0, w / h, max(self.dist * 0.02, 0.5), far)
        self._mat("u_view", view)
        self._mat("u_proj", proj)
        gl.glUniform4f(L["u_plane"], 0.0, -1.0, 0.0, 0.0)   # cut away the front (y < 0)

        # printer bed: grid, then a brighter outline
        self._ensure_grid()
        self._mat("u_model", eye4)
        gl.glUniform1i(L["u_mode"], 0)
        gl.glUniform1f(L["u_alpha"], 1.0)
        gl.glUniform3f(L["u_color"], 0.27, 0.30, 0.35)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self.grid_vbo)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, 12, ctypes.c_void_p(0))
        gl.glDrawArrays(gl.GL_LINES, 0, self.grid_count)
        gl.glUniform3f(L["u_color"], 0.50, 0.56, 0.64)
        gl.glDrawArrays(gl.GL_LINES, self.grid_count, 8)

        # meshes
        gl.glUniform1i(L["u_mode"], 1)
        gl.glUniform1i(L["u_clip"], 1 if self.section else 0)
        gl.glEnableVertexAttribArray(1)
        opaque = [m for m in self.meshes.values() if m.visible and m.alpha >= 0.999]
        clear = [m for m in self.meshes.values() if m.visible and m.alpha < 0.999]
        gl.glDisable(gl.GL_BLEND)
        for m in opaque:
            if self.section:
                gl.glDisable(gl.GL_CULL_FACE)
            else:
                gl.glEnable(gl.GL_CULL_FACE)
                gl.glCullFace(gl.GL_BACK)
            if m.overlay:
                gl.glEnable(gl.GL_POLYGON_OFFSET_FILL)
                gl.glPolygonOffset(-1.0, -2.0)
            self._draw(m)
            if m.overlay:
                gl.glDisable(gl.GL_POLYGON_OFFSET_FILL)
        if clear:
            gl.glEnable(gl.GL_BLEND)
            gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
            gl.glDepthMask(gl.GL_FALSE)
            gl.glEnable(gl.GL_CULL_FACE)
            for face in (gl.GL_FRONT, gl.GL_BACK):
                gl.glCullFace(face)
                for m in clear:
                    self._draw(m)
            gl.glDepthMask(gl.GL_TRUE)
            gl.glDisable(gl.GL_BLEND)
        gl.glDisable(gl.GL_CULL_FACE)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
        gl.glUseProgram(0)

    def _draw(self, m: _Mesh):
        if m.vbo is None:
            self._upload(m)
        L = self._loc
        self._mat("u_model", m.model)
        gl.glUniform3f(L["u_color"], *m.color)
        gl.glUniform1f(L["u_alpha"], m.alpha)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, m.vbo)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, 24, ctypes.c_void_p(0))
        gl.glVertexAttribPointer(1, 3, gl.GL_FLOAT, gl.GL_FALSE, 24, ctypes.c_void_p(12))
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, m.count)

    # ------------------------------------------------------------- mouse
    def mousePressEvent(self, ev):
        self._last = ev.position()

    def mouseMoveEvent(self, ev):
        if self._last is None:
            self._last = ev.position()
            return
        d: QPointF = ev.position() - self._last
        self._last = ev.position()
        if ev.buttons() & Qt.LeftButton and not (ev.modifiers() & Qt.ShiftModifier):
            self.yaw -= d.x() * 0.4
            self.pitch = max(-89.0, min(89.0, self.pitch + d.y() * 0.4))
        elif ev.buttons() & (Qt.RightButton | Qt.MiddleButton | Qt.LeftButton):
            v = self.view_matrix()
            right, up = v[0, :3], v[1, :3]
            k = self.dist * 0.0018
            self.target = self.target - right * d.x() * k + up * d.y() * k
        self.update()

    def mouseReleaseEvent(self, ev):
        self._last = None

    def mouseDoubleClickEvent(self, ev):
        self.fit_view(reset_angles=True)

    def wheelEvent(self, ev):
        steps = ev.angleDelta().y() / 120.0
        self.dist = max(5.0, min(5000.0, self.dist * (0.88 ** steps)))
        self.update()

    def keyPressEvent(self, ev):
        k = ev.key()
        if k == Qt.Key_Left:
            self.yaw += 5
        elif k == Qt.Key_Right:
            self.yaw -= 5
        elif k == Qt.Key_Up:
            self.pitch = min(89.0, self.pitch + 5)
        elif k == Qt.Key_Down:
            self.pitch = max(-89.0, self.pitch - 5)
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.dist *= 0.9
        elif k == Qt.Key_Minus:
            self.dist /= 0.9
        elif k in (Qt.Key_Home, Qt.Key_F):
            self.fit_view(reset_angles=True)
            return
        else:
            super().keyPressEvent(ev)
            return
        self.update()
