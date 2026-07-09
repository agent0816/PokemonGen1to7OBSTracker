"""BoxMenu — zeigt PC-Boxen eines Spielers als Grid.

Lese-Strategie: Boxen werden nur auf expliziten Refresh-Klick abgefragt, nicht
zyklisch — read_all_boxes ist teuer (pro Box 1 TCP-Roundtrip über BizHawk,
bzw. ~220 UDP-Roundtrips über Citra). Nach erfolgreichem Lesen liegen die
dekodierten Boxen in munchlax.boxes[player].
"""
import asyncio
import traceback

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.screenmanager import Screen, ScreenManager, NoTransition
from kivy.uix.spinner import Spinner

from frontend.widgets.pokemon_detail import PokemonDetailScreen
from backend.logging_setup import get_logger


GAMES_CITRA = {"X", "Y", "Omega Rubin", "Alpha Saphir",
               "Sonne", "Mond", "Ultra Sonne", "Ultra Mond"}

# Grid-Dimensionen pro Generation — Gen 1/2 nutzen 4×5=20 Slots,
# Gen 3+ die vollen 5×6=30. Für einheitliches Layout rendern wir immer
# 5 Zeilen × 6 Spalten und blenden überzählige Slots aus.
SLOTS_PER_BOX_GEN12 = 20
SLOTS_PER_BOX_GEN3PLUS = 30
GRID_COLS = 6
GRID_ROWS = 5


logger = get_logger(__name__, './logs/boxmenu.log')


class BoxSlotWidget(ButtonBehavior, BoxLayout):
    """Ein einzelner Slot im Box-Grid: Sprite + Nickname + Level."""

    def __init__(self, obs_websocket, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.obs_websocket = obs_websocket

        self.sprite = Image(
            source=f"{obs_websocket.conf['common_path']}/{obs_websocket.conf['red']}/0.png",
            fit_mode="contain",
            size_hint_y=0.7,
        )
        self.add_widget(self.sprite)

        self.nickname_label = Label(text="", size_hint_y=0.15, font_size="10sp")
        self.add_widget(self.nickname_label)

        self.level_label = Label(text="", size_hint_y=0.15, font_size="10sp")
        self.add_widget(self.level_label)

    def set_pokemon(self, pokemon, edition: int):
        if pokemon is None or pokemon.dexnr in (0, "", None):
            self.sprite.source = f"{self.obs_websocket.conf['common_path']}/{self.obs_websocket.conf['red']}/0.png"
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


class BoxMenu(Screen):
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

        root = BoxLayout(orientation="vertical", padding=("10dp", "10dp"), spacing="5dp")

        # Kopfzeile: Zurück + Titel + Refresh
        header = BoxLayout(orientation="horizontal", size_hint_y=None, height="40dp", spacing="10dp")
        self.back_button = Button(text="Zurück zum Hauptmenü", size_hint_x=0.3, on_press=self._back)
        header.add_widget(self.back_button)
        self.title_label = Label(text="PC-Boxen")
        header.add_widget(self.title_label)
        self.refresh_button = Button(text="Aktualisieren", size_hint_x=0.2, on_press=self._trigger_refresh)
        header.add_widget(self.refresh_button)
        root.add_widget(header)

        # Navigation: Spieler-Spinner + Box-Pfeile + Box-Spinner
        nav = BoxLayout(orientation="horizontal", size_hint_y=None, height="40dp", spacing="10dp")

        nav.add_widget(Label(text="Spieler:", size_hint_x=0.1))
        player_values = [str(i) for i in range(1, self.pl.get("player_count", 1) + 1)]
        self.player_spinner = Spinner(
            text=player_values[0] if player_values else "1",
            values=player_values,
            size_hint_x=0.1,
        )
        self.player_spinner.bind(text=self._on_player_change)
        nav.add_widget(self.player_spinner)

        self.prev_button = Button(text="<", size_hint_x=0.08, on_press=self._prev_box)
        nav.add_widget(self.prev_button)

        self.box_spinner = Spinner(text="Box 1", values=["Box 1"], size_hint_x=0.18)
        self.box_spinner.bind(text=self._on_box_spinner_change)
        nav.add_widget(self.box_spinner)

        self.next_button = Button(text=">", size_hint_x=0.08, on_press=self._next_box)
        nav.add_widget(self.next_button)

        self.status_label = Label(text="Noch keine Boxen geladen — auf Aktualisieren klicken.", size_hint_x=0.46)
        nav.add_widget(self.status_label)
        root.add_widget(nav)

        # Slot-Grid (5 Zeilen × 6 Spalten), gewrappt in nested ScreenManager
        self.grid = GridLayout(cols=GRID_COLS, rows=GRID_ROWS, spacing="5dp")
        self.slot_widgets: list[BoxSlotWidget] = []
        for i in range(GRID_COLS * GRID_ROWS):
            slot = BoxSlotWidget(self.obs_websocket)
            slot.bind(on_press=lambda instance, idx=i: self._show_slot_detail(idx))
            self.slot_widgets.append(slot)
            self.grid.add_widget(slot)

        grid_screen = Screen(name="BoxGrid")
        grid_screen.add_widget(self.grid)
        self.box_detail_screen = PokemonDetailScreen(self.obs_websocket)
        self.box_sm = ScreenManager(transition=NoTransition())
        self.box_sm.add_widget(grid_screen)
        self.box_sm.add_widget(self.box_detail_screen)

        root.add_widget(self.box_sm)

        self.add_widget(root)

    def on_pre_enter(self, *args):
        # Spieleranzahl kann sich seit Konstruktion geändert haben (Session-
        # Wechsel, Settings) — Spinner deshalb bei jedem Öffnen neu aufbauen.
        self._refresh_player_spinner_values()
        self._render_current_box()
        self.box_sm.current = "BoxGrid"

    def _refresh_player_spinner_values(self):
        player_count = max(1, int(self.pl.get("player_count", 1)))
        values = [str(i) for i in range(1, player_count + 1)]
        self.player_spinner.values = values
        # Falls der zuletzt gewählte Spieler nicht mehr existiert, auf 1 fallen.
        if self.current_player > player_count:
            self.current_player = 1
            self.current_box_index = 0
        self.player_spinner.text = str(self.current_player)

    def _back(self, instance):
        self.manager.current = "MainMenu"

    def _on_player_change(self, spinner, text):
        try:
            self.current_player = int(text)
        except ValueError:
            return
        self.current_box_index = 0
        self._refresh_box_spinner_values()
        self._render_current_box()

    def _on_box_spinner_change(self, spinner, text):
        if not text.startswith("Box "):
            return
        try:
            self.current_box_index = int(text.split()[1]) - 1
        except (ValueError, IndexError):
            return
        self._render_current_box()

    def _prev_box(self, instance):
        boxes = self.munchlax.boxes.get(self.current_player, [])
        if not boxes:
            return
        self.current_box_index = (self.current_box_index - 1) % len(boxes)
        self.box_spinner.text = f"Box {self.current_box_index + 1}"

    def _next_box(self, instance):
        boxes = self.munchlax.boxes.get(self.current_player, [])
        if not boxes:
            return
        self.current_box_index = (self.current_box_index + 1) % len(boxes)
        self.box_spinner.text = f"Box {self.current_box_index + 1}"

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
            await self.munchlax.update_boxes(player, boxes)
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
        return await self.bizhawk.read_and_decode_boxes(client_id)

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
        values = [f"Box {i + 1}" for i in range(len(boxes))] or ["Box 1"]
        self.box_spinner.values = values
        self.box_spinner.text = f"Box {self.current_box_index + 1}"

    def _show_slot_detail(self, slot_index):
        boxes = self.munchlax.boxes.get(self.current_player, [])
        if not boxes:
            return
        index = max(0, min(self.current_box_index, len(boxes) - 1))
        box = boxes[index]
        if slot_index >= len(box):
            return
        pokemon = box[slot_index]
        if pokemon is None:
            return
        edition = self.munchlax.editions.get(self.current_player, 0)
        rando_data = None
        if self.manager:
            main_menu = self.manager.get_screen("MainMenu")
            if hasattr(main_menu, 'randomizer'):
                rando_data = main_menu.randomizer.get_log_data()
        self.box_detail_screen.show(
            pokemon, edition, rando_data,
            back_callback=lambda: setattr(self.box_sm, 'current', 'BoxGrid'),
            munchlax=self.munchlax,
        )
        self.box_sm.current = "PokemonDetail"

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
