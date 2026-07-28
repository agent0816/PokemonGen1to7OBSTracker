"""TotalWipeBanner — Popup bei soullink_rule_violation type=total_wipe.

Zeigt vier Handlungs-Optionen:
- "Letzten Snapshot laden" — nutzt SnapshotManager, restore neuester Eintrag
  (Legacy-Rollback ohne neuen Run: schreibt Save auf ``original_path``).
- "Neu randomisieren + Snapshot laden" — legt einen neuen Randomizer-Run
  an und mappt anschliessend den letzten Snapshot-Save auf die neue Run-ROM
  (Gen 1-5). Fuer Content-Creator: kein Projekt-Restart, aber frisches
  Randomizer-Seed.
- "Anderen Snapshot wählen" — schließt Popup, User navigiert zu NuzlockeMenu.
- "Bei Null starten" — archiviert encounters-Tabelle, aktive DB-Reset.

Aktions-Buttons sperren sich gegenseitig, solange ein Flow läuft — sonst
könnte "Bei Null starten" (finalisiert Run im Executor-Thread) parallel zu
"Neu randomisieren + Snapshot laden" (Randomize + close_pokedex_db im
Main-Thread) auf denselben Zustand zugreifen. Es gibt keine Lock-
Koordination zwischen diesen Pfaden.
"""

import asyncio
import traceback
from pathlib import Path

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup

from backend.logging_setup import get_logger
from backend.snapshot_manager import SnapshotManager

logger = get_logger(__name__, './logs/wipe_banner.log')


class TotalWipeBanner(Popup):
    def __init__(self, munchlax, configsave, bh: dict | None = None,
                  on_pick_other=None, bizhawk=None,
                  rnd: dict | None = None, pl: dict | None = None,
                  rem: dict | None = None,
                  **kwargs):
        super().__init__(**kwargs)
        self.munchlax = munchlax
        self.configsave = configsave
        self.bh = bh or {}
        self.bizhawk = bizhawk
        self.rnd = rnd
        self.pl = pl
        # rem als explizite Konstruktor-Referenz statt Durchgriff auf
        # munchlax.rem — passt zum Config-Dict-Muster (Referenzen sind
        # In-Place aktualisiert, wer sie braucht bekommt sie direkt).
        self.rem = rem
        self._on_pick_other = on_pick_other

        self.title = "TOTAL WIPE — Run-Ende erkannt"
        self.size_hint = (0.7, 0.6)
        self.auto_dismiss = False

        content = BoxLayout(orientation="vertical", padding="10dp", spacing="8dp")
        content.add_widget(Label(
            text="Alle Pokemon sind gefallen.\nBitte Aktion wählen:",
            font_size="16sp",
        ))

        btn_row = BoxLayout(orientation="vertical", spacing="6dp")
        self.latest_button = Button(
            text="Letzten Snapshot laden",
            on_press=lambda *_: self._load_latest_snapshot(),
        )
        btn_row.add_widget(self.latest_button)
        self.combo_button = None
        if self.rnd is not None and self.pl is not None:
            self.combo_button = Button(
                text="Neu randomisieren + Snapshot laden",
                on_press=lambda *_: self._randomize_and_load_snapshot(),
            )
            btn_row.add_widget(self.combo_button)
        self.other_button = Button(
            text="Anderen Snapshot wählen",
            on_press=lambda *_: self._pick_other(),
        )
        btn_row.add_widget(self.other_button)
        self.reset_button = Button(
            text="Bei Null starten (Encounter archivieren)",
            on_press=lambda *_: self._reset_run(),
        )
        btn_row.add_widget(self.reset_button)
        # Vollreset: unumkehrbar, deshalb rot markiert + Bestätigungs-Popup
        # bevor die Aktion tatsächlich läuft.
        self.full_reset_button = Button(
            text="Alles frisch starten (kompletter Reset — DB leeren)",
            background_color=(0.7, 0.2, 0.2, 1),
            on_press=lambda *_: self._confirm_full_reset(),
        )
        btn_row.add_widget(self.full_reset_button)
        # Test-/Notausgang: schliesst Popup ohne Aktion. Wipe-State bleibt
        # bestehen, Popup kann bei naechstem Wipe-Signal wieder aufpoppen.
        self.cancel_button = Button(
            text="Abbrechen (nur Test — Wipe-State bleibt)",
            on_press=lambda *_: self.dismiss(),
        )
        btn_row.add_widget(self.cancel_button)
        content.add_widget(btn_row)

        self.status_label = Label(text="", size_hint_y=None, height="30dp")
        content.add_widget(self.status_label)

        self.content = content

    def _action_buttons(self) -> list:
        """Alle Action-Buttons als Liste — combo_button ist optional."""
        buttons = [self.latest_button, self.other_button, self.reset_button,
                    self.full_reset_button]
        if self.combo_button is not None:
            buttons.append(self.combo_button)
        return buttons

    def _lock_actions(self):
        for btn in self._action_buttons():
            btn.disabled = True

    def _unlock_actions(self):
        for btn in self._action_buttons():
            btn.disabled = False

    def _schedule_unlock(self, delay: float = 0):
        """Reenable auf Kivy-Main-Thread — nur wenn Popup noch offen ist,
        sonst irrelevant. delay=3 fuer Erfolgsflows die per verzoegertem
        Dismiss den User Status lesen lassen (Reenable sonst waehrend
        Lesepause klickbar → Race)."""
        def _apply(_dt):
            self._unlock_actions()
        Clock.schedule_once(_apply, delay)

    def _schedule_finish(self, dismiss_delay):
        """Sammelt Reenable + optionalen Dismiss in EINEN Kivy-Clock-Callback,
        damit zwischen Unlock und Dismiss garantiert kein Input-Event
        verarbeitet wird (verhindert Race beim Lesepause-Dismiss).

        ``dismiss_delay`` = ``None`` → Popup bleibt offen (Fehlerfall),
        Sofort-Unlock. ``float`` → dismiss + unlock nach ``delay`` Sekunden.
        """
        if dismiss_delay is None:
            def _finish(_dt):
                self._unlock_actions()
            Clock.schedule_once(_finish, 0)
        else:
            def _finish(_dt):
                self._unlock_actions()
                self.dismiss()
            Clock.schedule_once(_finish, dismiss_delay)

    def _snapshot_manager(self) -> SnapshotManager:
        session_path = str(self.configsave)
        session_name = Path(session_path).name or "default"
        return SnapshotManager(session_path, session_name,
                                 bh_config=self.bh, munchlax=self.munchlax)

    def _set_status(self, text: str):
        def _apply(_dt):
            self.status_label.text = text
        Clock.schedule_once(_apply, 0)

    def _load_latest_snapshot(self):
        self._lock_actions()
        self.status_label.text = "Snapshot wird geladen..."
        asyncio.create_task(self._load_latest_snapshot_async())

    async def _load_latest_snapshot_async(self):
        dismiss_delay = None
        try:
            try:
                self.munchlax.close_pokedex_db()
                sm = self._snapshot_manager()
                loop = asyncio.get_event_loop()
                entries = await loop.run_in_executor(None, sm.list)
                if not entries:
                    self._set_status("Keine Snapshots vorhanden")
                    return
                latest = entries[0]
                snap_id = latest.get("id", "")
                ok = await loop.run_in_executor(None, sm.restore, snap_id)
            except Exception as err:
                logger.error(f"latest snapshot load failed: {type(err)},{err}")
                logger.error(f"{traceback.format_exc()}")
                self._set_status(f"Fehler: {err}")
                return
            if ok:
                self._set_status(f"Geladen: {snap_id}")
                dismiss_delay = 0
            else:
                self._set_status("Restore fehlgeschlagen")
        finally:
            self._schedule_finish(dismiss_delay)

    def _pick_other(self):
        # Kein Lock nötig: _pick_other schliesst das Popup sofort synchron.
        if callable(self._on_pick_other):
            try:
                self._on_pick_other()
            except Exception as err:
                logger.error(f"on_pick_other callback failed: {err}")
                logger.error(traceback.format_exc())
        self.dismiss()

    def _reset_run(self):
        self._lock_actions()
        self.status_label.text = "Encounters werden archiviert..."
        asyncio.create_task(self._reset_run_async())

    async def _reset_run_async(self):
        dismiss_delay = None
        try:
            try:
                loop = asyncio.get_event_loop()
                archive = await loop.run_in_executor(None, self.munchlax.archive_active_run)
            except Exception as err:
                logger.error(f"reset_run failed: {err}")
                logger.error(traceback.format_exc())
                self._set_status(f"Fehler: {err}")
                return
            if archive:
                self._set_status(f"Encounters archiviert als {archive}")
                dismiss_delay = 0
            else:
                self._set_status("Archivieren fehlgeschlagen")
        finally:
            self._schedule_finish(dismiss_delay)

    def _confirm_full_reset(self):
        """Bestätigungs-Popup vor unumkehrbarem DB-Wipe. Host-Only-Check:
        Reset feuert nur, wenn dieser Tracker der Host ist (start_server=True);
        Non-Host-Clients sehen eine Fehler-Statuszeile."""
        rem = self.rem or {}
        is_host = bool(rem.get("start_server"))
        if not is_host:
            self._set_status("Fehler: Nur der Host darf Session zurücksetzen")
            return

        confirm = Popup(
            title="Kompletter Reset — irreversibel",
            size_hint=(0.6, 0.4),
            auto_dismiss=False,
        )
        box = BoxLayout(orientation="vertical", padding="10dp", spacing="8dp")
        box.add_widget(Label(
            text=(
                "Alle Encounter, Teams, Bag-Items und Soullink-Links werden\n"
                "gelöscht (auf allen verbundenen Trackern). Snapshots bleiben.\n\n"
                "Wirklich fortfahren?"
            ),
            font_size="14sp",
        ))
        row = BoxLayout(orientation="horizontal", spacing="6dp",
                         size_hint_y=None, height="44dp")
        yes_btn = Button(text="Ja, Reset durchführen",
                          background_color=(0.7, 0.2, 0.2, 1))
        no_btn = Button(text="Abbrechen")
        def _do_reset(*_):
            confirm.dismiss()
            self._full_reset()
        yes_btn.bind(on_press=_do_reset)
        no_btn.bind(on_press=lambda *_: confirm.dismiss())
        row.add_widget(no_btn)
        row.add_widget(yes_btn)
        box.add_widget(row)
        confirm.content = box
        confirm.open()

    def _full_reset(self):
        self._lock_actions()
        self.status_label.text = "Kompletter Reset läuft..."
        asyncio.create_task(self._full_reset_async())

    async def _full_reset_async(self):
        dismiss_delay = None
        try:
            try:
                ok, msg = await self.munchlax.reset_session_data()
            except Exception as err:
                logger.error(f"full_reset failed: {type(err)},{err}")
                logger.error(traceback.format_exc())
                self._set_status(f"Fehler: {err}")
                return
            self._set_status(msg)
            if ok:
                dismiss_delay = 2.0
        finally:
            self._schedule_finish(dismiss_delay)

    def _randomize_and_load_snapshot(self):
        # Alle Action-Buttons sperren, sonst kann "Bei Null starten" parallel
        # den Run finalisieren, während Randomize+Restore läuft — keine
        # Lock-Koordination zwischen Executor-Thread und Main-Thread-Flow.
        self._lock_actions()
        self.status_label.text = "Randomize + Snapshot: startet..."
        asyncio.create_task(self._randomize_and_load_snapshot_async())

    async def _randomize_and_load_snapshot_async(self):
        # dismiss_delay wird gesetzt, wenn wir bei Erfolg verzoegert
        # dismissen wollen — dann muss auch der Reenable an dieselbe
        # Verzoegerung, damit der 3s-Lesepausen-Klick nicht einen neuen
        # Flow startet. Bei Fehler bleibt Popup offen → Sofort-Reenable.
        dismiss_delay = None
        try:
            dismiss_delay = await self._run_randomize_and_restore()
        finally:
            self._schedule_finish(dismiss_delay)

    async def _run_randomize_and_restore(self) -> float | None:
        """Fuehrt Randomize + Snapshot-Restore aus. Rueckgabe:
        - ``float`` (Sekunden) → Popup soll nach dieser Verzoegerung dismissen
          (Erfolgsfall mit Lesepause fuer den User).
        - ``None`` → Popup bleibt offen, User kann anderen Weg waehlen.
        """
        # 1) Neuen Randomizer-Run anlegen (finalisiert automatisch den durch
        #    Wipe-Detection zurueckgelassenen Marker als 'superseded_by_new_randomize').
        try:
            from backend.controller.randomizer_controller import RandomizerController
            rc = RandomizerController(self.rnd, self.pl, configsave=self.configsave)
        except Exception as err:
            logger.error(f"RandomizerController-Init failed: {err}")
            logger.error(traceback.format_exc())
            self._set_status(f"Randomizer-Init-Fehler: {err}")
            return None
        self._set_status("Randomize laeuft...")
        try:
            ok, msg, _ = await rc.randomize()
        except Exception as err:
            logger.error(f"randomize failed: {err}")
            logger.error(traceback.format_exc())
            self._set_status(f"Randomize-Fehler: {err}")
            return None
        if not ok:
            self._set_status(f"Randomize fehlgeschlagen: {msg}")
            return None
        # 2) Latest Snapshot restoren — Remap greift, weil jetzt ein aktiver
        #    Run mit neuen ROM-Pfaden existiert.
        try:
            self.munchlax.close_pokedex_db()
            sm = self._snapshot_manager()
            loop = asyncio.get_event_loop()
            entries = await loop.run_in_executor(None, sm.list)
        except Exception as err:
            logger.error(f"snapshot list failed: {err}")
            logger.error(traceback.format_exc())
            self._set_status(f"Snapshot-Fehler: {err}")
            return None
        if not entries:
            self._set_status("Randomize OK — aber keine Snapshots vorhanden")
            return None
        latest = entries[0]
        snap_id = latest.get("id", "")
        try:
            restored = await loop.run_in_executor(None, sm.restore, snap_id)
        except Exception as err:
            logger.error(f"snapshot restore failed: {err}")
            logger.error(traceback.format_exc())
            self._set_status(f"Restore-Fehler: {err}")
            return None
        if not restored:
            self._set_status("Restore fehlgeschlagen")
            return None
        # 3) User-Hinweis: der neue ROM-Pfad wird ermittelt (RunManager) und
        #    zusammen mit der BizHawk-Verbindungssituation ausgegeben. Der
        #    Emulator laedt die ROM nicht automatisch — der User muss sie in
        #    BizHawk oeffnen. RunManager liest run_meta.yml von Disk →
        #    ueber Executor, damit der Kivy-Loop nicht blockiert.
        try:
            rom_hint = await loop.run_in_executor(None, self._new_rom_hint)
        except Exception as err:
            logger.error(f"_new_rom_hint executor failed: {err}")
            logger.error(traceback.format_exc())
            rom_hint = "Neue ROM: (Pfad-Lookup fehlgeschlagen)."
        bh_hint = self._bh_reload_hint()
        self._set_status(
            f"Fertig: Snapshot {snap_id} geladen. {rom_hint} {bh_hint}"
        )
        return 3.0

    def _new_rom_hint(self) -> str:
        try:
            from backend.controller.run_manager import RunManager
            rm = RunManager(str(self.configsave))
            active = rm.get_active_run()
            if not active:
                return "Neue ROM: (Run-Info nicht verfuegbar)."
            paths = rm.get_run_paths(active["run_id"])
            roms = paths.get("roms", {})
            if not roms:
                return "Neue ROM: (keine ROM im aktiven Run)."
            # Slot-Label pro Eintrag, damit Multi-Player-Setups die richtige
            # ROM pro Spieler zuordnen koennen.
            rom_parts = [f"P{slot}: {entry.get('rom', '')}"
                          for slot, entry in sorted(roms.items())]
            return "Neue ROM(s): " + " | ".join(rom_parts) + "."
        except Exception as err:
            logger.error(f"_new_rom_hint failed: {err}")
            logger.error(traceback.format_exc())
            return "Neue ROM: (Pfad-Lookup fehlgeschlagen)."

    def _bh_reload_hint(self) -> str:
        if self.bizhawk is None:
            return "Bitte ROM manuell im Emulator laden."
        connected = list(getattr(self.bizhawk, "bizhawks", {}).keys())
        if connected:
            return ("BizHawk laeuft ({}) — bitte dort die neue ROM oeffnen "
                     "(File > Open ROM), sonst spielt der Emulator weiter mit "
                     "der alten ROM.").format(", ".join(connected))
        return "BizHawk nicht verbunden — beim naechsten Start neue ROM laden."
