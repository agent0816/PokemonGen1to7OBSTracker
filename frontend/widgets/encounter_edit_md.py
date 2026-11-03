"""KivyMD-Dialog zum manuellen Bearbeiten eines Encounters.

Controller-Klasse (kein Widget) mit einem ``MDDialog`` (Stil wie
``pokemon_editor_md.PokemonEditorDialogMD``). Gesendet wird ueber
``munchlax.edit_encounter`` — Munchlax laeuft auf eigenem Thread-Loop, daher
zwingend ueber ``munchlax.submit_cross_thread``. Die Diff-Logik
(``compute_changes``) ist als freie Funktion testbar.
"""
from __future__ import annotations

import asyncio
import traceback
from typing import Callable

from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.widget import Widget
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonIcon, MDButtonText
from kivymd.uix.dialog import (
    MDDialog, MDDialogButtonContainer, MDDialogContentContainer, MDDialogHeadlineText,
)
from kivymd.uix.label import MDIcon, MDLabel
from kivymd.uix.menu import MDDropdownMenu
from kivy.clock import Clock
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.selectioncontrol import MDCheckbox, MDSwitch
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText

from backend.classes.pokedex_db import PokedexDB
from backend.encounter_view import link_targets, locations_for_edition
from backend.logging_setup import get_logger
from backend.player_display import button_text, owner_label
from backend.pokedex_view import location_name, species_name
from frontend.widgets.md_common import confirm_dialog, show_snackbar

logger = get_logger(__name__, 'logs/encountermenu.log')

LEVEL_MIN = 1
LEVEL_MAX = 100
MAX_HITS = 15
SEND_TIMEOUT = 30

OUTCOME_OPTIONS: list[tuple[str, str]] = [
    ("gefangen", "caught"), ("erhalten", "obtained"),
    ("verpasst", "not_caught"), ("offen", "unknown"),
]
METHOD_OPTIONS: list[tuple[str, str]] = [
    ("wild", "wild"), ("Geschenk", "gift"), ("Statisch", "static"),
    ("Starter", "starter"), ("Token", "token"),
]
GENDER_OPTIONS: list[tuple[str, object]] = [
    ("männlich", False), ("weiblich", True), ("—", None),
]
# (Feld, Anzeigetext) der Flag-Checkboxen
FLAG_FIELDS: list[tuple[str, str]] = [
    ("shiny", "Shiny"),
    ("is_first", "Erster Encounter der Route"),
    ("is_dupes_skip", "Dupe übersprungen"),
    ("is_shiny_override", "Shiny-Ausnahme"),
    ("has_balls", "Hatte Bälle"),
]

# Strong-Refs auf laufende Sende-Tasks (GC-Schutz)
_tasks: set[asyncio.Task] = set()


def _on_task_done(task: asyncio.Task) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    err = task.exception()
    if err is not None:
        logger.error(f"Encounter-Edit Sende-Task fehlgeschlagen: {type(err)},{err}")
        logger.error("".join(traceback.format_exception(type(err), err, err.__traceback__)))


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def spawn_task(coro) -> asyncio.Task:
    """Startet einen Sende-Task mit Strong-Ref und Fehler-Logging."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_on_task_done)
    return task


async def send_munchlax_edit(munchlax, make_coro: Callable[[], object],
                             label: str) -> tuple[bool, str] | None:
    """Fuehrt eine Munchlax-Edit-Coroutine auf dem Munchlax-Loop aus (Timeout inklusive).

    ``make_coro`` baut die Coroutine erst hier. Rueckgabe ``(ok, grund)``; bei Timeout
    oder Fehler wird eine Snackbar gezeigt und ``None`` geliefert. CancelledError
    wird durchgereicht.
    """
    try:
        coro = make_coro()
        try:
            future = munchlax.submit_cross_thread(coro)
        except Exception:
            coro.close()
            raise
        # shield: ein Timeout hier bricht nur das Warten ab, nicht den Edit
        # auf dem Munchlax-Loop (sonst z.B. DB-Insert ohne encounter_sync).
        return await asyncio.wait_for(asyncio.shield(future), SEND_TIMEOUT)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logger.error(f"{label}: keine Antwort in {SEND_TIMEOUT} s")
        show_snackbar("Keine Antwort vom Backend — Änderung nicht bestätigt.", level='error')
    except Exception as err:
        logger.error(f"{label} fehlgeschlagen: {type(err)},{err}")
        logger.error(traceback.format_exc())
        show_snackbar("Speichern fehlgeschlagen — Logs prüfen.", level='error')
    return None


async def run_edit_request(munchlax, make_coro: Callable[[], object], label: str,
                           on_sent: Callable[[], None] | None = None) -> bool:
    """Sendet eine Edit-Coroutine, zeigt Ergebnis-Snackbars, ruft bei Erfolg ``on_sent``.

    Rueckgabe True nur bei erfolgreichem (vom Server akzeptiertem) Senden.
    """
    result = await send_munchlax_edit(munchlax, make_coro, label)
    if result is None:
        return False
    ok, reason = result
    if not ok:
        logger.info(f"{label}: nicht gesendet/angewendet: {reason}")
        show_snackbar(reason or "Änderung abgelehnt.", level='error')
        return False
    show_snackbar(reason or "Gespeichert.", level='success')
    if on_sent is not None:
        try:
            on_sent()
        except Exception:
            logger.error(f"{label} on_sent fehlgeschlagen: {traceback.format_exc()}")
    return True


def build_route_entries(edition: int, extra: int | None = None) -> list[tuple[str, int]]:
    """Routen-Auswahl (Name + Nr.) einer Edition inkl. Starter (-1) und ``extra``."""
    routes = {-1}
    routes.update(locations_for_edition(edition))
    if extra is not None:
        routes.add(extra)
    return [(f"{location_name(r, edition)} ({r})", r) for r in sorted(routes)]


def build_species_entries(species_de: dict) -> list[tuple[str, int]]:
    return [(f"{name} (#{nr})", int(nr)) for nr, name in sorted(species_de.items())
            if isinstance(nr, int) and nr > 0]


def entry_label(entries: list[tuple[str, int]], value: int | None) -> str:
    if value is None:
        return ""
    for text, val in entries:
        if val == value:
            return text
    return str(value)


def match_entries(entries: list[tuple[str, int]], query: str) -> list[tuple[str, int]]:
    """Treffer fuer die Filter-Eingabe (Name-Teilstring oder Nummer), max. MAX_HITS."""
    q = (query or "").strip().lower()
    if not q:
        return []
    hits = [(t, v) for t, v in entries if q in t.lower() or q == str(v)]
    return hits[:MAX_HITS]


def open_dropdown(caller, entries: list[tuple[str, object]], on_pick,
                  max_height: float = dp(260)) -> MDDropdownMenu | None:
    """Oeffnet ein Dropdown; ``None`` bei leerer Liste."""
    items = [{"text": text, "on_release": (lambda v=value: on_pick(v))}
             for text, value in entries]
    if not items:
        return None
    menu = MDDropdownMenu(caller=caller, items=items, max_height=max_height)
    menu.open()
    return menu


class EntryPicker:
    """Textfeld mit Treffer-Dropdown (Name oder Nummer eintippen), z.B. Spezies/Route."""

    def __init__(self, hint: str, entries: list[tuple[str, int]], value: int,
                 on_pick: Callable[[int], None]) -> None:
        self.entries = entries
        self.value = value
        self._on_pick = on_pick
        self._suspend = False
        self._menu: MDDropdownMenu | None = None
        self.field = MDTextField(MDTextFieldHintText(text=hint), mode="outlined",
                                 text=entry_label(entries, value))
        self.field.bind(text=self._on_text, focus=self._on_focus)

    def close_menu(self) -> None:
        if self._menu is not None:
            self._menu.dismiss()
            self._menu = None

    def set_value(self, value: int) -> None:
        """Setzt Wert und Anzeige ohne ``on_pick`` auszuloesen."""
        self.value = value
        self._suspend = True
        try:
            self.field.text = entry_label(self.entries, value)
        finally:
            self._suspend = False

    def _on_text(self, _inst, text: str) -> None:
        if self._suspend:
            return
        hits = match_entries(self.entries, text)
        self.close_menu()
        if not hits or text == entry_label(self.entries, self.value):
            return
        self._menu = open_dropdown(self.field, hits, self._pick)

    def _pick(self, value: int) -> None:
        self.close_menu()
        self.set_value(int(value))
        self._on_pick(int(value))

    def _on_focus(self, _inst, focused: bool) -> None:
        if focused:
            return
        # Treffer-Menue verzoegert schliessen, damit ein Tap auf einen Eintrag
        # (der den Fokus nimmt) noch durchkommt.
        Clock.schedule_once(lambda _dt: self.close_menu(), 0.3)
        # Unvollstaendige Eingabe verwerfen: Anzeige zurueck auf den gewaehlten Wert
        self.set_value(self.value)


def initial_state(row: dict) -> dict:
    """Ausgangswerte des Dialogs aus einer ``build_encounter_rows``-Zeile."""
    raw_female = row.get("female_raw", row.get("female"))
    lvl = _to_int(row.get("lvl"))
    return {
        "outcome": row.get("outcome") or "unknown",
        "dexnr": _to_int(row.get("dexnr")) or 0,
        "route": _to_int(row.get("route")) or 0,
        "method": row.get("method") or "wild",
        "female": None if raw_female is None else bool(raw_female),
        "shiny": bool(row.get("shiny")),
        "lvl": str(lvl) if lvl else "",
        "form": str(row.get("form") or ""),
        "is_first": bool(row.get("is_first")),
        "is_dupes_skip": bool(row.get("is_dupes_skip")),
        "is_shiny_override": bool(row.get("is_shiny_override")),
        "has_balls": bool(row.get("has_balls")),
        "dead": bool(row.get("dead")),
    }


def compute_changes(original: dict, current: dict) -> dict:
    """Nur geaenderte Felder als ``changes`` (Typen wie der Server sie erwartet).

    Wirft ``ValueError`` bei ungueltigem Level.
    """
    changes: dict = {}
    for key in ("outcome", "method"):
        if current[key] != original[key]:
            changes[key] = str(current[key])
    for key in ("dexnr", "route"):
        if current[key] != original[key]:
            changes[key] = int(current[key])
    for key in ("shiny", "is_first", "is_dupes_skip", "is_shiny_override",
                "has_balls", "dead"):
        if bool(current[key]) != bool(original[key]):
            changes[key] = int(bool(current[key]))
    if current["female"] != original["female"]:
        changes["female"] = current["female"]
    if str(current["form"]).strip() != str(original["form"]).strip():
        changes["form"] = str(current["form"]).strip()
    lvl_text = str(current["lvl"]).strip()
    if lvl_text != str(original["lvl"]).strip():
        level = _to_int(lvl_text)
        if level is None or not LEVEL_MIN <= level <= LEVEL_MAX:
            raise ValueError(f"Level muss zwischen {LEVEL_MIN} und {LEVEL_MAX} liegen.")
        changes["lvl"] = level
    return changes


class EncounterEditDialogMD:
    """Bearbeiten-Dialog fuer eine Encounter-Zeile (``build_encounter_rows``-Format)."""

    def __init__(self, row: dict, munchlax, species_de: dict,
                 on_sent: Callable[[], None] | None = None,
                 on_dismiss: Callable[[], None] | None = None,
                 groups: list[dict] | None = None) -> None:
        self.row = row
        # Soullink-Gruppen (group_soullink-Format); None = kein Soullink-Modus
        self.groups = groups
        self.munchlax = munchlax
        self.species_de = species_de
        self._on_sent = on_sent
        self._on_dismiss = on_dismiss
        self.personality = row.get("personality")
        self.owner = str(row.get("owner") or "")
        self.root_owner = PokedexDB.owner_root(self.owner)
        self.edition = _to_int(row.get("edition")) or 0
        self.locks: set[str] = set(row.get("locks") or ())
        self.deleted = bool(row.get("deleted"))
        self.original = initial_state(row)
        self.state = dict(self.original)
        self._busy = False
        self._suspend = False
        self._menu: MDDropdownMenu | None = None
        self._choice_texts: dict[str, MDButtonText] = {}
        self._pickers: dict[str, EntryPicker] = {}
        self._flag_widgets: dict[str, object] = {}
        self.unlock_buttons: dict[str, MDButton] = {}
        self._route_entries: list[tuple[str, int]] = build_route_entries(
            self.edition, self.state["route"])
        self._species_entries: list[tuple[str, int]] = build_species_entries(species_de)
        self._build()

    # -- Aufbau ----------------------------------------------------------------

    def _block(self, field: str, label: str, control, control_height: float) -> MDBoxLayout:
        """Label-Kopf (mit Schloss + 'Automatik' bei gesperrtem Feld) + Steuerelement."""
        locked = field in self.locks
        head_h = dp(40) if locked else dp(26)
        box = MDBoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2),
                          height=head_h + dp(2) + control_height)
        head = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=head_h,
                           spacing=dp(6))
        lbl = MDLabel(text=label, role="small", valign="center")
        head.add_widget(lbl)
        if locked:
            head.add_widget(self._lock_icon())
            head.add_widget(self._unlock_button(field))
        box.add_widget(head)
        box.add_widget(control)
        return box

    @staticmethod
    def _lock_icon() -> MDIcon:
        return MDIcon(icon="lock", size_hint=(None, None), size=(dp(16), dp(16)),
                      pos_hint={"center_y": .5}, theme_font_size="Custom", font_size=dp(14))

    def _unlock_button(self, field: str) -> MDButton:
        btn = MDButton(MDButtonIcon(icon="restore"), MDButtonText(text="Automatik"),
                       style="text", pos_hint={"center_y": .5},
                       on_release=lambda _i, f=field: self.unlock(f))
        self.unlock_buttons[field] = btn
        return btn

    def _choice_button(self, key: str, options: list[tuple[str, object]]) -> MDButton:
        text = MDButtonText(text=self._choice_label(options, self.state[key]))
        self._choice_texts[key] = text
        return MDButton(text, style="tonal", size_hint_x=1,
                        on_release=lambda inst, k=key, o=options: self._open_choice_menu(k, o, inst))

    @staticmethod
    def _choice_label(options: list[tuple[str, object]], value) -> str:
        for label, val in options:
            if val == value:
                return label
        return str(value)

    def _pick_field(self, key: str, hint: str, entries: list[tuple[str, int]]) -> MDTextField:
        picker = EntryPicker(hint, entries, self.state[key],
                             lambda v, k=key: self._set_state(k, v))
        self._pickers[key] = picker
        return picker.field

    def _build(self) -> None:
        content = MDDialogContentContainer(orientation="vertical", spacing=dp(6),
                                           size_hint_y=None)
        content.height = min(dp(460), max(dp(200), Window.height - dp(240)))
        body = MDBoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6),
                           padding=(0, 0, dp(8), 0))
        body.bind(minimum_height=body.setter("height"))

        # Kopf: Spezies + Route + Spieler
        species = species_name(self.original["dexnr"], self.species_de)
        route_text = location_name(self.original["route"], self.edition)
        head = MDLabel(text=f"{species} - {route_text}", bold=True, size_hint_y=None,
                       height=dp(26))
        self.head_label = head
        body.add_widget(head)
        try:
            names = self.munchlax.snapshot_player_names()
        except Exception:
            names = {}
        owner = MDLabel(text=button_text(owner_label(self.owner, names)), role="small",
                        size_hint_y=None,
                        height=dp(20))
        body.add_widget(owner)
        self.group_button = None
        if self.groups is not None and not self.deleted:
            self.group_button = MDButton(
                MDButtonIcon(icon="link-variant"), MDButtonText(text="Gruppe…"), style="tonal",
                on_release=lambda inst: self.open_group_menu(inst))
            body.add_widget(self.group_button)
        if self.deleted:
            gone = MDLabel(text="Dieser Encounter ist gelöscht.", role="small",
                           size_hint_y=None, height=dp(20))
            body.add_widget(gone)

        body.add_widget(self._block("outcome", "Status",
                                    self._choice_button("outcome", OUTCOME_OPTIONS), dp(48)))
        body.add_widget(self._block("method", "Methode",
                                    self._choice_button("method", METHOD_OPTIONS), dp(48)))
        body.add_widget(self._block("dexnr", "Spezies (Name oder Nr. eintippen)",
                                    self._pick_field("dexnr", "Spezies", self._species_entries),
                                    dp(56)))
        body.add_widget(self._block("route", "Route (Name oder Nr. eintippen)",
                                    self._pick_field("route", "Route", self._route_entries),
                                    dp(56)))

        self.level_field = MDTextField(MDTextFieldHintText(text="Level (1-100)"),
                                       mode="outlined", input_filter="int",
                                       text=self.state["lvl"])
        self.level_field.bind(text=lambda _i, v: self._set_state("lvl", v))
        body.add_widget(self._block("lvl", "Level", self.level_field, dp(56)))

        body.add_widget(self._block("female", "Geschlecht",
                                    self._choice_button("female", GENDER_OPTIONS), dp(48)))

        self.form_field = MDTextField(MDTextFieldHintText(text="Form"), mode="outlined",
                                      text=self.state["form"])
        self.form_field.bind(text=lambda _i, v: self._set_state("form", v))
        body.add_widget(self._block("form", "Form", self.form_field, dp(56)))

        for field, text in FLAG_FIELDS:
            body.add_widget(self._flag_row(field, text, MDCheckbox))
        body.add_widget(self._flag_row("dead", "Tot", MDSwitch))

        scroll = MDScrollView(do_scroll_x=False)
        scroll.add_widget(body)
        content.add_widget(scroll)
        self.scroll = scroll

        if self.deleted:
            self.delete_button = MDButton(MDButtonText(text="Wiederherstellen"), style="text",
                                          on_release=lambda _i: self.restore())
        else:
            self.delete_button = MDButton(MDButtonText(text="Löschen"), style="text",
                                          on_release=lambda _i: self.ask_delete())
        self.cancel_button = MDButton(MDButtonText(text="Abbrechen"), style="text",
                                      on_release=lambda _i: self.dismiss())
        self.save_button = MDButton(MDButtonText(text="Speichern"), style="filled",
                                    on_release=lambda _i: self.save())
        self._dialog = MDDialog(
            MDDialogHeadlineText(text="Encounter bearbeiten"),
            content,
            MDDialogButtonContainer(self.delete_button, Widget(), self.cancel_button,
                                    self.save_button, spacing="10dp"),
            auto_dismiss=False,
        )
        self._dialog.bind(on_dismiss=self._handle_dismiss)

    def _flag_row(self, field: str, text: str, widget_cls) -> MDBoxLayout:
        locked = field in self.locks
        row = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44),
                          spacing=dp(6))
        control = widget_cls(size_hint=(None, None), pos_hint={"center_y": .5})
        if widget_cls is MDCheckbox:
            control.size = (dp(40), dp(40))
        control.active = bool(self.state[field])
        # bind(active=...) — on_active feuert bei Property-Aenderung nicht
        control.bind(active=lambda _i, v, f=field: self._set_state(f, bool(v)))
        self._flag_widgets[field] = control
        row.add_widget(control)
        row.add_widget(MDLabel(text=text, role="small", valign="center"))
        if locked:
            row.add_widget(self._lock_icon())
            row.add_widget(self._unlock_button(field))
        return row

    # -- Zustand ---------------------------------------------------------------

    def _set_state(self, key: str, value) -> None:
        if self._suspend:
            return
        self.state[key] = value

    def set_value(self, key: str, value) -> None:
        """Setzt einen Wert samt UI-Anzeige (Dropdown-Auswahl, Tests)."""
        self.state[key] = value
        if key in self._choice_texts:
            options = {"outcome": OUTCOME_OPTIONS, "method": METHOD_OPTIONS,
                       "female": GENDER_OPTIONS}[key]
            self._choice_texts[key].text = self._choice_label(options, value)
        elif key in self._pickers:
            self._pickers[key].set_value(int(value))
        elif key in self._flag_widgets:
            self._flag_widgets[key].active = bool(value)
        elif key == "lvl":
            self.level_field.text = str(value)
        elif key == "form":
            self.form_field.text = str(value)

    # -- Dropdowns / Picker ------------------------------------------------------

    def _close_menu(self) -> None:
        if self._menu is not None:
            self._menu.dismiss()
            self._menu = None
        for picker in self._pickers.values():
            picker.close_menu()

    def _open_menu(self, caller, entries: list[tuple[str, object]], on_pick) -> None:
        self._close_menu()
        self._menu = open_dropdown(caller, entries, on_pick)

    def _open_choice_menu(self, key: str, options: list[tuple[str, object]], caller) -> None:
        self._open_menu(caller, options, lambda v, k=key: self._pick_choice(k, v))

    def _pick_choice(self, key: str, value) -> None:
        self._close_menu()
        self.set_value(key, value)

    # -- Lifecycle ---------------------------------------------------------------

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
                logger.error(f"Encounter-Edit on_dismiss fehlgeschlagen: {traceback.format_exc()}")

    # -- Aktionen ----------------------------------------------------------------

    def save(self) -> None:
        if self._busy:
            return
        try:
            changes = compute_changes(self.original, self.state)
        except ValueError as err:
            show_snackbar(str(err), level='error')
            return
        if not changes:
            show_snackbar("Keine Änderungen.", level='info')
            self.dismiss()
            return
        self._send(changes=changes)

    def unlock(self, field: str) -> None:
        """Hebt die Sperre eines Feldes auf (eigener Request, sofort)."""
        try:
            pending = compute_changes(self.original, self.state)
        except ValueError:
            pending = {"_ungueltig": True}
        if pending:
            # Unlock schliesst den Dialog — ungespeicherte Eingaben nicht still verwerfen
            show_snackbar("Erst speichern oder abbrechen, dann Automatik.", level='info')
            return
        self._send(unlock=[field])

    def ask_delete(self) -> None:
        if self._busy:
            return
        confirm_dialog("Encounter löschen?",
                       "Der Encounter wird ausgeblendet (die Automatik legt ihn nicht neu an). "
                       "Er kann über \"Gelöschte zeigen\" wiederhergestellt werden.",
                       on_confirm=lambda: self._send(delete=True), confirm_text="Löschen")

    def restore(self) -> None:
        self._send(delete=False)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for btn in (self.save_button, self.cancel_button, self.delete_button,
                    *self.unlock_buttons.values(),
                    *([self.group_button] if self.group_button is not None else [])):
            btn.disabled = busy

    def _send(self, **kwargs) -> None:
        if self._busy:
            return
        if self.munchlax is None or self.personality is None:
            show_snackbar("Bearbeiten nicht möglich: keine Verbindung zum Backend.", level='error')
            return
        self._set_busy(True)
        spawn_task(self._do_send(kwargs))

    async def _do_send(self, kwargs: dict) -> None:
        await self._run(lambda: self.munchlax.edit_encounter(
            self.personality, self.owner, **kwargs),
            f"edit_encounter({self.personality}, {self.owner})")

    async def _run(self, make_coro: Callable[[], object], label: str) -> None:
        try:
            ok = await run_edit_request(self.munchlax, make_coro, label, self._on_sent)
        except asyncio.CancelledError:
            self._set_busy(False)
            raise
        self._set_busy(False)
        if ok:
            self.dismiss()

    # -- Gruppen-Aktionen (nur Soullink) -----------------------------------------

    def _own_group(self) -> dict | None:
        """Gruppe, in der dieser Encounter Mitglied ist (PV + Owner-Root)."""
        for group in self.groups or []:
            for mem in group.get("members", []):
                if (mem.get("personality") == self.personality
                        and mem.get("root_owner") == self.root_owner):
                    return group
        return None

    def open_group_menu(self, caller=None) -> None:
        """Menue mit den Gruppen-Aktionen (je nach Mitgliedschaft)."""
        caller = caller or self.group_button
        if self._own_group() is not None:
            entries: list[tuple[str, object]] = [
                ("In andere Gruppe verschieben…", "move"), ("Aus Gruppe lösen", "unlink")]
        else:
            entries = [("Zu Gruppe hinzufügen…", "move")]
        self._open_menu(caller, entries, lambda v, c=caller: self._group_action(v, c))

    def _group_action(self, action: str, caller) -> None:
        self._close_menu()
        if self._busy:
            return
        if self._has_pending():
            show_snackbar("Erst speichern oder abbrechen, dann Gruppe ändern.", level='info')
            return
        if action == "unlink":
            confirm_dialog("Aus Gruppe lösen?",
                           "Das Pokemon bleibt als Encounter erhalten, ist aber keiner "
                           "Gruppe mehr zugeordnet.",
                           on_confirm=lambda: self.send_link("unlink"), confirm_text="Lösen")
            return
        own = self._own_group()
        targets = link_targets(self.groups or [], self.root_owner,
                               exclude_link_id=None if own is None else own.get("link_id"))
        if not targets:
            show_snackbar("Keine passende Gruppe vorhanden.", level='info')
            return
        entries = [(text, link_id) for text, link_id in targets]
        self._open_menu(caller, entries, lambda v: self._pick_target(v))

    def _pick_target(self, link_id) -> None:
        self._close_menu()
        self.send_link("move", target_link_id=link_id)

    def _has_pending(self) -> bool:
        try:
            return bool(compute_changes(self.original, self.state))
        except ValueError:
            return True

    def send_link(self, op: str, **kwargs) -> None:
        """Link-Operation fuer dieses Pokemon (move/unlink)."""
        if self._busy:
            return
        if self.munchlax is None or self.personality is None:
            show_snackbar("Bearbeiten nicht möglich: keine Verbindung zum Backend.", level='error')
            return
        self._set_busy(True)
        spawn_task(self._run(lambda: self.munchlax.edit_soullink_link(
            op, personality=self.personality, owner=self.owner, **kwargs),
            f"edit_soullink_link({op}, {self.personality}, {self.owner})"))
