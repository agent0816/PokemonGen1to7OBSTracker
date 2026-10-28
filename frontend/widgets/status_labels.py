"""Status-Effekt-Anzeige-Konstanten (Kürzel + Farbe) für die Team-Anzeige.

Kivy-frei, damit klassische UI (trainerbox.py) und KivyMD-UI (trainerboxmd.py)
dieselbe Quelle nutzen, ohne dass die MD-UI das komplette Klassik-Widget-Modul
(mit munchlax/obs-Abhängigkeiten, Feature-Freeze) importieren muss.
"""

# Kürzel als Kivy-Markup (farbig) + RGBA-Tupel (für Nicht-Markup-Kontexte).
STATUS_LABELS = {
    'freeze': ('[color=3068B0]FRZ[/color]', (0.19, 0.41, 0.69, 1)),
    'burn':   ('[color=E85030]BRN[/color]', (0.91, 0.31, 0.19, 1)),
    'para':   ('[color=FAD300]PAR[/color]', (0.98, 0.83, 0.0, 1)),
    'toxic':  ('[color=A040A0]TOX[/color]', (0.63, 0.25, 0.63, 1)),
    'poison': ('[color=A040A0]PSN[/color]', (0.63, 0.25, 0.63, 1)),
    'sleep':  ('[color=98D8D8]SLP[/color]', (0.6, 0.85, 0.85, 1)),
}
STATUS_PRIORITY = ('freeze', 'burn', 'para', 'toxic', 'poison', 'sleep')
