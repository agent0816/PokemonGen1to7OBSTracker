# Boot-Capture: startet main.py (TRACKER_UI=md), Screenshot frueh (Splash) + spaet.
# Nutzt die ECHTE backend/config, aber nur lesend (kein Settings-Save) -> unkritisch.
import os, sys, runpy

root = sys.argv[1]
os.chdir(root)
sys.path.insert(0, root)
os.environ['TRACKER_UI'] = 'md'

from kivy.clock import Clock

OUT = os.path.join(root, 'scratchpad')


def _arm(*_):
    from kivy.app import App
    from kivy.core.window import Window
    # Frueh = Splash sollte sichtbar sein (IP-Load + Update-Check laufen noch).
    Clock.schedule_once(lambda *_: Window.screenshot(name=os.path.join(OUT, 'splash_early.png')), 0.6)
    Clock.schedule_once(lambda *_: Window.screenshot(name=os.path.join(OUT, 'splash_late.png')), 6)

    def _close(*_):
        Window.dispatch('on_request_close')
        Clock.schedule_once(lambda *_: App.get_running_app().stop(), 3)
    Clock.schedule_once(_close, 9)


Clock.schedule_once(_arm, 0)
sys.argv = ['main.py']
runpy.run_path('main.py', run_name='__main__')
