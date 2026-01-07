import numpy as np
import moderngl


class BaseRenderer:
    def __init__(self, ctx):
        self.ctx = ctx
    def render(self, w, h, samples, spectrum, energy, flux, color, sensitivity):
        raise NotImplementedError


# ---- Waveform ----

WAVE_VERT = """
#version 330
in vec2 in_vert;
uniform vec2 u_scale;
void main() {
    vec2 v = in_vert * u_scale;
    gl_Position = vec4(v, 0.0, 1.0);
}
"""

WAVE_FRAG = """
#version 330
uniform vec3 u_color;
out vec4 f_color;
void main() {
    f_color = vec4(u_color, 1.0);
}
"""

WAVE_CIRC_VERT = """
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
        self.prog = ctx.program(vertex_shader=WAVE_VERT if not circular else WAVE_CIRC_VERT, fragment_shader=WAVE_FRAG)
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


# ---- Spectrum ----

SPEC_VERT = """
#version 330
in vec2 in_vert;
uniform vec2 u_scale;
void main() {
    vec2 v = in_vert * u_scale;
    gl_Position = vec4(v, 0.0, 1.0);
}
"""

SPEC_FRAG = """
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
        self.prog = ctx.program(vertex_shader=SPEC_VERT, fragment_shader=SPEC_FRAG)
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


# ---- Particles ----

PART_VERT = """
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

PART_FRAG = """
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
        self.prog = ctx.program(vertex_shader=PART_VERT, fragment_shader=PART_FRAG)
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
