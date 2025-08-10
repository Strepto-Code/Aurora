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

CIRC_VERT = """
#version 330
in vec2 in_vert;
uniform float u_radius;
void main() {
    float angle = (in_vert.x + 1.0) * 3.14159265; // map -1..1 to 0..2pi
    float r = u_radius + in_vert.y * 0.5;
    vec2 pos = vec2(cos(angle), sin(angle)) * r;
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""

class WaveformRenderer(BaseRenderer):
    def __init__(self, ctx, circular=False):
        super().__init__(ctx)
        self.circular = circular
        self.prog = ctx.program(vertex_shader=VERT if not circular else CIRC_VERT, fragment_shader=FRAG)
        self.vbo = None
        self.vao = None
        self.n = 0

    def render(self, w, h, samples, spectrum, energy, flux, color, sensitivity):
        n = len(samples)
        x = np.linspace(-1.0, 1.0, n).astype("f4")
        y = (samples * 0.9 * sensitivity).astype("f4")
        verts = np.column_stack([x, y]).astype("f4")

        if self.vbo is None or self.n != n:
            self.vbo = self.ctx.buffer(verts.tobytes())
            self.vao = self.ctx.simple_vertex_array(self.prog, self.vbo, "in_vert")
            self.n = n
        else:
            self.vbo.write(verts.tobytes())

        if self.circular:
            self.prog["u_radius"].value = 0.65 + 0.2 * energy
        else:
            sx = 1.0
            sy = 1.0
            self.prog["u_scale"].value = (sx, sy)

        self.prog["u_color"].value = color
        self.vao.render(moderngl.LINE_STRIP)
