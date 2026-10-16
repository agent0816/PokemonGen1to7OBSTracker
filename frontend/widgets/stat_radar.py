"""Stat-Radar (Hexagon/Pentagon) im Stil der Ultra-Sonne/Ultra-Mond-Übersicht.

Zeichnet die Basiswerte eines Pokemon als ausgefülltes Vieleck über einem
Gitter, mit Wertebeschriftung an jeder Achse. Reines Kivy-Zeichenwidget — die
Werte/Beschriftungen kommen vom Aufrufer (Detail-Screen).
"""
import math

from kivy.graphics import Color, Line, Mesh
from kivy.metrics import dp, sp
from kivy.uix.widget import Widget
from kivymd.app import MDApp
from kivymd.uix.label import MDLabel

# Feste Bezugsskala, damit Radare verschiedener Pokemon vergleichbar sind
# (ein 90er-Wert sieht immer gleich weit aus). Werte darüber werden gekappt.
RADAR_SCALE = 200.0


class StatRadar(Widget):
    """Radar-Chart für N Achsen (6 = Hexagon, 5 = Gen-1-Pentagon)."""

    def __init__(self, labels: list[str], values: list[int],
                 accent=(0.20, 0.55, 0.95, 1.0), scale: float = RADAR_SCALE, **kwargs):
        super().__init__(**kwargs)
        self.labels = labels
        self.values = values
        self.accent = accent
        self.scale = scale or RADAR_SCALE
        self._vertex_labels: list[MDLabel] = []
        for name, val in zip(labels, values):
            # MDLabel folgt dem Theme (Textfarbe), bleibt also auch bei hellem
            # Theme lesbar — anders als ein plain Label mit Default-Weiß.
            lbl = MDLabel(text=f"{name}\n[b]{val}[/b]", markup=True, role="small",
                          halign="center", valign="center", size_hint=(None, None),
                          size=(dp(70), dp(34)))
            lbl.bind(size=lbl.setter("text_size"))
            self._vertex_labels.append(lbl)
            self.add_widget(lbl)
        self.bind(pos=self._redraw, size=self._redraw)

    def _points(self, cx, cy, radius, n):
        """N Vertex-Punkte, Start oben (90°), im Uhrzeigersinn."""
        pts = []
        for i in range(n):
            ang = math.radians(90 - i * (360 / n))
            pts.append((cx + radius * math.cos(ang), cy + radius * math.sin(ang)))
        return pts

    def _grid_color(self):
        app = MDApp.get_running_app()
        if app is not None:
            c = app.theme_cls.onSurfaceVariantColor
            return (c[0], c[1], c[2], 0.5)
        return (0.55, 0.55, 0.55, 0.45)

    def _redraw(self, *_args):
        self.canvas.before.clear()
        n = len(self.values)
        if n < 3:
            return
        cx, cy = self.center_x, self.center_y
        # Platz für die Randbeschriftung lassen.
        radius = min(self.width, self.height) / 2 - dp(38)
        if radius <= 0:
            return
        grid = self._grid_color()

        with self.canvas.before:
            # Gitter-Ringe (theme-abhängig)
            Color(*grid)
            for frac in (0.25, 0.5, 0.75, 1.0):
                ring = self._points(cx, cy, radius * frac, n)
                Line(points=[c for p in ring for c in p] +
                     [ring[0][0], ring[0][1]], width=1)
            # Speichen
            outer = self._points(cx, cy, radius, n)
            for px, py in outer:
                Line(points=[cx, cy, px, py], width=1)

            # Wert-Vieleck (gefüllt + Kontur), gekappt auf feste Skala
            base = self._points(cx, cy, radius, n)
            vpts = [(cx + (px - cx) * min(1.0, v / self.scale),
                     cy + (py - cy) * min(1.0, v / self.scale))
                    for (px, py), v in zip(base, self.values)]
            r, g, b, _ = self.accent
            Color(r, g, b, 0.35)
            # Triangle-Fan: Mittelpunkt + Vertices + erster Vertex (Schließung)
            fan = [cx, cy, 0, 0]
            for px, py in vpts + [vpts[0]]:
                fan += [px, py, 0, 0]
            indices = list(range(len(fan) // 4))
            Mesh(vertices=fan, indices=indices, mode="triangle_fan")
            Color(r, g, b, 1.0)
            Line(points=[c for p in vpts for c in p] + [vpts[0][0], vpts[0][1]], width=1.4)

        # Randbeschriftung an die Achsen setzen
        label_pts = self._points(cx, cy, radius + dp(22), n)
        for lbl, (px, py) in zip(self._vertex_labels, label_pts):
            lbl.center = (px, py)
