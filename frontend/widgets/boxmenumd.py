"""KivyMD-BoxMenu — zeigt PC-Boxen eines Spielers als 6x5-Grid.

Lese-Strategie: Boxen werden nur auf expliziten Refresh-Klick abgefragt, nicht
zyklisch — read_all_boxes ist teuer (pro Box 1 TCP-Roundtrip über BizHawk,
bzw. ~220 UDP-Roundtrips über Citra). Nach erfolgreichem Lesen liegen die
dekodierten Boxen in munchlax.boxes[player]. Die Refresh-Mechanik ist 1:1 aus
der klassischen UI (frontend/widgets/boxmenu.py) übernommen.
"""
import asyncio
import traceback

from kivy.clock import Clock
from kivy.metrics import dp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText, MDIconButton
from kivymd.uix.card import MDCard
from kivymd.uix.fitimage import FitImage
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.screen import MDScreen

from backend.logging_setup import get_logger

GAMES_CITRA = {"X", "Y", "Omega Rubin", "Alpha Saphir",
               "Sonne", "Mond", "Ultra Sonne", "Ultra Mond"}

# Gen 1/2 nutzen 4x5=20 Slots, Gen 3+ die vollen 5x6=30. Für ein einheitliches
# Layout wird immer 5 Zeilen x 6 Spalten gerendert, überzählige Slots bleiben leer.
SLOTS_PER_BOX_GEN12 = 20
SLOTS_PER_BOX_GEN3PLUS = 30
GRID_COLS = 6
GRID_ROWS = 5

logger = get_logger(__name__, 'logs/boxmenu.log')


class BoxSlotCard(MDCard):
    """Ein einzelner Slot im Box-Grid: Sprite + Nickname + Level."""

    def __init__(self, obs_websocket, **kwargs):
        super().__init__(style="elevated", ripple_behavior=True, orientation="vertical",
                         padding=dp(2), spacing=0, radius=[dp(8)] * 4, **kwargs)
        self.obs_websocket = obs_websocket

        self.sprite = FitImage(source=self._empty_sprite(), fit_mode="contain", size_hint_y=0.68)
        self.add_widget(self.sprite)

        self.nickname_label = self._small_label()
        self.add_widget(self.nickname_label)
        self.level_label = self._small_label()
        self.add_widget(self.level_label)

    @staticmethod
    def _small_label() -> MDLabel:
        lbl = MDLabel(text="", size_hint_y=0.16, halign="center", valign="center",
                      shorten=True, shorten_from="right")
        lbl.role = "small"
        lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        return lbl

    def _empty_sprite(self) -> str:
        try:
            conf = self.obs_websocket.conf
            return f"{conf['common_path']}/{conf['red']}/0.png"
        except Exception:
            return ""

    def set_pokemon(self, pokemon, edition: int) -> None:
        if pokemon is None or pokemon.dexnr in (0, "", None):
            self.sprite.source = self._empty_sprite()
            self.nickname_label.text = ""
            self.level_label.text = ""
            return
        try:
            path = self.obs_websocket.get_sprite(pokemon, False, edition, two_pc=False)
            self.sprite.source = path
        except Exception as err:
            logger.error(f"get_sprite für Box-Slot gescheitert: {err}")
        self.nickname_label.text = str(pokemon.nickname or "")
        self.level_label.text = f"Lv {pokemon.lvl}" if pokemon.lvl else ""


class BoxMenu(MDScreen):
    def __init__(self, bizhawk, citra, munchlax, obs_websocket, pl, **kwargs):
        super().__init__(**kwargs)
        self.name = "BoxMenu"
        self.bizhawk = bizhawk
        self.citra = citra
        self.munchlax = munchlax
        self.obs_websocket = obs_websocket
        self.pl = pl
        self.current_player: int = 1
        self.current_box_index: int = 0
        self._refresh_task = None
        self._menus: list[MDDropdownMenu] = []

        root = MDBoxLayout(orientation="vertical", padding="10dp", spacing="8dp")

        # Kopfzeile: Titel + Refresh (kein Zurück-Button, Navigation über die Rail)
        header = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="48dp", spacing="10dp")
        self.title_label = MDLabel(text="PC-Boxen", role="large", valign="center")
        header.add_widget(self.title_label)
        self.refresh_button = MDButton(
            MDButtonText(text="Aktualisieren"), style="tonal",
            pos_hint={"center_y": .5}, on_release=self._trigger_refresh,
        )
        header.add_widget(self.refresh_button)
        root.add_widget(header)

        # Navigation: Spieler-Dropdown + Box-Pfeile + Box-Dropdown + Status.
        # Zweizeilig, damit bei schmaler Breite nichts abgeschnitten wird.
        nav = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="48dp", spacing="8dp")
        self.player_button_text = MDButtonText(text="Spieler: 1")
        self.player_button = MDButton(self.player_button_text, style="tonal",
                                      pos_hint={"center_y": .5})
        self.player_button.bind(on_release=lambda btn: self._open_player_menu(btn))
        nav.add_widget(self.player_button)

        self.prev_button = MDIconButton(icon="chevron-left", pos_hint={"center_y": .5},
                                        on_release=self._prev_box)
        nav.add_widget(self.prev_button)
        self.box_button_text = MDButtonText(text="Box 1/1")
        self.box_button = MDButton(self.box_button_text, style="tonal", pos_hint={"center_y": .5})
        self.box_button.bind(on_release=lambda btn: self._open_box_menu(btn))
        nav.add_widget(self.box_button)
        self.next_button = MDIconButton(icon="chevron-right", pos_hint={"center_y": .5},
                                        on_release=self._next_box)
        nav.add_widget(self.next_button)
        root.add_widget(nav)

        self.status_label = MDLabel(text="Noch keine Boxen geladen — auf Aktualisieren klicken.",
                                    size_hint_y=None, height="24dp", valign="center")
        self.status_label.role = "small"
        root.add_widget(self.status_label)

        # Slot-Grid fest 6 Spalten x 5 Zeilen; Slots skalieren über size_hint.
        self.grid = MDGridLayout(cols=GRID_COLS, rows=GRID_ROWS, spacing=dp(6))
        self.slot_widgets: list[BoxSlotCard] = []
        for i in range(GRID_COLS * GRID_ROWS):
            slot = BoxSlotCard(self.obs_websocket)
            slot.bind(on_release=lambda _inst, idx=i: self._show_slot_detail(idx))
            self.slot_widgets.append(slot)
            self.grid.add_widget(slot)
        root.add_widget(self.grid)

        self.add_widget(root)

    # --- Lifecycle ------------------------------------------------------------

    def on_pre_enter(self, *args):
        # Spieleranzahl kann sich seit Konstruktion geändert haben (Session-
        # Wechsel, Settings) — Spieler-Nav deshalb bei jedem Öffnen neu aufbauen.
        self._refresh_player_spinner_values()
        self._refresh_box_spinner_values()
        self._render_current_box()

    def load_from_session(self) -> None:
        """Session-Wechsel-Hook: zurück auf Spieler 1 / Box 1. Kein Emulator-Read."""
        self.current_player = 1
        self.current_box_index = 0
        self._refresh_player_spinner_values()
        self._refresh_box_spinner_values()
        self._render_current_box()

    def _player_count(self) -> int:
        try:
            return max(1, int(self.pl.get("player_count", 1)))
        except (TypeError, ValueError):
            return 1

    def _refresh_player_spinner_values(self):
        player_count = self._player_count()
        # Falls der zuletzt gewählte Spieler nicht mehr existiert, auf 1 fallen.
        if self.current_player > player_count:
            self.current_player = 1
            self.current_box_index = 0
        self.player_button_text.text = f"Spieler: {self.current_player}"

    # --- Dropdowns ------------------------------------------------------------

    def _open_menu(self, caller, entries: list[tuple[str, object]], on_pick) -> None:
        # Evtl. noch offenes Dropdown schließen, bevor ein neues die Referenz ersetzt
        for m in self._menus:
            m.dismiss()
        items = [{"text": text, "on_release": (lambda v=value: self._menu_pick(on_pick, v))}
                 for text, value in entries]
        menu = MDDropdownMenu(caller=caller, items=items)
        self._menus = [menu]
        menu.open()

    def _menu_pick(self, on_pick, value) -> None:
        for m in self._menus:
            m.dismiss()
        on_pick(value)

    def _open_player_menu(self, caller) -> None:
        entries = [(f"Spieler {i}", i) for i in range(1, self._player_count() + 1)]
        self._open_menu(caller, entries, self._on_player_change)

    def _open_box_menu(self, caller) -> None:
        boxes = self.munchlax.boxes.get(self.current_player, [])
        count = max(1, len(boxes))
        entries = [(f"Box {i + 1}", i) for i in range(count)]
        self._open_menu(caller, entries, self._on_box_spinner_change)

    def _on_player_change(self, player) -> None:
        try:
            self.current_player = int(player)
        except (TypeError, ValueError):
            return
        self.current_box_index = 0
        self.player_button_text.text = f"Spieler: {self.current_player}"
        self._refresh_box_spinner_values()
        self._render_current_box()

    def _on_box_spinner_change(self, index) -> None:
        boxes = self.munchlax.boxes.get(self.current_player, [])
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return
        self.current_box_index = idx % len(boxes) if boxes else 0
        self._refresh_box_spinner_values()
        self._render_current_box()

    def _prev_box(self, instance):
        boxes = self.munchlax.boxes.get(self.current_player, [])
        if not boxes:
            return
        self._on_box_spinner_change((self.current_box_index - 1) % len(boxes))

    def _next_box(self, instance):
        boxes = self.munchlax.boxes.get(self.current_player, [])
        if not boxes:
            return
        self._on_box_spinner_change((self.current_box_index + 1) % len(boxes))

    # --- Refresh (1:1 aus der Klassik) -----------------------------------------

    def _trigger_refresh(self, instance):
        if self._refresh_task and not self._refresh_task.done():
            self.status_label.text = "Läuft bereits…"
            return
        self.refresh_button.disabled = True
        self.status_label.text = f"Lese Boxen für Spieler {self.current_player}…"
        self._refresh_task = asyncio.create_task(self._do_refresh(self.current_player))

    async def _do_refresh(self, player: int):
        try:
            boxes = await self._read_boxes_for_player(player)
            if boxes is None:
                return
            # update_boxes cached lokal und pusht (falls verbunden) an Arceus,
            # damit Remote-Munchlaxes automatisch nachgezogen werden.
            # Munchlax auf eigenem Thread (Phase 3) → cross-thread submit.
            await self.munchlax.submit_cross_thread(
                self.munchlax.update_boxes(player, boxes)
            )
            self.current_box_index = 0
            # Kivy-Widgets nur im Main-Thread updaten: Clock.schedule_once().
            Clock.schedule_once(lambda dt: self._after_refresh(player, len(boxes)), 0)
        except Exception as err:
            logger.error(f"BoxMenu-Refresh gescheitert: {type(err)},{err}")
            logger.error(traceback.format_exc())
            Clock.schedule_once(lambda dt: self._after_refresh_error(str(err)), 0)

    async def _read_boxes_for_player(self, player: int) -> list | None:
        """Entscheidet anhand der Edition, welcher Emulator-Handler zuständig ist."""
        game = self.pl.get("session_game", "")
        if game in GAMES_CITRA:
            # Citra-Handler bedient aktuell nur den lokalen Player
            if self.citra.player_number != player:
                Clock.schedule_once(
                    lambda dt: self._after_refresh_error(
                        f"Citra ist Spieler {self.citra.player_number} zugeordnet, nicht {player}."
                    ), 0,
                )
                return None
            return await self.citra.read_and_decode_boxes()
        client_id = f"player{player:03d}"
        if client_id not in self.bizhawk.bizhawks:
            Clock.schedule_once(
                lambda dt: self._after_refresh_error(f"Emulator {client_id} nicht verbunden."),
                0,
            )
            return None
        from frontend.bh_dispatch import bh_submit
        return await bh_submit(self.bizhawk.read_and_decode_boxes(client_id))

    def _after_refresh(self, player: int, box_count: int):
        self.refresh_button.disabled = False
        self.status_label.text = f"{box_count} Boxen für Spieler {player} geladen."
        self._refresh_box_spinner_values()
        self._render_current_box()

    def _after_refresh_error(self, message: str):
        self.refresh_button.disabled = False
        self.status_label.text = message

    def _refresh_box_spinner_values(self):
        boxes = self.munchlax.boxes.get(self.current_player, [])
        self.box_button_text.text = f"Box {self.current_box_index + 1}/{max(1, len(boxes))}"

    # --- Detail / Render ------------------------------------------------------

    def _show_slot_detail(self, slot_index: int):
        boxes = self.munchlax.boxes.get(self.current_player, [])
        if not boxes:
            return
        index = max(0, min(self.current_box_index, len(boxes) - 1))
        box = boxes[index]
        if slot_index >= len(box):
            return
        pokemon = box[slot_index]
        if pokemon is None or self.manager is None:
            return
        edition = self.munchlax.editions.get(self.current_player, 0)
        rando_data = None
        try:
            if self.manager.has_screen("MainMenu"):
                main_menu = self.manager.get_screen("MainMenu")
                if hasattr(main_menu, 'randomizer'):
                    rando_data = main_menu.randomizer.get_log_data()
        except Exception as err:
            logger.error(f"Rando-Daten für Box-Detail nicht lesbar: {err}")
        detail = self.manager.get_screen("PokemonDetail")
        detail.show(pokemon, edition, rando_data, return_screen="BoxMenu", munchlax=self.munchlax)
        # Bearbeiten (nur BizHawk-Spieler lokal; Gate liegt in set_edit_target)
        personality = getattr(pokemon, "personality", None)
        target = None
        if personality is not None:
            target = {"kind": "box", "box": index, "slot": slot_index,
                      "personality": personality}
        try:
            detail.set_edit_target(target, self.bizhawk, self.current_player, self.pl)
        except Exception:
            logger.error(f"Edit-Ziel setzen fehlgeschlagen: {traceback.format_exc()}")
        self.manager.current = "PokemonDetail"

    def _render_current_box(self):
        boxes = self.munchlax.boxes.get(self.current_player, [])
        edition = self.munchlax.editions.get(self.current_player, 0)
        if not boxes:
            for slot_widget in self.slot_widgets:
                slot_widget.set_pokemon(None, edition)
            self.title_label.text = f"PC-Boxen — Spieler {self.current_player}"
            return
        index = max(0, min(self.current_box_index, len(boxes) - 1))
        box = boxes[index]
        # Slots die die Box kennt werden befüllt, der Rest wird leer gerendert.
        for i, slot_widget in enumerate(self.slot_widgets):
            pokemon = box[i] if i < len(box) else None
            slot_widget.set_pokemon(pokemon, edition)
        self.title_label.text = (
            f"PC-Boxen — Spieler {self.current_player} — Box {index + 1}/{len(boxes)}"
        )
        # Status nur setzen, wenn gerade kein Refresh läuft (sonst dessen
        # Fortschritts-/Fehlertext nicht überschreiben).
        if not (self._refresh_task and not self._refresh_task.done()):
            self.status_label.text = f"{len(boxes)} Boxen geladen."
