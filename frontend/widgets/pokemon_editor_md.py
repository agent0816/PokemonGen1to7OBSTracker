"""KivyMD-Dialog zum Bearbeiten eines Pokemon (Level + Attacken).

Controller-Klasse (kein Widget), die einen ``MDDialog`` besitzt. Die Logik
(Pool, Validierung, Stat-Vorschau, ``PokemonEdit``) liegt Kivy-frei in
``backend/pokemon_editor_logic.py``. Geschrieben wird über
``bizhawk.edit_pokemon`` — BizHawk läuft auf eigenem Thread-Loop, daher
zwingend über ``frontend.bh_dispatch.bh_submit``.
"""
from __future__ import annotations

import asyncio
import traceback
from typing import Callable

from kivy.clock import Clock
from kivy.metrics import dp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText, MDIconButton
from kivymd.uix.dialog import (
    MDDialog, MDDialogButtonContainer, MDDialogContentContainer, MDDialogHeadlineText,
)
from kivymd.uix.label import MDLabel
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.textfield import MDTextField
from kivy.uix.widget import Widget

from backend.logging_setup import get_logger
from backend.pokemon_detail_view import moves_de
from backend.pokemon_editor_logic import (
    LEVEL_MAX, LEVEL_MIN, MOVE_SLOTS, build_edit, clamp_level, current_move_ids,
    format_stats, move_choices, preview_stats, validate_moves,
)
from frontend.widgets.md_common import show_snackbar

logger = get_logger(__name__, 'logs/pokemon_detail.log')

EMPTY_LABEL = "— leer —"
HINT_TEXT = "Nicht im Kampf. Keine Entwicklung/kein Attacken-Lernen wird ausgelöst."

# Strong-Refs auf laufende Schreib-Tasks (GC-Schutz)
_tasks: set[asyncio.Task] = set()


def move_label(move_id: int) -> str:
    return EMPTY_LABEL if not move_id else moves_de.get(move_id, f"#{move_id}")


def _on_task_done(task: asyncio.Task) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    err = task.exception()
    if err is not None:
        logger.error(f"Pokemon-Editor Schreib-Task fehlgeschlagen: {type(err)},{err}")
        logger.error("".join(traceback.format_exception(type(err), err, err.__traceback__)))


class PokemonEditorDialogMD:
    """Bearbeiten-Dialog. ``target`` wie ``Bizhawk.edit_pokemon`` erwartet."""

    def __init__(self, pokemon, edition: int, rando_data, target: dict, bizhawk,
                 client_id: str, on_dismiss: Callable[[], None] | None = None,
                 on_written: Callable[[], None] | None = None) -> None:
        self.pokemon = pokemon
        self.edition = int(edition)
        self.rando_data = rando_data
        self.target = target
        self.bizhawk = bizhawk
        self.client_id = client_id
        self._on_dismiss = on_dismiss
        self._on_written = on_written
        self._level_ev = None  # Debounce-Event der Level-Eingabe
        self.is_party = target.get("kind") == "party"
        self.dexnr = pokemon.dexnr

        start_level = clamp_level(getattr(pokemon, "lvl", 1)) or LEVEL_MIN
        self.level: int = start_level
        self.moves: list[int] = current_move_ids(pokemon)
        self.pool: list[int] = []
        self._busy = False
        self._menu: MDDropdownMenu | None = None
        self._move_buttons: list[MDButton] = []
        self._move_texts: list[MDButtonText] = []

        self._build()
        self._refresh_pool()
        self._refresh_preview()

    # -- Aufbau ----------------------------------------------------------------

    def _build(self) -> None:
        content = MDDialogContentContainer(orientation="vertical", spacing=dp(6),
                                           size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        level_row = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(56),
                                spacing=dp(6))
        level_row.add_widget(MDLabel(text="Level:", size_hint_x=None, width=dp(60),
                                     valign="center"))
        self.minus_button = MDIconButton(icon="minus", style="tonal",
                                         pos_hint={"center_y": .5},
                                         on_release=lambda _i: self.change_level(-1))
        level_row.add_widget(self.minus_button)
        self.level_field = MDTextField(text=str(self.level), input_filter="int",
                                       mode="outlined", size_hint_x=None, width=dp(90),
                                       pos_hint={"center_y": .5})
        self.level_field.bind(text=self._on_level_text, focus=self._on_level_focus)
        level_row.add_widget(self.level_field)
        self.plus_button = MDIconButton(icon="plus", style="tonal",
                                        pos_hint={"center_y": .5},
                                        on_release=lambda _i: self.change_level(1))
        level_row.add_widget(self.plus_button)
        level_row.add_widget(Widget())
        content.add_widget(level_row)

        for index in range(MOVE_SLOTS):
            text = MDButtonText(text=move_label(self.moves[index]))
            btn = MDButton(text, style="tonal", size_hint_x=1,
                           on_release=lambda inst, i=index: self._open_move_menu(i, inst))
            self._move_texts.append(text)
            self._move_buttons.append(btn)
            content.add_widget(btn)

        self.preview_label = MDLabel(text="", size_hint_y=None, height=dp(40))
        self.preview_label.role = "small"
        if self.is_party:
            content.add_widget(self.preview_label)

        hint = MDLabel(text=HINT_TEXT, size_hint_y=None, height=dp(40))
        hint.role = "small"
        content.add_widget(hint)

        self.status_label = MDLabel(text="", size_hint_y=None, height=dp(22))
        self.status_label.role = "small"
        content.add_widget(self.status_label)

        self.cancel_button = MDButton(MDButtonText(text="Abbrechen"), style="text",
                                      on_release=lambda _i: self.dismiss())
        self.write_button = MDButton(MDButtonText(text="Schreiben"), style="filled",
                                     on_release=lambda _i: self.write())
        self._dialog = MDDialog(
            MDDialogHeadlineText(text="Pokemon bearbeiten"),
            content,
            MDDialogButtonContainer(Widget(), self.cancel_button, self.write_button,
                                    spacing="10dp"),
            auto_dismiss=False,
        )
        self._dialog.bind(on_dismiss=self._handle_dismiss)

    def open(self) -> None:
        self._dialog.open()

    def dismiss(self) -> None:
        self._close_menu()
        self._dialog.dismiss()

    def _handle_dismiss(self, *_args) -> None:
        self._close_menu()
        if self._on_dismiss is not None:
            try:
                self._on_dismiss()
            except Exception:
                logger.error(f"Pokemon-Editor on_dismiss fehlgeschlagen: {traceback.format_exc()}")

    # -- Level / Pool / Vorschau -----------------------------------------------

    def change_level(self, delta: int) -> None:
        self.set_level(self.level + delta)
        self.level_field.text = str(self.level)

    def set_level(self, level: int) -> None:
        self.level = max(LEVEL_MIN, min(LEVEL_MAX, int(level)))
        self._refresh_pool()
        self._refresh_preview()

    def _on_level_text(self, _inst, value: str) -> None:
        # Entprellt: Pool/Vorschau erst nach kurzer Tipp-Pause neu berechnen
        if self._level_ev is not None:
            self._level_ev.cancel()
        self._level_ev = Clock.schedule_once(lambda _dt: self._apply_level_text(value), 0.3)

    def _apply_level_text(self, value: str) -> None:
        self._level_ev = None
        level = clamp_level(value)
        if level is None or level == self.level:
            return
        self.set_level(level)

    def _on_level_focus(self, _inst, focused: bool) -> None:
        if not focused:
            # Beim Verlassen normalisieren (leer/außerhalb des Bereichs)
            self.level_field.text = str(self.level)

    def _refresh_pool(self) -> None:
        """Pool für das aktuelle Level; gewählte Attacken bleiben enthalten."""
        try:
            self.pool = move_choices(self.edition, self.dexnr, self.level, self.moves,
                                     self.rando_data)
        except Exception:
            logger.error(f"Pokemon-Editor Pool-Berechnung fehlgeschlagen: {traceback.format_exc()}")
            self.pool = [m for m in self.moves if m]

    def _refresh_preview(self) -> None:
        if not self.is_party:
            return
        stats = preview_stats(self.edition, self.dexnr, self.level,
                              getattr(self.pokemon, "ivs", None),
                              getattr(self.pokemon, "evs", None),
                              getattr(self.pokemon, "nature", 0), self.rando_data)
        self.preview_label.text = (f"Neue Werte: {format_stats(stats)}" if stats
                                   else "Neue Werte: nicht berechenbar")

    # -- Attacken-Auswahl ------------------------------------------------------

    def _close_menu(self) -> None:
        if self._menu is not None:
            self._menu.dismiss()
            self._menu = None

    def _open_move_menu(self, index: int, caller) -> None:
        self._close_menu()
        entries: list[tuple[str, int]] = [(EMPTY_LABEL, 0)]
        entries += [(move_label(m), m) for m in self.pool]
        items = [{"text": text, "on_release": (lambda v=value: self.pick_move(index, v))}
                 for text, value in entries]
        self._menu = MDDropdownMenu(caller=caller, items=items, max_height=dp(300))
        self._menu.open()

    def pick_move(self, index: int, move_id: int) -> None:
        self._close_menu()
        if move_id and move_id in [m for i, m in enumerate(self.moves) if i != index]:
            self.status_label.text = "Attacke ist bereits gewählt."
            return
        self.moves[index] = int(move_id)
        self._move_texts[index].text = move_label(move_id)
        self.status_label.text = ""
        self._refresh_pool()

    # -- Schreiben -------------------------------------------------------------

    def write(self) -> None:
        if self._busy:
            return
        error = validate_moves(self.moves)
        if error is not None:
            self.status_label.text = error
            show_snackbar(error, level='error')
            return
        self._set_busy(True)
        edit = build_edit(self.level, self.moves)
        task = asyncio.create_task(self._do_write(edit))
        _tasks.add(task)
        task.add_done_callback(_on_task_done)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.write_button.disabled = busy
        self.cancel_button.disabled = busy

    async def _do_write(self, edit) -> None:
        try:
            from frontend.bh_dispatch import bh_submit
            # Watchdog: Backend hat 15 s pro Lua-Antwort und bis zu 3 Versuche
            success, msg = await asyncio.wait_for(bh_submit(
                self.bizhawk.edit_pokemon(self.client_id, self.target, edit, self.rando_data)), 60)
        except asyncio.CancelledError:
            self._set_busy(False)
            raise
        except asyncio.TimeoutError:
            logger.error(f"edit_pokemon({self.client_id}, {self.target}): keine Antwort in 60 s")
            self._set_busy(False)
            show_snackbar("Emulator antwortet nicht — Schreiben nicht bestätigt.", level='error')
            return
        except Exception as err:
            logger.error(f"edit_pokemon fehlgeschlagen: {type(err)},{err}")
            logger.error(traceback.format_exc())
            self._set_busy(False)
            show_snackbar("Schreiben fehlgeschlagen — Logs prüfen.", level='error')
            return
        logger.info(f"edit_pokemon({self.client_id}, {self.target}, lvl={edit.new_level}, "
                    f"moves={edit.moves}): success={success}, msg={msg}")
        self._set_busy(False)
        show_snackbar(msg or ("Gespeichert." if success else "Schreiben fehlgeschlagen."),
                      level='success' if success else 'error')
        if success:
            if self._on_written is not None:
                try:
                    self._on_written()
                except Exception:
                    logger.error(f"Pokemon-Editor on_written fehlgeschlagen: {traceback.format_exc()}")
            self.dismiss()
