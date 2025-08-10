import numpy as np
import moderngl
from .base import BaseRenderer

VERT = """
#version 330
in vec2 in_vert;
uniform float u_aspect;
uniform float u_time;
uniform float u_energy;
void main() {
    vec2 v = in_vert;
    v *= (1.0 + 0.05 * u_energy);
    v.x *= u_aspect;
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

class ParticleRenderer(BaseRenderer):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.prog = ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
        self.vbo = None
        self.vao = None
        self.n = 0
        self.particles = np.zeros((0, 4), dtype=np.float32)  # x, y, vx, vy
        self.time = 0.0
        self.threshold = 0.02
        self.decay = 0.98

    def render(self, w, h, samples, spectrum, energy, flux, color, sensitivity):
        self.time += 1.0 / 60.0
        if flux * sensitivity > self.threshold:
            nspawn = int(20 + 100 * np.clip(flux * sensitivity, 0, 1))
            angles = np.random.rand(nspawn) * 2 * np.pi
            speeds = 0.2 + np.random.rand(nspawn) * 0.6 * (0.5 + energy)
            vx = np.cos(angles) * speeds * 0.02
            vy = np.sin(angles) * speeds * 0.02
            newp = np.column_stack([np.zeros(nspawn), np.zeros(nspawn), vx, vy]).astype(np.float32)
            self.particles = np.vstack([self.particles * self.decay, newp])
            if len(self.particles) > 4000:
                self.particles = self.particles[-4000:]

        self.particles[:, 0] += self.particles[:, 2]
        self.particles[:, 1] += self.particles[:, 3]
        self.particles[:, 2:] *= 0.99

        verts = self.particles[:, :2].astype("f4")
        if len(verts) == 0:
            verts = np.zeros((1,2), dtype="f4")

        if self.vbo is None or self.n != len(verts):
            self.vbo = self.ctx.buffer(verts.tobytes())
            self.vao = self.ctx.simple_vertex_array(self.prog, self.vbo, "in_vert")
            self.n = len(verts)
        else:
            self.vbo.write(verts.tobytes())

        aspect = w / max(1.0, float(h))
        self.prog["u_aspect"].value = aspect
        self.prog["u_time"].value = self.time
        self.prog["u_energy"].value = energy
        self.prog["u_color"].value = color
        self.vao.render(moderngl.POINTS)
