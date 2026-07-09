"""TotalWipeBanner — Popup bei soullink_rule_violation type=total_wipe.

Zeigt drei Handlungs-Optionen:
- "Letzten Snapshot laden" — nutzt SnapshotManager, restore neuester Eintrag
- "Anderen Snapshot wählen" — schließt Popup, User navigiert zu SoullinkMenu
- "Bei Null starten" — archiviert encounters-Tabelle, aktive DB-Reset
"""

import traceback
from pathlib import Path

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup

from backend.logging_setup import get_logger
from backend.snapshot_manager import SnapshotManager

logger = get_logger(__name__, './logs/wipe_banner.log')


class TotalWipeBanner(Popup):
    def __init__(self, munchlax, configsave, bh: dict | None = None,
                  on_pick_other=None, **kwargs):
        super().__init__(**kwargs)
        self.munchlax = munchlax
        self.configsave = configsave
        self.bh = bh or {}
        self._on_pick_other = on_pick_other

        self.title = "TOTAL WIPE — Run-Ende erkannt"
        self.size_hint = (0.7, 0.5)
        self.auto_dismiss = False

        content = BoxLayout(orientation="vertical", padding="10dp", spacing="8dp")
        content.add_widget(Label(
            text="Alle Pokemon sind gefallen.\nBitte Aktion wählen:",
            font_size="16sp",
        ))

        btn_row = BoxLayout(orientation="vertical", spacing="6dp")
        btn_row.add_widget(Button(
            text="Letzten Snapshot laden",
            on_press=lambda *_: self._load_latest_snapshot(),
        ))
        btn_row.add_widget(Button(
            text="Anderen Snapshot wählen",
            on_press=lambda *_: self._pick_other(),
        ))
        btn_row.add_widget(Button(
            text="Bei Null starten (Encounter archivieren)",
            on_press=lambda *_: self._reset_run(),
        ))
        content.add_widget(btn_row)

        self.status_label = Label(text="", size_hint_y=None, height="30dp")
        content.add_widget(self.status_label)

        self.content = content

    def _snapshot_manager(self) -> SnapshotManager:
        session_path = str(self.configsave)
        session_name = Path(session_path).name or "default"
        return SnapshotManager(session_path, session_name,
                                 bh_config=self.bh, munchlax=self.munchlax)

    def _load_latest_snapshot(self):
        try:
            db = getattr(self.munchlax, "pokedex_db", None)
            if db is not None:
                try:
                    db.close()
                except Exception as err:
                    logger.warning(f"pokedex_db close: {err}")
                self.munchlax.pokedex_db = None
            sm = self._snapshot_manager()
            entries = sm.list()
            if not entries:
                self.status_label.text = "Keine Snapshots vorhanden"
                return
            latest = entries[0]
            ok = sm.restore(latest.get("id", ""))
            if ok:
                self.status_label.text = f"Geladen: {latest.get('id', '')}"
                self.dismiss()
            else:
                self.status_label.text = "Restore fehlgeschlagen"
        except Exception as err:
            logger.error(f"latest snapshot load failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            self.status_label.text = f"Fehler: {err}"

    def _pick_other(self):
        if callable(self._on_pick_other):
            try:
                self._on_pick_other()
            except Exception as err:
                logger.error(f"on_pick_other callback failed: {err}")
        self.dismiss()

    def _reset_run(self):
        try:
            archive = self.munchlax.archive_active_run()
            if archive:
                self.status_label.text = f"Encounters archiviert als {archive}"
                self.dismiss()
            else:
                self.status_label.text = "Archivieren fehlgeschlagen"
        except Exception as err:
            logger.error(f"reset_run failed: {err}")
            self.status_label.text = f"Fehler: {err}"
