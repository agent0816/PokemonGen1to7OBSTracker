"""Linke Navigations-Rail der KivyMD-UI (löst die Bottom-NavBar ab).

Die Auswahl läuft ausschließlich über ``screen_manager.current`` als Single
Source of Truth — nicht über das Toggle-Verhalten von
``MDNavigationRail.set_active_item``. ``set_active_item`` wird nur benutzt, um
den Highlight nachzuziehen (es macht genau ein Item aktiv und alle anderen
inaktiv, inkl. der KivyMD-Animationen).
"""
from typing import Callable

from kivy.clock import Clock
from kivy.properties import StringProperty

from kivymd.uix.navigationrail import (
    MDNavigationRail,
    MDNavigationRailItem,
    MDNavigationRailItemIcon,
    MDNavigationRailItemLabel,
)

from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/frontend.log')


class TrackerNavigationRailItem(MDNavigationRailItem):
    """Rail-Item mit Icon + Label, das beim Klick einen Screen anwählt."""

    icon = StringProperty()
    display_name = StringProperty()
    screen_name = StringProperty()

    def __init__(self, on_select: Callable[[str], None], **kwargs) -> None:
        super().__init__(**kwargs)
        self._on_select = on_select
        # Icon/Label verzögert adden (wie im KivyMD-Doc-Beispiel): die
        # Container-ids der Item-KV stehen erst nach dem ersten Frame bereit.
        Clock.schedule_once(self._build_children)

    def _build_children(self, _dt: float) -> None:
        self.add_widget(MDNavigationRailItemIcon(icon=self.icon))
        self.add_widget(MDNavigationRailItemLabel(text=self.display_name))

    def on_release(self) -> None:
        # Kein Toggle: nur current setzen, der Highlight folgt via sync.
        self._on_select(self.screen_name)


class TrackerNavigationRail(MDNavigationRail):
    """NavigationRail, deren Highlight an ``screen_manager.current`` hängt."""

    def __init__(self, screen_manager, item_list: list[dict], **kwargs) -> None:
        kwargs.setdefault("type", "labeled")
        # anchor="center": Items mittig in der Rail. "top" klebt das erste Item
        # an die Oberkante, dann clippt der Auswahl-Indikator des aktiven Items.
        kwargs.setdefault("anchor", "center")
        super().__init__(**kwargs)
        self.screen_manager = screen_manager
        for item in item_list:
            self.add_item(item)
        # Highlight an den ScreenManager koppeln (programmatische Wechsel wie
        # Update-Check -> SessionMenu oder Wipe-Banner -> NuzlockeMenu ziehen
        # nach). Initial-Sync erst nach einem Frame, wenn die box_items-Kinder
        # und ihre Icon/Label-Container stehen.
        self.screen_manager.bind(current=self._on_screen_current)
        Clock.schedule_once(lambda _dt: self.sync_to_screen(self.screen_manager.current))

    def add_item(self, item: dict) -> None:
        rail_item = TrackerNavigationRailItem(on_select=self._select, **item)
        self.add_widget(rail_item)

    def _select(self, screen_name: str) -> None:
        self.screen_manager.current = screen_name
        # Highlight zusätzlich hart nachziehen: fängt den Re-Click-Fall ab, in
        # dem current unverändert bleibt (kein _on_screen_current) und das
        # Auto-Toggle von MDNavigationRail das Item sonst ausschaltet.
        self.sync_to_screen(screen_name)

    def _on_screen_current(self, screen_manager, screen_name: str) -> None:
        self.sync_to_screen(screen_name)

    def sync_to_screen(self, screen_name: str) -> None:
        items = self.get_items()
        target = next((it for it in items if getattr(it, "screen_name", None) == screen_name), None)
        if target is not None:
            if not target.active:
                # set_active_item toggelt: aus inaktiv wird aktiv, alle anderen
                # werden deaktiviert — genau das gewünschte Absolut-Verhalten.
                self.set_active_item(target)
        else:
            # Screen ohne Rail-Eintrag (z.B. Update): aktives Item ausschalten.
            active = next((it for it in items if it.active), None)
            if active is not None:
                self.set_active_item(active)
