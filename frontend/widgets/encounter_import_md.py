"""KivyMD-Dialog: Encounter aus bereits gefangenen Pokemon (Team + PC-Boxen) uebernehmen.

Schritt 1: Spieler waehlen, Boxen einlesen (oder gespeicherte Boxen nutzen).
Schritt 2: Vorschau nach Route gruppiert (uebernehmen ja/nein, erstes Encounter
der Route), dann ``munchlax.import_encounters``. Die reine Auswahl-Logik steckt in
``ImportSelection`` (ohne Kivy testbar); Backend-Logik in ``backend/encounter_import``.
"""
from __future__ import annotations

import asyncio
import os
import traceback
from typing import Callable

from kivy.metrics import dp
from kivy.uix.widget import Widget
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText, MDIconButton
from kivymd.uix.dialog import (
    MDDialog, MDDialogButtonContainer, MDDialogContentContainer, MDDialogHeadlineText,
)
from kivymd.uix.fitimage import FitImage
from kivymd.uix.label import MDLabel
from kivymd.uix.selectioncontrol import MDCheckbox

from backend.encounter_import import (
    ImportCandidate, RouteGroup, build_encounters, collect_candidates, group_by_route,
    own_trainer,
)
from backend.logging_setup import get_logger
from backend.player_display import labels_for_slots
from backend.pokedex_view import location_name, species_name
from frontend.box_reader import read_boxes_for_player
from frontend.widgets.encounter_create_md import ChoiceField, _DialogBase, _dialog_content, labeled
from frontend.widgets.encounter_edit_md import SEND_TIMEOUT, send_munchlax_edit, spawn_task
from frontend.widgets.md_common import show_snackbar

logger = get_logger(__name__, 'logs/encountermenu.log')

SPRITE_WIDTH = dp(48)
ROW_HEIGHT = dp(56)


class ImportSelection:
    """Auswahlzustand der Vorschau: welche PVs uebernommen werden, welche je Route 'erste' ist.

    Invarianten: hoechstens ein 'erstes' pro Route; 'erstes' ist immer auch ausgewaehlt,
    nie ein Geschenk (Fremder OT) und nie in einer Route mit vorhandener Erstbegegnung.
    """

    def __init__(self, groups: list[RouteGroup]) -> None:
        self.groups = groups
        self._cands: dict[int, ImportCandidate] = {}
        self._group_of: dict[int, RouteGroup] = {}
        for group in groups:
            for cand in group.candidates:
                self._cands[cand.pv] = cand
                self._group_of[cand.pv] = group
        self.included: set[int] = set(self._cands)
        # route -> pv des ersten Encounters
        self.first: dict[int, int] = {g.route: g.default_first_pv for g in groups
                                      if g.default_first_pv is not None}

    def can_be_first(self, pv: int) -> bool:
        cand = self._cands.get(pv)
        group = self._group_of.get(pv)
        if cand is None or group is None:
            return False
        return cand.method == "wild" and not cand.foreign_ot and not group.has_existing_first

    def is_first(self, pv: int) -> bool:
        group = self._group_of.get(pv)
        return group is not None and self.first.get(group.route) == pv

    def toggle_include(self, pv: int, on: bool) -> None:
        if pv not in self._cands:
            return
        if on:
            self.included.add(pv)
            return
        self.included.discard(pv)
        if self.is_first(pv):
            del self.first[self._group_of[pv].route]

    def set_first(self, pv: int, on: bool = True) -> bool:
        """Setzt/loest 'erstes' fuer ``pv``. Gibt False zurueck, wenn nicht moeglich."""
        if not self.can_be_first(pv):
            return False
        route = self._group_of[pv].route
        if not on:
            if self.first.get(route) == pv:
                del self.first[route]
            return True
        self.included.add(pv)
        self.first[route] = pv
        return True

    def selected_pvs(self) -> set[int]:
        return set(self.included)

    def first_pvs(self) -> set[int]:
        return {pv for pv in self.first.values() if pv in self.included}


def summary_text(count: int, skipped: dict[str, int]) -> str:
    """'N übernehmen · übersprungen: x Eier, y bereits erfasst'."""
    text = f"{count} übernehmen"
    parts = []
    if skipped.get("egg"):
        parts.append(f"{skipped['egg']} Eier")
    if skipped.get("known"):
        parts.append(f"{skipped['known']} bereits erfasst")
    if skipped.get("no_pv"):
        parts.append(f"{skipped['no_pv']} ohne PV")
    if parts:
        text += " · übersprungen: " + ", ".join(parts)
    return text


class EncounterImportDialogMD(_DialogBase):
    """Aus dem Spiel (Team + Boxen) Encounter nachtragen."""

    headline = "Aus Spiel übernehmen"
    ok_text = "Übernehmen"

    def __init__(self, munchlax, pl: dict, species_de: dict, bizhawk=None, citra=None,
                 sprite_source: Callable[[dict], str] | None = None,
                 on_sent: Callable[[], None] | None = None,
                 on_dismiss: Callable[[], None] | None = None) -> None:
        super().__init__(munchlax, on_sent, on_dismiss)
        self.pl = pl if pl is not None else {}
        self.species_de = species_de
        self.bizhawk = bizhawk
        self.citra = citra
        self._sprite_source = sprite_source
        try:
            self.owned: list[int] = list(munchlax.owned_player_ids())
        except Exception:
            logger.error(f"Import-Dialog: eigene Slots nicht lesbar: {traceback.format_exc()}")
            self.owned = []
        self.pid: int | None = self.owned[0] if self.owned else None
        self.ctx: dict | None = None
        self.selection: ImportSelection | None = None
        self.skipped: dict[str, int] = {"egg": 0, "known": 0, "no_pv": 0}
        self.step: int = 1
        self._ready = False  # Uebernehmen erst nach geladener Vorschau
        self._pid_field: ChoiceField | None = None
        self._rows: dict[int, tuple[MDCheckbox, MDIconButton | None]] = {}
        self._suspend = False
        self._preview_pid: int | None = None
        self._build()

    # -- Aufbau -------------------------------------------------------------------

    def _assemble(self, content: MDDialogContentContainer) -> None:
        """Wie _DialogBase, plus "Zurück" (nur in der Vorschau aktiv)."""
        self.back_button = MDButton(MDButtonText(text="Zurück"), style="text",
                                    on_release=lambda _i: self.go_back())
        self.back_button.disabled = True
        self.cancel_button = MDButton(MDButtonText(text="Abbrechen"), style="text",
                                      on_release=lambda _i: self.dismiss())
        self.save_button = MDButton(MDButtonText(text=self.ok_text), style="filled",
                                    on_release=lambda _i: self.save())
        self._dialog = MDDialog(
            MDDialogHeadlineText(text=self.headline), content,
            MDDialogButtonContainer(self.back_button, Widget(), self.cancel_button,
                                    self.save_button, spacing="10dp"),
            auto_dismiss=False,
        )
        self._dialog.bind(on_dismiss=self._handle_dismiss)

    def go_back(self) -> None:
        """Von der Vorschau zurück zu Schritt 1 (Spieler/Boxen); Auswahl verwerfen."""
        if self._busy or self.step != 2:
            return
        self.selection = None
        self.ctx = None
        self._preview_pid = None
        self._show_step1()

    def _build(self) -> None:
        content, self.body, self.scroll = _dialog_content()
        self._assemble(content)
        self.save_button.disabled = True
        self._show_step1()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.save_button.disabled = busy or not self._ready
        self.cancel_button.disabled = busy
        self.back_button.disabled = busy or self.step != 2
        for btn in getattr(self, "_step1_buttons", []):
            btn.disabled = busy
        if self._pid_field is not None:
            self._pid_field.button.disabled = busy

    def _label(self, text: str, height: float = dp(24), role: str = "small") -> MDLabel:
        return MDLabel(text=text, role=role, valign="center", size_hint_y=None, height=height)

    def _show_step1(self) -> None:
        self.step = 1
        self._ready = False
        self.save_button.disabled = True
        self.back_button.disabled = True
        self.body.clear_widgets()
        self._step1_buttons: list[MDButton] = []
        if not self.owned:
            self.body.add_widget(self._label("Kein lokaler Spieler vorhanden.", dp(48)))
            return
        try:
            names = self.munchlax.snapshot_player_names()
        except Exception:
            names = {}
        labels = labels_for_slots(self.owned, names)
        if len(self.owned) > 1:
            self._pid_field = ChoiceField([(labels[p], p) for p in self.owned], self.pid,
                                          self.set_player)
            self.body.add_widget(labeled("Spieler", self._pid_field.button, dp(48)))
        else:
            self.body.add_widget(self._label(labels.get(self.pid, f"Spieler {self.pid}"),
                                             dp(32), "medium"))
        hint = MDLabel(
            text=("Liest Team und PC-Boxen des Spielers und trägt bereits gefangene Pokemon "
                  "als Encounter nach. Bereits erfasste und Eier werden übersprungen."),
            role="small", size_hint_y=None, adaptive_height=True)
        self.body.add_widget(hint)
        self.status_label = MDLabel(text="", role="small", size_hint_y=None,
                                    adaptive_height=True)
        self.body.add_widget(self.status_label)
        self.read_button = MDButton(MDButtonText(text="Boxen einlesen"), style="filled",
                                    on_release=lambda _i: self.start_read())
        self.cached_button = MDButton(MDButtonText(text="Mit gespeicherten Boxen"),
                                      style="tonal",
                                      on_release=lambda _i: self.start_cached())
        self._step1_buttons = [self.read_button, self.cached_button]
        self.body.add_widget(self.read_button)
        self.body.add_widget(self.cached_button)

    def set_player(self, pid) -> None:
        self.pid = pid
        if self._pid_field is not None:
            self._pid_field.set_value(pid)

    def _close_menus(self) -> None:
        if self._pid_field is not None:
            self._pid_field.close()

    def _set_status(self, text: str) -> None:
        if self.step == 1 and hasattr(self, "status_label"):
            self.status_label.text = text

    # -- Schritt 1: Einlesen ---------------------------------------------------------

    def start_read(self) -> None:
        """Boxen vom Emulator lesen, cachen, dann Vorschau laden."""
        if self._busy or self.pid is None:
            return
        self._set_busy(True)
        spawn_task(self._read_flow(self.pid, read=True))

    def start_cached(self) -> None:
        """Ohne Emulator-Lesen mit den gecachten Boxen fortfahren."""
        if self._busy or self.pid is None:
            return
        self._set_busy(True)
        spawn_task(self._read_flow(self.pid, read=False))

    async def _cross(self, coro):
        """Coroutine auf dem Munchlax-Loop ausfuehren (Timeout); Fehler werden geworfen."""
        try:
            future = self.munchlax.submit_cross_thread(coro)
        except Exception:
            coro.close()
            raise
        return await asyncio.wait_for(future, SEND_TIMEOUT)

    async def _read_flow(self, pid: int, read: bool) -> None:
        try:
            if read:
                self._set_status("Boxen werden eingelesen ...")
                boxes, error = None, None
                try:
                    boxes, error = await read_boxes_for_player(
                        self.pl, self.citra, self.bizhawk, pid)
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    logger.error(f"Import: Boxen lesen fehlgeschlagen: {traceback.format_exc()}")
                    error = f"Lesen fehlgeschlagen ({type(err).__name__})"
                if not boxes:
                    # Leere Liste nicht als Erfolg werten: wuerde den Box-Cache
                    # (und den Stand bei Arceus) mit nichts ueberschreiben.
                    if boxes is not None:
                        error = error or "keine Boxen gelesen"
                    logger.info(f"Import: Boxen fuer Spieler {pid} nicht lesbar: {error}")
                    self._set_status(
                        f"Boxen nicht lesbar: {error or 'unbekannter Fehler'}. "
                        "Mit gespeicherten Boxen fortfahren möglich.")
                    self._set_busy(False)
                    return
                await self._cross(self.munchlax.update_boxes(pid, boxes))
            await self._load_preview(pid)
        except asyncio.CancelledError:
            self._set_busy(False)
            raise
        except Exception as err:
            logger.error(f"Import-Dialog Einlesen fehlgeschlagen: {type(err)},{err}")
            logger.error(traceback.format_exc())
            self._set_status("Einlesen fehlgeschlagen - Logs prüfen.")
            show_snackbar("Einlesen fehlgeschlagen - Logs prüfen.", level='error')
        self._set_busy(False)

    async def _load_preview(self, pid: int) -> None:
        ctx = await self._cross(self.munchlax.encounter_import_context(pid))
        if not ctx:
            logger.info(f"Import Spieler {pid}: kein Kontext (Edition/DB fehlt)")
            self._set_status("Kein Import möglich: Edition oder Datenbank nicht verfügbar.")
            return
        self.ctx = ctx
        # Vorschau gehoert zu genau diesem Spieler (Import nutzt ihn, nicht self.pid)
        self._preview_pid = pid
        team = list(self.munchlax.snapshot_sorted_teams().get(pid) or [])[:6]
        boxes = self.munchlax.snapshot_boxes(pid)
        everything = team + [p for box in boxes for p in (box or [])]
        own = own_trainer(everything)
        cands, self.skipped = collect_candidates(
            team, boxes, own, set(ctx.get("existing_pvs") or ()))
        logger.info(
            f"Import-Vorschau Spieler {pid}: owner={ctx.get('owner')} "
            f"edition={ctx.get('edition')} eigener OT={own} Team={len(team)} "
            f"Box-Pokemon={len(everything) - len(team)} Kandidaten={len(cands)} "
            f"uebersprungen={self.skipped}")
        if not cands and self.skipped.get("no_pv") and not self.skipped.get("known"):
            logger.info(f"Import Spieler {pid}: nur Pokemon ohne PV (Gen 1/2) - abgebrochen")
            self._set_status("Gen 1/2 nicht unterstützt: Diese Spiele liefern keine "
                             "eindeutige PV für den Abgleich.")
            return
        groups = group_by_route(cands, set(ctx.get("routes_with_first") or ()))
        self.selection = ImportSelection(groups)
        self._show_step2()

    # -- Schritt 2: Vorschau -----------------------------------------------------------

    def _show_step2(self) -> None:
        self.step = 2
        self.back_button.disabled = False
        self._ready = bool(self.selection and self.selection.groups)
        self.body.clear_widgets()
        self._rows = {}
        self.summary_label = self._label("", dp(28), "medium")
        self.body.add_widget(self.summary_label)
        sel = self.selection
        if not sel or not sel.groups:
            self.body.add_widget(self._label(
                "Nichts zu übernehmen: alles bereits erfasst oder keine Pokemon gefunden.",
                dp(48)))
            self.summary_label.text = summary_text(0, self.skipped)
            self.save_button.disabled = True
            return
        edition = int(self.ctx["edition"])
        for group in sel.groups:
            title = location_name(group.route, edition)
            if group.has_existing_first:
                title += " (hat schon Erstbegegnung)"
            self.body.add_widget(self._label(title, dp(32), "medium"))
            for cand in group.candidates:
                self.body.add_widget(self._build_row(cand, edition))
        self._refresh()
        self.save_button.disabled = False

    def _build_row(self, cand: ImportCandidate, edition: int) -> MDBoxLayout:
        row = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=ROW_HEIGHT,
                          spacing=dp(6))
        box = MDCheckbox(size_hint=(None, None), size=(dp(40), dp(40)),
                         pos_hint={"center_y": .5}, active=True)
        # bind(active=...) - on_active feuert bei Property-Aenderung nicht
        box.bind(active=lambda _i, v, pv=cand.pv: self._on_include(pv, v))
        row.add_widget(box)
        source = self._sprite(cand, edition)
        if source:
            row.add_widget(FitImage(source=source, fit_mode="contain", size_hint=(None, 1),
                                    width=SPRITE_WIDTH))
        else:
            row.add_widget(MDBoxLayout(size_hint_x=None, width=SPRITE_WIDTH))
        name = species_name(cand.dexnr, self.species_de)
        if cand.shiny:
            name += " (Shiny)"
        info = MDBoxLayout(orientation="vertical", padding=(0, dp(4), 0, dp(4)))
        info.add_widget(MDLabel(text=f"{name}  Lv. {cand.lvl}", role="medium",
                                size_hint_y=None, height=dp(22), valign="center"))
        detail = cand.source
        if cand.foreign_ot:
            detail += "  |  Fremder OT: Geschenk"
        info.add_widget(MDLabel(text=detail, role="small", size_hint_y=None,
                                height=dp(18), valign="center"))
        row.add_widget(info)
        star = None
        if self.selection.can_be_first(cand.pv):
            star = MDIconButton(icon="star-outline", style="standard",
                                pos_hint={"center_y": .5},
                                on_release=lambda _i, pv=cand.pv: self.toggle_first(pv))
            row.add_widget(star)
        else:
            row.add_widget(MDBoxLayout(size_hint_x=None, width=dp(48)))
        self._rows[cand.pv] = (box, star)
        return row

    def _sprite(self, cand: ImportCandidate, edition: int) -> str:
        if self._sprite_source is None:
            return ""
        try:
            source = self._sprite_source({
                "dexnr": cand.dexnr, "edition": edition, "shiny": cand.shiny,
                "female": bool(cand.female), "form": cand.form, "is_open": False})
        except Exception:
            logger.debug(f"Import: Sprite-Pfad fehlgeschlagen: {traceback.format_exc()}")
            return ""
        return source if source and os.path.exists(source) else ""

    def _refresh(self) -> None:
        """Widgets aus dem Auswahlzustand spiegeln (ohne Handler-Rueckkopplung)."""
        sel = self.selection
        if sel is None:
            return
        self._suspend = True
        try:
            for pv, (box, star) in self._rows.items():
                want = pv in sel.included
                if box.active != want:
                    box.active = want
                if star is not None:
                    star.icon = "star" if sel.is_first(pv) else "star-outline"
        finally:
            self._suspend = False
        self.summary_label.text = summary_text(len(sel.included), self.skipped)

    # -- Interaktion (auch von Tests genutzt) ----------------------------------------------

    def _on_include(self, pv: int, active: bool) -> None:
        if self._suspend or self.selection is None:
            return
        self.selection.toggle_include(pv, bool(active))
        self._refresh()

    def toggle_include(self, pv: int, on: bool) -> None:
        if self.selection is None:
            return
        self.selection.toggle_include(pv, on)
        self._refresh()

    def toggle_first(self, pv: int) -> None:
        """Stern-Klick: erstes setzen; nochmal klicken loest es wieder."""
        sel = self.selection
        if sel is None:
            return
        if not sel.set_first(pv, not sel.is_first(pv)):
            show_snackbar("Dieses Pokemon kann nicht das erste Encounter sein.", level='error')
        self._refresh()

    # -- Senden ---------------------------------------------------------------------------

    def save(self) -> None:
        if self._busy or self.step != 2 or self.selection is None or self.ctx is None:
            return
        sel = self.selection
        encounters = build_encounters(sel.groups, sel.selected_pvs(), sel.first_pvs(),
                                      str(self.ctx["owner"]), int(self.ctx["edition"]))
        if not encounters:
            show_snackbar("Nichts ausgewählt.", level='error')
            return
        self._set_busy(True)
        spawn_task(self._import(encounters))

    async def _import(self, encounters: list[dict]) -> None:
        pid = self._preview_pid
        try:
            result = await send_munchlax_edit(
                self.munchlax, lambda: self.munchlax.import_encounters(pid, encounters),
                f"import_encounters({pid}, {len(encounters)})")
        except asyncio.CancelledError:
            self._set_busy(False)
            raise
        self._set_busy(False)
        if result is None:
            return
        ok, count, reason = result
        if not ok:
            logger.info(f"Import abgelehnt: {reason}")
            show_snackbar(reason or "Import abgelehnt.", level='error')
            return
        show_snackbar(reason or f"{count} Encounter übernommen.", level='success')
        if self._on_sent is not None:
            try:
                self._on_sent()
            except Exception:
                logger.error(f"Import on_sent fehlgeschlagen: {traceback.format_exc()}")
        self.dismiss()
