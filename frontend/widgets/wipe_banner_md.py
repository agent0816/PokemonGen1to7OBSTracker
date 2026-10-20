"""TotalWipeBannerMD — KivyMD-Variante von wipe_banner.TotalWipeBanner (Phase E).

Gleicher Trigger (soullink_rule_violation type=total_wipe) und dieselben Flows
wie die klassische Popup-Version — nur die UI ist ein MDDialog statt Kivy-Popup.
Die async-Handler (Snapshot laden, Randomize+Restore, Bei-Null-Start, Full-Reset,
Cancel) sind 1:1 portiert; Backend-Aufrufe bleiben unverändert.

Kein Widget: ein Controller, der einen MDDialog besitzt und ihn öffnet/schließt.
appmd registriert ihn als on_total_wipe_callback-Ziel mit `on_dismiss`-Callback.
"""
import asyncio
import traceback
from pathlib import Path

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.widget import Widget

from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.dialog import (
    MDDialog, MDDialogHeadlineText, MDDialogSupportingText,
    MDDialogContentContainer, MDDialogButtonContainer,
)
from kivymd.uix.label import MDLabel

from backend.logging_setup import get_logger
from backend.snapshot_manager import SnapshotManager
from frontend.widgets.md_common import confirm_dialog

logger = get_logger(__name__, './logs/wipe_banner.log')


class TotalWipeBannerMD:
    def __init__(self, munchlax, configsave, bh: dict | None = None,
                 on_pick_other=None, bizhawk=None, rnd: dict | None = None,
                 pl: dict | None = None, rem: dict | None = None,
                 violation: dict | None = None, on_dismiss=None):
        self.munchlax = munchlax
        self.configsave = configsave
        self.bh = bh or {}
        self.bizhawk = bizhawk
        self.rnd = rnd
        self.pl = pl
        self.rem = rem
        self._on_pick_other = on_pick_other
        self._on_dismiss = on_dismiss
        self._violation = violation or {}
        self._is_open = False
        self._tasks: set = set()  # Strong-Refs gegen GC (feedback_async_task_strong_ref)
        self._build_dialog()

    def _spawn(self, coro) -> None:
        """create_task mit Strong-Ref + Auto-Cleanup + Exception-Log."""
        def _done(task):
            self._tasks.discard(task)
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                logger.error(f"WipeBanner-Task-Fehler: {exc}")
        try:
            task = asyncio.create_task(coro)
        except RuntimeError:
            coro.close()
            return
        self._tasks.add(task)
        task.add_done_callback(_done)

    # ------------------------------------------------------------------ Aufbau
    def _build_dialog(self) -> None:
        content = MDDialogContentContainer(orientation="vertical", spacing=dp(6),
                                           size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        self.latest_button = self._btn("Letzten Snapshot laden", self._load_latest_snapshot)
        content.add_widget(self.latest_button)
        self.combo_button = None
        if self.rnd is not None and self.pl is not None:
            self.combo_button = self._btn("Neu randomisieren + Snapshot laden",
                                          self._randomize_and_load_snapshot)
            content.add_widget(self.combo_button)
        self.other_button = self._btn("Anderen Snapshot wählen", self._pick_other)
        content.add_widget(self.other_button)
        self.reset_button = self._btn("Bei Null starten (Encounter archivieren)",
                                      self._reset_run)
        content.add_widget(self.reset_button)
        self.full_reset_button = self._btn(
            "Alles frisch starten (kompletter Reset — DB leeren)",
            self._confirm_full_reset, danger=True)
        content.add_widget(self.full_reset_button)

        self.status_label = MDLabel(text="", role="small", adaptive_height=True)
        content.add_widget(self.status_label)

        self.cancel_button = MDButton(
            MDButtonText(text="Popup schließen (Run läuft weiter)"),
            style="tonal", on_release=lambda *_a: self._cancel_wipe())

        self._dialog = MDDialog(
            MDDialogHeadlineText(text="TOTAL WIPE — Run-Ende erkannt"),
            MDDialogSupportingText(text="Alle Pokemon sind gefallen. Bitte Aktion wählen:"),
            content,
            MDDialogButtonContainer(Widget(), self.cancel_button, spacing="10dp"),
            auto_dismiss=False,
        )
        self._dialog.bind(on_dismiss=self._handle_dismiss)

    def _btn(self, text: str, on_release, danger: bool = False) -> MDButton:
        btn = MDButton(MDButtonText(text=text),
                       style="filled" if danger else "tonal",
                       size_hint_x=1,
                       on_release=lambda *_a: on_release())
        if danger:
            btn.theme_bg_color = "Custom"
            btn.md_bg_color = (0.6, 0.2, 0.2, 1)
        return btn

    # --------------------------------------------------------------- Lifecycle
    def open(self) -> None:
        self._is_open = True
        try:
            self._dialog.open()
        except Exception:
            self._is_open = False
            raise

    def dismiss(self) -> None:
        self._dialog.dismiss()

    def _handle_dismiss(self, *_args) -> None:
        self._is_open = False
        if callable(self._on_dismiss):
            try:
                self._on_dismiss()
            except Exception as err:
                logger.warning(f"on_dismiss callback failed: {err}")

    # ------------------------------------------------------------ Lock-Helfer
    def _action_buttons(self) -> list:
        buttons = [self.latest_button, self.other_button, self.reset_button,
                   self.full_reset_button]
        if self.combo_button is not None:
            buttons.append(self.combo_button)
        return buttons

    def _lock_actions(self) -> None:
        for btn in self._action_buttons():
            btn.disabled = True

    def _unlock_actions(self) -> None:
        for btn in self._action_buttons():
            btn.disabled = False

    def _schedule_finish(self, dismiss_delay) -> None:
        """Reenable + optionaler Dismiss in EINEM Clock-Callback (kein Input-
        Event zwischen Unlock und Dismiss). ``None`` = Popup bleibt offen."""
        if dismiss_delay is None:
            Clock.schedule_once(lambda _dt: self._unlock_actions(), 0)
        else:
            def _finish(_dt):
                self._unlock_actions()
                self.dismiss()
            Clock.schedule_once(_finish, dismiss_delay)

    def _snapshot_manager(self) -> SnapshotManager:
        session_path = str(self.configsave)
        session_name = Path(session_path).name or "default"
        return SnapshotManager(session_path, session_name, bh_config=self.bh,
                               munchlax=self.munchlax, rnd=self.rnd)

    def _set_status(self, text: str) -> None:
        Clock.schedule_once(lambda _dt: setattr(self.status_label, "text", text), 0)

    # ---------------------------------------------------- Snapshot (latest)
    def _load_latest_snapshot(self) -> None:
        self._lock_actions()
        self.status_label.text = "Snapshot wird geladen..."
        self._spawn(self._load_latest_snapshot_async())

    async def _load_latest_snapshot_async(self) -> None:
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
                logger.error(traceback.format_exc())
                self._set_status(f"Fehler: {err}")
                return
            if ok:
                self._set_status(f"Geladen: {snap_id}")
                try:
                    await self.munchlax.submit_cross_thread(
                        self.munchlax.force_overlay_broadcast(f"snapshot restore: {snap_id}"))
                except Exception as err:
                    logger.warning(f"force_overlay_broadcast nach restore failed: {err}")
                dismiss_delay = 0
            else:
                self._set_status("Restore fehlgeschlagen")
        finally:
            self._schedule_finish(dismiss_delay)

    # ------------------------------------------------------------- Cancel
    def _cancel_wipe(self) -> None:
        subject_pid = None
        try:
            raw = self._violation.get("player_id")
            if raw is None:
                raw = self._violation.get("subject")
            if raw is not None:
                subject_pid = int(raw)
        except Exception as err:
            logger.debug(f"_cancel_wipe: subject_pid-Parse failed: {err}")
        try:
            self.munchlax.dispatch_cross_thread(
                self.munchlax.dismiss_wipe(subject_pid), name="dismiss_wipe")
        except Exception as err:
            logger.error(f"dismiss_wipe dispatch failed: {err}")
            logger.error(traceback.format_exc())
        self.dismiss()

    def _pick_other(self) -> None:
        if callable(self._on_pick_other):
            try:
                self._on_pick_other()
            except Exception as err:
                logger.error(f"on_pick_other callback failed: {err}")
                logger.error(traceback.format_exc())
        self.dismiss()

    # --------------------------------------------------------- Reset (Null)
    def _reset_run(self) -> None:
        self._lock_actions()
        self.status_label.text = "Encounters werden archiviert..."
        self._spawn(self._reset_run_async())

    async def _reset_run_async(self) -> None:
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

    # ------------------------------------------------------- Full-Reset
    def _confirm_full_reset(self) -> None:
        rem = self.rem or {}
        if not bool(rem.get("start_server")):
            self._set_status("Fehler: Nur der Host darf Session zurücksetzen")
            return
        confirm_dialog(
            "Kompletter Reset — irreversibel",
            "Alle Encounter, Teams, Bag-Items und Soullink-Links werden gelöscht "
            "(auf allen verbundenen Trackern). Snapshots bleiben. Wirklich fortfahren?",
            on_confirm=self._full_reset, confirm_text="Ja, Reset durchführen",
            cancel_text="Abbrechen")

    def _full_reset(self) -> None:
        self._lock_actions()
        self.status_label.text = "Kompletter Reset läuft..."
        self._spawn(self._full_reset_async())

    async def _full_reset_async(self) -> None:
        dismiss_delay = None
        try:
            try:
                ok, msg = await self.munchlax.submit_cross_thread(
                    self.munchlax.reset_session_data())
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

    # ------------------------------------------------ Randomize + Restore
    def _randomize_and_load_snapshot(self) -> None:
        self._lock_actions()
        self.status_label.text = "Randomize + Snapshot: startet..."
        self._spawn(self._randomize_and_load_snapshot_async())

    async def _randomize_and_load_snapshot_async(self) -> None:
        dismiss_delay = None
        try:
            dismiss_delay = await self._run_randomize_and_restore()
        finally:
            self._schedule_finish(dismiss_delay)

    async def _run_randomize_and_restore(self) -> float | None:
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
        try:
            await self.munchlax.submit_cross_thread(
                self.munchlax.force_overlay_broadcast(f"randomize+restore: {snap_id}"))
        except Exception as err:
            logger.warning(f"force_overlay_broadcast nach randomize+restore failed: {err}")
        try:
            rom_hint = await loop.run_in_executor(None, self._new_rom_hint)
        except Exception as err:
            logger.error(f"_new_rom_hint executor failed: {err}")
            logger.error(traceback.format_exc())
            rom_hint = "Neue ROM: (Pfad-Lookup fehlgeschlagen)."
        bh_hint = self._bh_reload_hint()
        self._set_status(f"Fertig: Snapshot {snap_id} geladen. {rom_hint} {bh_hint}")
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
