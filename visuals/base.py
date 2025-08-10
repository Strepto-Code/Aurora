class BaseRenderer:
    def __init__(self, ctx):
        self.ctx = ctx
    def render(self, w, h, samples, spectrum, energy, flux, color, sensitivity):
        raise NotImplementedError
