import numpy as np
import moderngl
from .base import BaseRenderer

VERT = """
#version 330
in vec2 in_vert;
uniform vec2 u_scale;
void main() {
    vec2 v = in_vert * u_scale;
    gl_Position = vec4(v, 0.0, 1.0);
}
"""

FRAG = """
#version 330
uniform vec3 u_color;
out vec4 f_color;
void main() {
    f_color = vec4(u_color, 1.0);
}
"""

class SpectrumRenderer(BaseRenderer):
    def __init__(self, ctx, radial=False):
        super().__init__(ctx)
        self.radial = radial
        self.prog = ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
        self.vbo = None
        self.vao = None
        self.n = 0

    def render(self, w, h, samples, spectrum, energy, flux, color, sensitivity):
        bins = 128
        spec = spectrum
        if len(spec) < bins:
            spec = np.pad(spec, (0, bins - len(spec)))
        else:
            spec = spec[:bins*4].reshape(bins, 4).mean(axis=1)
        spec = np.clip(spec, 0, None)
        spec /= (np.max(spec) + 1e-6)
        spec = spec ** (0.7)

        if not self.radial:
            xs = np.linspace(-0.95, 0.95, bins).astype("f4")
            verts = []
            for i, v in enumerate(spec):
                x = xs[i]
                y = v * sensitivity * 0.9
                verts += [
                    [x, -0.9],
                    [x, -0.9 + y],
                ]
            verts = np.array(verts, dtype="f4")
        else:
            verts = []
            for i, v in enumerate(spec):
                a = i / bins * 2 * np.pi
                r0 = 0.5
                r1 = r0 + v * 0.35 * sensitivity + energy * 0.1
                x0, y0 = np.cos(a) * r0, np.sin(a) * r0
                x1, y1 = np.cos(a) * r1, np.sin(a) * r1
                verts += [[x0, y0], [x1, y1]]
            verts = np.array(verts, dtype="f4")

        if self.vbo is None or self.n != len(verts):
            self.vbo = self.ctx.buffer(verts.tobytes())
            self.vao = self.ctx.simple_vertex_array(self.prog, self.vbo, "in_vert")
            self.n = len(verts)
        else:
            self.vbo.write(verts.tobytes())

        self.prog["u_scale"].value = (1.0, 1.0)
        self.prog["u_color"].value = color
        self.vao.render(moderngl.LINES)
