"""
Build-Spike KivyMD 2.0.0
========================

Minimal-App, die prüft, ob KivyMD 2.0.0 samt neuer Abhängigkeiten
(PyCairo, MaterialShapes, MaterialYouColor, asynckivy) aus Source UND als
Nuitka-Onefile-Binary läuft.

Startet wie main.py über ``app.async_run(async_lib='asyncio')``, öffnet
nacheinander Dialog, Snackbar, Dropdown-Menü und Loading-Indicator,
macht einen Screenshot und schreibt ``spike_result.json`` ins cwd.

Aufruf:
    python spike_app.py               # Auto-Durchlauf, beendet sich selbst
    python spike_app.py --interactive # bleibt offen zum Durchklicken
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from typing import Any

os.environ.setdefault('KIVY_LOG_MODE', 'PYTHON')

from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.metrics import dp

import kivymd
from kivymd.app import MDApp
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.dialog import (
    MDDialog,
    MDDialogButtonContainer,
    MDDialogHeadlineText,
    MDDialogSupportingText,
)
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.snackbar import MDSnackbar, MDSnackbarText

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger('kivymd_spike')

INTERACTIVE = '--interactive' in sys.argv

KV = """
MDScreen:
    md_bg_color: app.theme_cls.backgroundColor

    MDBoxLayout:
        orientation: "vertical"

        MDBoxLayout:
            orientation: "vertical"
            padding: "16dp"
            spacing: "12dp"

            MDCard:
                style: "elevated"
                size_hint_y: None
                height: "96dp"
                padding: "16dp"
                spacing: "12dp"

                MDIcon:
                    icon: "pokeball"
                    pos_hint: {"center_y": .5}

                MDLabel:
                    text: "KivyMD-Build-Spike"
                    font_style: "Title"
                    role: "large"

                MDLoadingIndicator:
                    id: indicator
                    size_hint: None, None
                    size: "48dp", "48dp"
                    pos_hint: {"center_y": .5}

            MDTextField:
                mode: "outlined"
                size_hint_x: None
                width: "300dp"

                MDTextFieldHintText:
                    text: "Spielername"

            MDBoxLayout:
                adaptive_height: True
                spacing: "8dp"

                MDButton:
                    id: btn_dialog
                    style: "filled"
                    on_release: app.open_dialog()

                    MDButtonText:
                        text: "Dialog"

                MDButton:
                    style: "outlined"
                    on_release: app.open_snackbar()

                    MDButtonText:
                        text: "Snackbar"

                MDButton:
                    id: btn_menu
                    style: "tonal"
                    on_release: app.open_menu(self)

                    MDButtonText:
                        text: "Menü"

            Widget:

        MDNavigationBar:

            MDNavigationItem:
                active: True

                MDNavigationItemIcon:
                    icon: "home"

                MDNavigationItemLabel:
                    text: "Team"

            MDNavigationItem:

                MDNavigationItemIcon:
                    icon: "book-open-variant"

                MDNavigationItemLabel:
                    text: "Pokedex"

            MDNavigationItem:

                MDNavigationItemIcon:
                    icon: "cog"

                MDNavigationItemLabel:
                    text: "Einstellungen"
"""


class SpikeApp(MDApp):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.result: dict[str, Any] = {
            'frozen': bool(getattr(sys, 'frozen', False)) or '__compiled__' in globals(),
            'python': sys.version,
            'kivymd_version': kivymd.__version__,
            'checks': {},
            'errors': [],
        }
        self.dialog: MDDialog | None = None
        self.menu: MDDropdownMenu | None = None

    def build(self):
        self.theme_cls.primary_palette = 'Red'
        return Builder.load_string(KV)

    # --- Checks -----------------------------------------------------------

    def _check(self, name: str, ok: bool, detail: Any = None) -> None:
        self.result['checks'][name] = {'ok': bool(ok), 'detail': detail}
        level = logging.INFO if ok else logging.ERROR
        logger.log(level, f"CHECK {name}: {'OK' if ok else 'FEHLER'} {detail if detail is not None else ''}")

    def _run_step(self, name: str, func) -> None:
        try:
            func()
        except Exception as err:
            self.result['errors'].append(f"{name}: {err}\n{traceback.format_exc()}")
            self._check(name, False, str(err))

    def check_assets(self) -> None:
        fonts = os.listdir(kivymd.fonts_path)
        self._check('fonts_roboto', 'Roboto-Regular.ttf' in fonts, len(fonts))
        self._check('fonts_icons', 'materialdesignicons-webfont.ttf' in fonts)
        self._check('label_fonts_registered',
                    'Roboto' in LabelBase._fonts and 'Icons' in LabelBase._fonts)
        images = os.listdir(kivymd.images_path)
        self._check('images', 'transparent.png' in images, len(images))

    def check_shapes(self) -> None:
        # MaterialShapes + PyCairo werden vom Loading-Indicator gebraucht
        import cairo
        import materialshapes
        indicator = self.root.ids.indicator
        indicator.start()
        self._check('pycairo', True, cairo.version)
        self._check('materialshapes', True, getattr(materialshapes, '__version__', 'n/a'))
        self._check('loading_indicator', True, indicator.get_shape_names()[:3])

    def open_dialog(self) -> None:
        self.dialog = MDDialog(
            MDDialogHeadlineText(text='Verbindung getrennt'),
            MDDialogSupportingText(text='Spieler 2 hat die Verbindung verloren.'),
            MDDialogButtonContainer(
                MDButton(MDButtonText(text='OK'), style='text',
                         on_release=lambda *_: self.dialog.dismiss()),
            ),
        )
        self.dialog.open()

    def open_snackbar(self) -> None:
        MDSnackbar(
            MDSnackbarText(text='Team synchronisiert'),
            y=dp(96),
            pos_hint={'center_x': 0.5},
            size_hint_x=0.6,
        ).open()

    def open_menu(self, caller) -> None:
        items = [{'text': name, 'on_release': lambda *_: self.menu.dismiss()}
                 for name in ('Rot', 'Gold', 'Saphir', 'Platin', 'Schwarz')]
        self.menu = MDDropdownMenu(caller=caller, items=items)
        self.menu.open()

    # --- Auto-Durchlauf -----------------------------------------------------

    def on_start(self) -> None:
        self._run_step('assets', self.check_assets)
        self._run_step('shapes', self.check_shapes)
        if INTERACTIVE:
            return
        Clock.schedule_once(lambda *_: self._run_step('dialog', self._step_dialog), 1.0)
        Clock.schedule_once(lambda *_: self._run_step('snackbar', self._step_snackbar), 2.0)
        Clock.schedule_once(lambda *_: self._run_step('menu', self._step_menu), 3.0)
        Clock.schedule_once(lambda *_: self._run_step('screenshot', self._step_screenshot), 4.0)
        Clock.schedule_once(lambda *_: self._finish(), 5.0)

    def _step_dialog(self) -> None:
        self.open_dialog()
        self._check('dialog', self.dialog is not None and self.dialog.parent is not None)

    def _step_snackbar(self) -> None:
        self.dialog.dismiss()
        self.open_snackbar()
        self._check('snackbar', True)

    def _step_menu(self) -> None:
        self.open_menu(self.root.ids.btn_menu)
        self._check('dropdown_menu', self.menu is not None)

    def _step_screenshot(self) -> None:
        path = Window.screenshot(name=os.path.join(os.getcwd(), 'spike_screenshot.png'))
        self._check('screenshot', bool(path) and os.path.exists(path), path)

    def _finish(self) -> None:
        self.result['ok'] = (not self.result['errors']
                             and all(c['ok'] for c in self.result['checks'].values()))
        with open('spike_result.json', 'w', encoding='utf-8') as f:
            json.dump(self.result, f, indent=2, ensure_ascii=False)
        logger.info(f"Spike fertig, ok={self.result['ok']}")
        self.stop()


async def main() -> None:
    app = SpikeApp()
    started = time.perf_counter()
    await app.async_run(async_lib='asyncio')
    logger.info(f"Laufzeit {time.perf_counter() - started:.1f}s")


if __name__ == '__main__':
    asyncio.run(main())
