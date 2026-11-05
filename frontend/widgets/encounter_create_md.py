"""KivyMD-Dialoge zum manuellen Anlegen eines Encounters bzw. einer Soullink-Gruppe.

``EncounterCreateDialogMD``: Spieler (nur lokale Slots) -> Quelle (Team / Box /
letzter Gegner / Freitext) -> Route, Methode, Status, Level. Gesendet wird ueber
``munchlax.edit_encounter(..., create=...)``.
``NewGroupDialogMD`` (nur Host): pro erwartetem Spieler einen Encounter waehlen,
mindestens zwei -> ``munchlax.edit_soullink_link("create", members=[...])``.
Sende-Muster/Picker kommen aus ``encounter_edit_md`` (keine Duplikate).
"""
from __future__ import annotations

import asyncio
import traceback
from typing import Callable

from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.widget import Widget
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.dialog import (
    MDDialog, MDDialogButtonContainer, MDDialogContentContainer, MDDialogHeadlineText,
)
from kivymd.uix.label import MDLabel
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText

from backend.classes.pokedex_db import PokedexDB
from backend.encounter_edit import new_manual_personality, opponent_pokemon, pokemon_gender_form
from backend.logging_setup import get_logger
from backend.player_display import labels_for_slots
from backend.pokedex_view import location_name, species_name
from frontend.widgets.encounter_edit_md import (
    LEVEL_MAX, LEVEL_MIN, METHOD_OPTIONS, OUTCOME_OPTIONS, EntryPicker, build_route_entries,
    build_species_entries, open_dropdown, run_edit_request, spawn_task, _to_int,
)
from frontend.widgets.md_common import show_snackbar

logger = get_logger(__name__, 'logs/encountermenu.log')

SOURCE_OPTIONS: list[tuple[str, str]] = [
    ("Aus Team", "team"), ("Aus Box", "box"), ("Letzter Gegner", "opponent"),
    ("Freitext", "free"),
]


def has_real_pv(pokemon) -> bool:
    """Echte PV vorhanden (Gen 1/2 liefern keine; 0/None gelten als fehlend)."""
    pv = getattr(pokemon, "personality", None)
    return isinstance(pv, int) and not isinstance(pv, bool) and pv > 0


def is_real_pokemon(pokemon) -> bool:
    """Kein leerer Slot und kein Ei."""
    if pokemon is None:
        return False
    dexnr = getattr(pokemon, "dexnr", 0)
    return isinstance(dexnr, int) and dexnr > 0


def pokemon_label(pokemon, species_de: dict, prefix: str = "") -> str:
    """'Slot: Art (Spitzname) Lv. N' fuer Auswahllisten."""
    name = species_name(pokemon.dexnr, species_de)
    nick = str(getattr(pokemon, "nickname", "") or "").strip()
    if nick and nick.lower() != name.lower():
        name = f"{name} ({nick})"
    lvl = _to_int(getattr(pokemon, "lvl", None))
    text = f"{name} Lv. {lvl}" if lvl else name
    if not has_real_pv(pokemon):
        text += " (keine PV)"
    return f"{prefix}{text}"


def build_create_payload(state: dict, pokemon, edition: int | None
                         ) -> tuple[int, dict]:
    """``(personality, create)`` aus dem Dialog-Zustand. Wirft ``ValueError`` mit Klartext."""
    if edition is None:
        raise ValueError("Edition unbekannt - Spiel erst verbinden.")
    if state.get("route") is None:
        raise ValueError("Bitte eine Route wählen.")
    create: dict = {
        "edition": int(edition), "route": int(state["route"]),
        "method": str(state["method"]), "outcome": str(state["outcome"]),
    }
    lvl_text = str(state.get("lvl") or "").strip()
    if lvl_text:
        level = _to_int(lvl_text)
        if level is None or not LEVEL_MIN <= level <= LEVEL_MAX:
            raise ValueError(f"Level muss zwischen {LEVEL_MIN} und {LEVEL_MAX} liegen.")
        create["lvl"] = level
    if state["source"] == "free":
        dexnr = _to_int(state.get("dexnr")) or 0
        if dexnr <= 0:
            raise ValueError("Bitte eine Spezies wählen.")
        create["dexnr"] = dexnr
        return new_manual_personality(), create
    if pokemon is None:
        raise ValueError("Bitte ein Pokemon wählen.")
    if not has_real_pv(pokemon):
        raise ValueError("Keine PV verfügbar - bitte Freitext nutzen.")
    create["dexnr"] = int(pokemon.dexnr)
    create["shiny"] = int(bool(getattr(pokemon, "shiny", False)))
    female, form = pokemon_gender_form(pokemon)
    if female is not None:
        create["female"] = female
    if form:
        create["form"] = form
    return int(pokemon.personality), create


class ChoiceField:
    """Button mit Dropdown-Auswahl (Label, Wert)."""

    def __init__(self, entries: list[tuple[str, object]], value: object,
                 on_pick: Callable[[object], None], placeholder: str = "wählen") -> None:
        self.entries = entries
        self.value = value
        self._on_pick = on_pick
        self._placeholder = placeholder
        self._menu: MDDropdownMenu | None = None
        self.text = MDButtonText(text=self._label())
        self.button = MDButton(self.text, style="tonal", size_hint_x=1,
                               on_release=lambda inst: self._open(inst))

    def _label(self) -> str:
        for label, val in self.entries:
            if val == self.value:
                return label
        return self._placeholder

    def close(self) -> None:
        if self._menu is not None:
            self._menu.dismiss()
            self._menu = None

    def set_value(self, value: object) -> None:
        self.value = value
        self.text.text = self._label()

    def _open(self, caller) -> None:
        self.close()
        self._menu = open_dropdown(caller, self.entries, self._pick)

    def _pick(self, value: object) -> None:
        self.close()
        self.set_value(value)
        self._on_pick(value)


def labeled(label: str, control, control_height: float) -> MDBoxLayout:
    """Kleines Label ueber einem Steuerelement."""
    box = MDBoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2),
                      height=dp(24) + dp(2) + control_height)
    box.add_widget(MDLabel(text=label, role="small", valign="center", size_hint_y=None,
                           height=dp(24)))
    box.add_widget(control)
    return box


def _dialog_content() -> tuple[MDDialogContentContainer, MDBoxLayout, MDScrollView]:
    content = MDDialogContentContainer(orientation="vertical", spacing=dp(6), size_hint_y=None)
    content.height = min(dp(460), max(dp(200), Window.height - dp(240)))
    body = MDBoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6),
                       padding=(0, 0, dp(8), 0))
    body.bind(minimum_height=body.setter("height"))
    scroll = MDScrollView(do_scroll_x=False)
    scroll.add_widget(body)
    content.add_widget(scroll)
    return content, body, scroll


class _DialogBase:
    """Gemeinsames Geruest: Dialog, Busy-Zustand, Senden mit Ergebnis-Feedback."""

    headline = ""
    ok_text = "Anlegen"

    def __init__(self, munchlax, on_sent: Callable[[], None] | None,
                 on_dismiss: Callable[[], None] | None) -> None:
        self.munchlax = munchlax
        self._on_sent = on_sent
        self._on_dismiss = on_dismiss
        self._busy = False
        self._dialog: MDDialog | None = None

    def _assemble(self, content: MDDialogContentContainer) -> None:
        self.cancel_button = MDButton(MDButtonText(text="Abbrechen"), style="text",
                                      on_release=lambda _i: self.dismiss())
        self.save_button = MDButton(MDButtonText(text=self.ok_text), style="filled",
                                    on_release=lambda _i: self.save())
        self._dialog = MDDialog(
            MDDialogHeadlineText(text=self.headline), content,
            MDDialogButtonContainer(Widget(), self.cancel_button, self.save_button,
                                    spacing="10dp"),
            auto_dismiss=False,
        )
        self._dialog.bind(on_dismiss=self._handle_dismiss)

    # Unterklassen
    def save(self) -> None:  # pragma: no cover - ueberschrieben
        raise NotImplementedError

    def _close_menus(self) -> None:
        pass

    def open(self) -> None:
        self._dialog.open()

    def dismiss(self) -> None:
        self._close_menus()
        self._dialog.dismiss()

    def _handle_dismiss(self, *_args) -> None:
        self._close_menus()
        if self._on_dismiss is not None:
            try:
                self._on_dismiss()
            except Exception:
                logger.error(f"Dialog on_dismiss fehlgeschlagen: {traceback.format_exc()}")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.save_button.disabled = busy
        self.cancel_button.disabled = busy

    def _send(self, make_coro: Callable[[], object], label: str) -> None:
        if self._busy:
            return
        if self.munchlax is None:
            show_snackbar("Bearbeiten nicht möglich: keine Verbindung zum Backend.", level='error')
            return
        self._set_busy(True)
        spawn_task(self._run(make_coro, label))

    async def _run(self, make_coro: Callable[[], object], label: str) -> None:
        try:
            ok = await run_edit_request(self.munchlax, make_coro, label, self._on_sent)
        except asyncio.CancelledError:
            self._set_busy(False)
            raise
        self._set_busy(False)
        if ok:
            self.dismiss()


class EncounterCreateDialogMD(_DialogBase):
    """Neuen Encounter anlegen (aus Team, Box, letztem Gegner oder Freitext)."""

    headline = "Encounter hinzufügen"

    def __init__(self, munchlax, pl: dict, species_de: dict,
                 on_sent: Callable[[], None] | None = None,
                 on_dismiss: Callable[[], None] | None = None) -> None:
        super().__init__(munchlax, on_sent, on_dismiss)
        self.pl = pl if pl is not None else {}
        self.species_de = species_de
        try:
            self.owned: list[int] = list(munchlax.owned_player_ids())
        except Exception:
            logger.error(f"Create-Dialog: eigene Slots nicht lesbar: {traceback.format_exc()}")
            self.owned = []
        self.state: dict = {
            "pid": self.owned[0] if self.owned else None, "source": "team",
            "route": None, "method": "wild", "outcome": "caught", "lvl": "", "dexnr": 0,
            "box": 0,
        }
        self.chosen = None  # gewaehltes Pokemon (Team/Box)
        self._species_entries = build_species_entries(species_de)
        self._fields: dict[str, ChoiceField] = {}
        self._route_picker: EntryPicker | None = None
        self._species_picker: EntryPicker | None = None
        self._build()
        self._rebuild_route()
        self._rebuild_source()

    # -- Daten --------------------------------------------------------------------

    def _edition(self) -> int | None:
        pid = self.state["pid"]
        try:
            return _to_int(self.munchlax.snapshot_editions().get(pid))
        except Exception:
            logger.error(f"Create-Dialog: Edition nicht lesbar: {traceback.format_exc()}")
            return None

    def _owner(self) -> str:
        name = str(self.pl.get("your_name", "") or "")
        if not name or self.state["pid"] is None:
            return ""
        return PokedexDB.build_owner(name, str(self.state["pid"]))

    def team_entries(self) -> list[tuple[str, int]]:
        try:
            team = self.munchlax.snapshot_sorted_teams().get(self.state["pid"]) or []
        except Exception:
            logger.error(f"Create-Dialog: Team nicht lesbar: {traceback.format_exc()}")
            return []
        return [(pokemon_label(p, self.species_de, f"Slot {i + 1}: "), i)
                for i, p in enumerate(list(team)[:6]) if is_real_pokemon(p)]

    def _boxes(self) -> list:
        return self.munchlax.snapshot_boxes(self.state["pid"])

    def box_entries(self) -> list[tuple[str, int]]:
        return [(f"Box {i + 1}", i) for i in range(len(self._boxes()))]

    def box_slot_entries(self) -> list[tuple[str, int]]:
        boxes = self._boxes()
        idx = self.state["box"]
        if not 0 <= idx < len(boxes):
            return []
        return [(pokemon_label(p, self.species_de, f"Platz {i + 1}: "), i)
                for i, p in enumerate(boxes[idx] or []) if is_real_pokemon(p)]

    def last_opponent(self) -> dict | None:
        """Zuletzt erkannter Wild-Gegner des gewaehlten Spielers (Snapshot)."""
        try:
            entry = self.munchlax.snapshot_last_opponents().get(self.state["pid"])
        except Exception:
            logger.error(f"Create-Dialog: letzter Gegner nicht lesbar: {traceback.format_exc()}")
            return None
        # Edition gewechselt (anderes Spiel geladen) → alter Gegner passt nicht
        if entry and entry.get("edition") not in (None, self._edition()):
            return None
        return entry

    def _team_pokemon(self, idx: int):
        team = self.munchlax.snapshot_sorted_teams().get(self.state["pid"]) or []
        return team[idx] if 0 <= idx < len(team) else None

    def _box_pokemon(self, slot: int):
        boxes = self._boxes()
        idx = self.state["box"]
        if not 0 <= idx < len(boxes):
            return None
        box = boxes[idx] or []
        return box[slot] if 0 <= slot < len(box) else None

    # -- Aufbau -------------------------------------------------------------------

    def _build(self) -> None:
        content, body, self.scroll = _dialog_content()
        self.body = body

        try:
            names = self.munchlax.snapshot_player_names()
        except Exception:
            names = {}
        labels = labels_for_slots(self.owned, names)
        player_entries = [(labels[pid], pid) for pid in self.owned]
        self._fields["pid"] = ChoiceField(player_entries, self.state["pid"], self.set_player,
                                          "kein lokaler Spieler")
        body.add_widget(labeled("Spieler", self._fields["pid"].button, dp(48)))
        self._fields["source"] = ChoiceField(SOURCE_OPTIONS, self.state["source"],
                                             self.set_source)
        body.add_widget(labeled("Quelle", self._fields["source"].button, dp(48)))

        # Quelle-abhaengiger Bereich (wird bei Spieler-/Quellenwechsel neu gebaut)
        self._src_holder = MDBoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6))
        self._src_holder.bind(minimum_height=self._src_holder.setter("height"))
        body.add_widget(self._src_holder)

        self._route_holder = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                         spacing=dp(2), height=dp(82))
        body.add_widget(self._route_holder)

        self._fields["method"] = ChoiceField(METHOD_OPTIONS, self.state["method"],
                                             lambda v: self.set_value("method", v))
        body.add_widget(labeled("Methode", self._fields["method"].button, dp(48)))
        self._fields["outcome"] = ChoiceField(OUTCOME_OPTIONS, self.state["outcome"],
                                              lambda v: self.set_value("outcome", v))
        body.add_widget(labeled("Status", self._fields["outcome"].button, dp(48)))

        self.level_field = MDTextField(MDTextFieldHintText(text="Level (1-100)"),
                                       mode="outlined", input_filter="int", text="")
        self.level_field.bind(text=lambda _i, v: self.state.__setitem__("lvl", v))
        body.add_widget(labeled("Level", self.level_field, dp(56)))
        self._assemble(content)

    def _rebuild_route(self) -> None:
        if self._route_picker is not None:
            self._route_picker.close_menu()
        self._route_holder.clear_widgets()
        edition = self._edition()
        entries = build_route_entries(edition) if edition is not None else []
        keep = self.state["route"]
        if keep is not None and keep not in [v for _t, v in entries]:
            self.state["route"] = keep = None
        self._route_picker = EntryPicker("Route", entries, keep,
                                         lambda v: self.state.__setitem__("route", v))
        label = "Route (Name oder Nr. eintippen)"
        if edition is None:
            label = "Route (Edition unbekannt - Spiel verbinden)"
            self._route_picker.field.disabled = True
        self._route_holder.add_widget(MDLabel(text=label, role="small", valign="center",
                                              size_hint_y=None, height=dp(24)))
        self._route_holder.add_widget(self._route_picker.field)
        self._route_picker.field.size_hint_y = None
        self._route_picker.field.height = dp(56)

    def _rebuild_source(self) -> None:
        self._close_menus(sources_only=True)
        self._src_holder.clear_widgets()
        self._species_picker = None
        for key in ("pokemon", "box", "slot"):
            self._fields.pop(key, None)
        source = self.state["source"]
        if source == "team":
            entries = self.team_entries()
            field = ChoiceField(entries, None, lambda i: self.pick_team(i),
                                "Pokemon wählen" if entries else "Team leer / nicht verbunden")
            self._fields["pokemon"] = field
            self._src_holder.add_widget(labeled("Pokemon aus dem Team", field.button, dp(48)))
        elif source == "opponent":
            self._add_opponent_info()
        elif source == "box":
            boxes = self.box_entries()
            bfield = ChoiceField(boxes, self.state["box"] if boxes else None,
                                 self.set_box, "keine Box geladen")
            self._fields["box"] = bfield
            self._src_holder.add_widget(labeled("Box", bfield.button, dp(48)))
            self._add_slot_field()
        else:
            self._species_picker = EntryPicker(
                "Spezies", self._species_entries, self.state["dexnr"] or None,
                lambda v: self.state.__setitem__("dexnr", v))
            self._src_holder.add_widget(labeled("Spezies (Name oder Nr. eintippen)",
                                                self._species_picker.field, dp(56)))
        self._src_holder.height = self._src_holder.minimum_height

    def _add_opponent_info(self) -> None:
        """Quelle "Letzter Gegner": Anzeige + Vorbelegung von Pokemon/Level/Route."""
        entry = self.last_opponent()
        pokemon = opponent_pokemon(entry)
        if pokemon is None:
            text = "Noch kein Wild-Gegner erkannt (Spiel verbunden?)."
        else:
            text = pokemon_label(pokemon, self.species_de)
            route = entry.get("route")
            if route is not None:
                text += f" - {location_name(int(route), self._edition())}"
        # Hinweise im Label statt Snackbar: _rebuild_source laeuft bei jedem
        # Quellen-/Spielerwechsel, Snackbars wuerden sich wiederholen
        notes: list[str] = []
        if pokemon is not None and not has_real_pv(pokemon):
            self.chosen = None
            notes.append("ohne PV - bitte Freitext nutzen")
        elif pokemon is not None:
            self.chosen = pokemon
            lvl = _to_int(getattr(pokemon, "lvl", None))
            self.set_value("lvl", str(lvl) if lvl else "")
            route = entry.get("route")
            if route is not None and self._route_picker is not None and int(route) in [
                    v for _t, v in self._route_picker.entries]:
                self.set_route(int(route))
            else:
                notes.append("Route nicht zuordenbar - bitte wählen")
        if notes:
            text += f"  ({'; '.join(notes)})"
        label = MDLabel(text=text, role="small", valign="center", size_hint_y=None)
        label.bind(texture_size=lambda inst, ts: setattr(inst, "height", max(dp(32), ts[1])))
        self._src_holder.add_widget(label)

    def _add_slot_field(self) -> None:
        old = self._fields.pop("slot", None)
        if old is not None:
            old.close()
            self._src_holder.remove_widget(old.button.parent)
        entries = self.box_slot_entries()
        field = ChoiceField(entries, None, lambda i: self.pick_box(i),
                            "Pokemon wählen" if entries else "Box leer")
        self._fields["slot"] = field
        self._src_holder.add_widget(labeled("Pokemon aus der Box", field.button, dp(48)))

    def _close_menus(self, sources_only: bool = False) -> None:
        for field in self._fields.values():
            field.close()
        for picker in (self._species_picker,) + (() if sources_only else (self._route_picker,)):
            if picker is not None:
                picker.close_menu()

    # -- Zustand (auch von Tests genutzt) -------------------------------------------

    def set_value(self, key: str, value) -> None:
        self.state[key] = value
        field = self._fields.get(key)
        if field is not None:
            field.set_value(value)
        if key == "lvl":
            self.level_field.text = str(value)

    def set_route(self, route: int) -> None:
        self.state["route"] = int(route)
        if self._route_picker is not None:
            self._route_picker.set_value(int(route))

    def set_species(self, dexnr: int) -> None:
        """Freitext: Spezies setzen (Anzeige + Zustand)."""
        self.state["dexnr"] = int(dexnr)
        if self._species_picker is not None:
            self._species_picker.set_value(int(dexnr))

    def set_player(self, pid) -> None:
        self.state["pid"] = pid
        self.chosen = None
        self._fields["pid"].set_value(pid)
        self._rebuild_route()
        self._rebuild_source()

    def set_source(self, source: str) -> None:
        self.state["source"] = source
        self.chosen = None
        self._fields["source"].set_value(source)
        self._rebuild_source()

    def set_box(self, idx: int) -> None:
        self.state["box"] = int(idx)
        self.chosen = None
        self._fields["box"].set_value(int(idx))
        self._add_slot_field()

    def pick_team(self, idx: int) -> None:
        self._choose(self._team_pokemon(int(idx)), self._fields.get("pokemon"), idx)

    def pick_box(self, slot: int) -> None:
        self._choose(self._box_pokemon(int(slot)), self._fields.get("slot"), slot)

    def _choose(self, pokemon, field: ChoiceField | None, idx) -> None:
        if pokemon is None:
            show_snackbar("Pokemon nicht mehr vorhanden.", level='error')
            return
        if not has_real_pv(pokemon):
            # Ohne echte PV waere der Encounter nicht eindeutig zuordenbar
            if field is not None:
                field.set_value(None)
            self.chosen = None
            show_snackbar("Keine PV verfügbar - bitte Freitext nutzen.", level='error')
            return
        self.chosen = pokemon
        if field is not None:
            field.set_value(idx)
        lvl = _to_int(getattr(pokemon, "lvl", None))
        self.set_value("lvl", str(lvl) if lvl else "")

    # -- Senden ---------------------------------------------------------------------

    def save(self) -> None:
        if self._busy:
            return
        owner = self._owner()
        if self.state["pid"] is None:
            show_snackbar("Kein lokaler Spieler vorhanden.", level='error')
            return
        if not owner:
            show_snackbar("Spielername fehlt (Einstellungen).", level='error')
            return
        try:
            personality, create = build_create_payload(self.state, self.chosen, self._edition())
        except ValueError as err:
            show_snackbar(str(err), level='error')
            return
        self._send(lambda: self.munchlax.edit_encounter(personality, owner, create=create),
                   f"edit_encounter(create, {personality}, {owner})")


def member_label(enc: dict, species_de: dict) -> str:
    """'Spezies - Route' eines Encounters (fuer die Gruppen-Auswahl)."""
    edition = _to_int(enc.get("edition")) or 0
    route = _to_int(enc.get("route")) or 0
    return (f"{species_name(enc.get('dexnr', 0), species_de)} - "
            f"{location_name(route, edition)}")


class NewGroupDialogMD(_DialogBase):
    """Host: neue Soullink-Gruppe aus je einem Encounter pro Spieler."""

    headline = "Neue Gruppe"
    ok_text = "Gruppe anlegen"

    def __init__(self, munchlax, encounters: list[dict], species_de: dict,
                 on_sent: Callable[[], None] | None = None,
                 on_dismiss: Callable[[], None] | None = None) -> None:
        super().__init__(munchlax, on_sent, on_dismiss)
        self.species_de = species_de
        self._encounters = [e for e in encounters if not e.get("deleted", 0)]
        self.owners: list[str] = self._expected_owners()
        # Wurzel-Owner -> gewaehlter Encounter (Index in self._encounters)
        self.selection: dict[str, int | None] = {o: None for o in self.owners}
        self._fields: dict[str, ChoiceField] = {}
        self._build()

    def _expected_owners(self) -> list[str]:
        cfg = self.munchlax.snapshot_soullink_config()
        owners = [str(o) for o in (cfg.get("expected_owners") or []) if o]
        if not owners:
            roots = {PokedexDB.owner_root(str(e.get("owner", "") or ""))
                     for e in self._encounters}
            owners = sorted(r for r in roots if r)
        return owners

    def _entries_for(self, root: str) -> list[tuple[str, int]]:
        return [(member_label(e, self.species_de), i) for i, e in enumerate(self._encounters)
                if PokedexDB.owner_root(str(e.get("owner", "") or "")) == root
                and e.get("personality") is not None]

    def _build(self) -> None:
        content, body, self.scroll = _dialog_content()
        body.add_widget(MDLabel(
            text="Pro Spieler einen Encounter wählen (mindestens zwei).", role="small",
            size_hint_y=None, height=dp(24)))
        for root in self.owners:
            field = ChoiceField(self._entries_for(root), None,
                                lambda i, r=root: self.select(r, i), "nicht dabei")
            self._fields[root] = field
            body.add_widget(labeled(root, field.button, dp(48)))
        self._assemble(content)

    def _close_menus(self) -> None:
        for field in self._fields.values():
            field.close()

    def select(self, root: str, idx: int | None) -> None:
        self.selection[root] = idx
        field = self._fields.get(root)
        if field is not None:
            field.set_value(idx)

    def members(self) -> list[dict]:
        result = []
        for root in self.owners:
            idx = self.selection.get(root)
            if idx is None:
                continue
            enc = self._encounters[idx]
            result.append({"personality": int(enc["personality"]),
                           "owner": str(enc.get("owner", "") or "")})
        return result

    def save(self) -> None:
        if self._busy:
            return
        members = self.members()
        if len(members) < 2:
            show_snackbar("Mindestens zwei Mitglieder wählen.", level='error')
            return
        self._send(lambda: self.munchlax.edit_soullink_link("create", members=members),
                   f"edit_soullink_link(create, {len(members)} Mitglieder)")
