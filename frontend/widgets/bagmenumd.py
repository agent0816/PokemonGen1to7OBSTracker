"""KivyMD-Bag-Screen (Tasche). Filter-/Gruppenlogik liegt in backend/bag_view.

Pro Spieler+Edition ein Block, darin eine MDCard je Pocket (responsiv
umbrechend). MDScrollView + Boxen statt RecycleView (rendert im ScreenManager
nicht, siehe Memory reference_recycleview_screenmanager). Datenfluss, Rando-
TM/HM-Lookup und Sonderbonbon-Schreiben sind 1:1 aus frontend/widgets/bagmenu.py.
"""
import asyncio
import traceback
from pathlib import Path

from kivy.clock import Clock
from kivy.metrics import dp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.card import MDCard
from kivymd.uix.fitimage import FitImage
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.selectioncontrol import MDSwitch
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText

from backend.bag_view import (
    filter_bag_rows, group_bag, item_display_name, item_slug, pocket_key_from_label,
    pocket_labels_for, safe_int,
)
from backend.classes.pokedex_db import PokedexDB
from backend.logging_setup import get_logger
from backend.tm_type_resolver import resolve_tm_hm_sprite
from frontend.widgets.md_common import show_snackbar

logger = get_logger(__name__, 'logs/bagmenu.log')

CARD_MIN_WIDTH = dp(260)
ITEM_ROW_H = dp(34)
ICON_W = dp(28)


def _label(text: str, bold: bool = False, role: str | None = None, **kwargs) -> MDLabel:
    lbl = MDLabel(text=text, bold=bold, halign="left", valign="center",
                  shorten=True, shorten_from="right", **kwargs)
    if role:
        lbl.role = role
    lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
    return lbl


class PocketCard(MDCard):
    """Eine Pocket: Kopf (Label) + vertikale Item-Liste. Höhe folgt dem Inhalt."""

    def __init__(self, label: str, **kwargs):
        super().__init__(style="elevated", orientation="vertical", size_hint_y=None,
                         padding=dp(8), spacing=dp(2), radius=[dp(12)] * 4, **kwargs)
        self.bind(minimum_height=self.setter("height"))
        head = _label(label, bold=True, size_hint_y=None, height=dp(30))
        self.add_widget(head)


class BagMenu(MDScreen):
    def __init__(self, configsave, pl, sp, bizhawk, citra, app_version, **kwargs):
        super().__init__(**kwargs)
        self.name = "BagMenu"
        self.configsave = configsave
        self.pl = pl if pl is not None else {}
        # sp = sprites-Config (items_path für Item-Icons)
        self.sp = sp
        # Bizhawk (Gen 1-5) / Citra (Gen 6/7) für den Sonderbonbon-Schreibknopf
        self.bizhawk = bizhawk
        self.citra = citra
        self.app_version = app_version
        self._rando_tm_moves: dict[int, str] | None = None
        self._rando_hm_moves: dict[int, str] | None = None

        # Cache aller DB-Zeilen; Filter laufen in-memory.
        self._all_rows: list[dict] = []
        # Sonderbonbon-Tippstand pro (owner, edition); überlebt Reloads.
        self._candy_count_per_section: dict[tuple[str, str], str] = {}

        self._player_filter: str = "alle"
        self._pocket_label: str = "alle"
        self._load_task: asyncio.Task | None = None
        self._reload_pending: bool = False
        self._load_gen: int = 0
        self._rendered: bool = False
        self._last_signature: tuple | None = None
        self._refresh_event = None
        self._menus: list[MDDropdownMenu] = []
        self._tasks: set[asyncio.Task] = set()

        root = MDBoxLayout(orientation="vertical", padding="10dp", spacing="8dp")

        header = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="48dp", spacing="10dp")
        self.head_label = MDLabel(text="Tasche", role="large", valign="center")
        header.add_widget(self.head_label)
        self.refresh_button = MDButton(
            MDButtonText(text="Aktualisieren"), style="tonal",
            pos_hint={"center_y": .5}, on_release=lambda _i: self._reload(),
        )
        header.add_widget(self.refresh_button)
        root.add_widget(header)

        filter_bar = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="56dp", spacing="10dp")
        self.player_button_text = MDButtonText(text="Spieler: alle")
        self.player_button = MDButton(self.player_button_text, style="tonal", pos_hint={"center_y": .5},
                                      on_release=lambda i: self._open_player_menu(i))
        filter_bar.add_widget(self.player_button)
        self.pocket_button_text = MDButtonText(text="Pocket: alle")
        self.pocket_button = MDButton(self.pocket_button_text, style="tonal", pos_hint={"center_y": .5},
                                      on_release=lambda i: self._open_pocket_menu(i))
        filter_bar.add_widget(self.pocket_button)
        self.search_input = MDTextField(MDTextFieldHintText(text="Name oder ID …"),
                                        mode="outlined", size_hint_x=0.4)
        # Debounce: nicht bei jedem Tastendruck alles neu aufbauen
        self._filter_trigger = Clock.create_trigger(lambda _dt: self._apply_filters(), 0.15)
        self.search_input.bind(text=lambda _i, _v: self._filter_trigger())
        filter_bar.add_widget(self.search_input)
        root.add_widget(filter_bar)

        sub_bar = MDBoxLayout(orientation="horizontal", size_hint_y=None, height="40dp", spacing="10dp")
        sub_bar.add_widget(MDLabel(text="nur Bälle", size_hint_x=None, width="90dp", valign="center"))
        self.balls_only_switch = MDSwitch(pos_hint={"center_y": .5})
        self.balls_only_switch.bind(active=lambda _i, _v: self._filter_trigger())
        sub_bar.add_widget(self.balls_only_switch)
        self.count_label = MDLabel(text="0 Einträge", valign="center", halign="right")
        sub_bar.add_widget(self.count_label)
        root.add_widget(sub_bar)

        self.empty_hint = MDLabel(text="", size_hint_y=None, height=0, opacity=0, halign="center")
        root.add_widget(self.empty_hint)

        scroll = MDScrollView(do_scroll_x=False)
        self.content_box = MDBoxLayout(orientation="vertical", size_hint_y=None,
                                       spacing=dp(12), padding=(0, dp(4)))
        self.content_box.bind(minimum_height=self.content_box.setter("height"))
        scroll.add_widget(self.content_box)
        root.add_widget(scroll)

        self.add_widget(root)

    # --- Lifecycle / Laden -----------------------------------------------------

    def on_pre_enter(self, *args) -> None:
        self._reload()
        # Bag-Reader schreibt im 5-s-Takt in die DB; solange der Screen offen
        # ist, im gleichen Takt nachladen. Guard gegen Doppel-Schedule.
        if self._refresh_event is None:
            self._refresh_event = Clock.schedule_interval(lambda _dt: self._reload(), 5.0)

    def on_leave(self, *args) -> None:
        if self._refresh_event is not None:
            self._refresh_event.cancel()
            self._refresh_event = None
        # Offenes Dropdown nicht über den Screen-Wechsel hinaus stehen lassen
        for m in self._menus:
            m.dismiss()
        self._menus = []

    def load_from_session(self) -> None:
        """Session-Wechsel-Hook: Filter zurücksetzen, DB neu laden."""
        self._player_filter = "alle"
        self._pocket_label = "alle"
        self.player_button_text.text = "Spieler: alle"
        self.pocket_button_text.text = "Pocket: alle"
        self.search_input.text = ""
        self.balls_only_switch.active = False
        # Bonbon-Tippstand gehört zur vorigen Session (gleicher owner/edition
        # könnte in der neuen Session einen Fremdwert zeigen).
        self._candy_count_per_section.clear()
        self._all_rows = []
        self._rendered = False
        self._reload()

    def _reload(self) -> None:
        self._update_rando_data()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._all_rows = self._load_from_db()
            self._after_load()
            return
        if self._load_task is not None and not self._load_task.done():
            self._reload_pending = True
            return
        self._start_load_task()

    def _start_load_task(self) -> None:
        self._reload_pending = False
        self._load_gen += 1
        gen = self._load_gen
        # Session-Identität beim Start festhalten (Guard gegen stale Anzeige)
        session = "" if self.configsave is None else str(self.configsave)
        self._load_task = asyncio.create_task(self._reload_async(gen, session))
        self._load_task.add_done_callback(self._on_load_done)

    async def _reload_async(self, gen: int, session: str) -> None:
        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(None, self._load_from_db, session)
        if gen != self._load_gen or self._reload_pending:
            return  # veraltet
        if session != str(self.configsave):
            # Session mitten im Load gewechselt → verwerfen und frisch laden
            self._reload_pending = True
            return
        self._all_rows = rows
        self._after_load()

    def _on_load_done(self, task: asyncio.Task) -> None:
        if self._load_task is task:
            self._load_task = None
        if task.cancelled():
            return
        err = task.exception()
        if err is not None:
            logger.error(f"BagMenu Reload-Task fehlgeschlagen: {type(err)},{err}")
            logger.error("".join(traceback.format_exception(type(err), err, err.__traceback__)))
            self._set_hint("Laden fehlgeschlagen (siehe Log).")
        if self._reload_pending:
            self._start_load_task()

    def _load_from_db(self, session: str | None = None) -> list[dict]:
        """Liest bag_inventory + bag_first_seen und joint pro (owner, edition, item_id)."""
        if session is None:
            session = str(self.configsave) if self.configsave is not None else ""
        if not session:
            return []
        db = PokedexDB(Path(session))
        try:
            with db.access_lock:
                db.connect()
                if db.connection is None:
                    return []
                inventory = self._fetch_all_inventory(db)
                first_seen = self._fetch_all_first_seen(db)
            rows: list[dict] = []
            for inv in inventory:
                owner = inv.get("owner")
                edition = str(inv.get("edition") or "")
                item_id = int(inv.get("item_id") or 0)
                fs_map = first_seen.get((owner, edition), {})
                rows.append({
                    "owner": owner,
                    "edition": edition,
                    "pocket": inv.get("pocket") or "",
                    "item_id": item_id,
                    "qty": int(inv.get("qty") or 0),
                    "last_seen": inv.get("last_seen") or "",
                    "first_seen": fs_map.get(item_id, ""),
                })
            return rows
        except Exception as err:
            logger.error(f"BagMenu DB-Load failed: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            return []
        finally:
            db.close()

    @staticmethod
    def _fetch_all_inventory(db: PokedexDB) -> list[dict]:
        if db.connection is None:
            return []
        cur = db.connection.cursor()
        cur.execute("SELECT * FROM bag_inventory ORDER BY owner, edition, pocket, item_id")
        return [dict(row) for row in cur.fetchall()]

    @staticmethod
    def _fetch_all_first_seen(db: PokedexDB) -> dict[tuple[str, str], dict[int, str]]:
        """Map (owner, edition) -> {item_id: first_seen_iso}."""
        if db.connection is None:
            return {}
        cur = db.connection.cursor()
        cur.execute("SELECT owner, edition, item_id, first_seen FROM bag_first_seen")
        result: dict[tuple[str, str], dict[int, str]] = {}
        for row in cur.fetchall():
            key = (row["owner"], str(row["edition"] or ""))
            result.setdefault(key, {})[int(row["item_id"])] = row["first_seen"]
        return result

    def _after_load(self, force: bool = False) -> None:
        owners = self._owners()
        if self._player_filter != "alle" and self._player_filter not in owners:
            self._player_filter = "alle"
            self.player_button_text.text = "Spieler: alle"
        labels = pocket_labels_for(self._all_rows, self._player_filter)
        if self._pocket_label not in labels:
            self._pocket_label = "alle"
            self.pocket_button_text.text = "Pocket: alle"
        sig = self._signature()
        if sig == self._last_signature and self._rendered and not force:
            return  # unverändert: kein Neuaufbau (erhält Tippstand in den Bonbon-Feldern)
        self._last_signature = sig
        self._apply_filters()

    def _signature(self) -> tuple:
        # Neben den Zeilen fließen auch Rando-TM/HM-Mapping (Sprites), items_path
        # und die remote-Flags (Candy-Button) in das Rendering ein — sonst bliebe
        # z.B. ein nach dem ersten Render eintreffender rando_moves_sync-Broadcast
        # unsichtbar, bis sich eine Zeile ändert (WARNUNG gui-reviewer).
        rows_sig = tuple(
            (r["owner"], r["edition"], r["pocket"], r["item_id"], r["qty"]) for r in self._all_rows
        )
        items_path = (self.sp.get("items_path") if self.sp else "") or ""
        extra = []
        for owner in sorted({str(r.get("owner") or "") for r in self._all_rows}):
            tm, hm = self._rando_moves_for_owner(owner)
            slot = PokedexDB.owner_slot(owner)
            remote = bool(self.pl.get(f"remote_{slot}", False)) if slot is not None else False
            extra.append((owner, tuple(sorted((tm or {}).items())),
                          tuple(sorted((hm or {}).items())), remote))
        return (rows_sig, items_path, tuple(extra))

    # --- Rando-Daten (1:1 Klassik) ---------------------------------------------

    def _update_rando_data(self) -> None:
        self._rando_tm_moves = None
        self._rando_hm_moves = None
        if not self.manager or not self.manager.has_screen("MainMenu"):
            return
        try:
            main_menu = self.manager.get_screen("MainMenu")
            if not hasattr(main_menu, 'randomizer'):
                return
            rando_data = main_menu.randomizer.get_log_data()
            if rando_data:
                self._rando_tm_moves = rando_data.tm_moves or None
                self._rando_hm_moves = rando_data.hm_moves or None
        except Exception as err:
            logger.error(f"BagMenu Rando-Daten nicht lesbar: {type(err)},{err}")
            logger.error(traceback.format_exc())

    def _rando_moves_for_owner(self, owner: str) -> tuple[dict | None, dict | None]:
        """Loest den Owner-String auf den player_id des jeweiligen Spielers
        auf und liefert dessen rando_tm_moves + rando_hm_moves aus Munchlax.

        Fallback-Kaskade:
        1. Munchlax.rando_tm_moves[pid]: Server-authoritativer Stand aus
           rando_moves_sync-Broadcast (deckt Remote-Spieler ab).
        2. Lokaler Parser-State ``self._rando_tm_moves``: **nur** wenn der
           Owner-Slot einem lokalen Spieler entspricht (``pid in local_slots``).
           Fuer Remote-Spieler ohne gesynctes Mapping darf NICHT auf den
           lokalen Parser zurueckgefallen werden — das ist der Rando-Stand
           dieses Hosts und waere falsch (R1 WARNUNG 2026-10-01).
        3. ``None`` → ``resolve_tm_hm_sprite`` faellt auf Vanilla-LUT.

        Normalisiert leeres ``{}`` zu ``None``, damit die Fallback-Kaskade
        auch bei leeren Broadcasts (z.B. Reset) sauber weiterprueft.
        """
        pid = PokedexDB.owner_slot(owner)
        munchlax = None
        local_slots: set[int] = set()
        try:
            main_menu = self.manager.get_screen("MainMenu")
            munchlax = getattr(main_menu, "munchlax", None)
            slot_getter = getattr(main_menu, "_local_player_slots", None)
            if callable(slot_getter):
                local_slots = set(slot_getter())
        except Exception:
            munchlax = None

        tm = hm = None
        if munchlax is not None and pid is not None:
            tm = (munchlax.rando_tm_moves or {}).get(pid) or None
            hm = (munchlax.rando_hm_moves or {}).get(pid) or None
        if tm is None and (pid is None or pid in local_slots):
            tm = self._rando_tm_moves or None
        if hm is None and (pid is None or pid in local_slots):
            hm = self._rando_hm_moves or None
        return tm, hm

    # --- Filter ------------------------------------------------------------------

    def _owners(self) -> list[str]:
        owners = {str(r.get("owner") or "").strip() for r in self._all_rows}
        return sorted(o for o in owners if o)

    def _open_menu(self, caller, entries: list[tuple[str, object]], on_pick) -> None:
        for m in self._menus:
            m.dismiss()
        items = [{"text": text, "on_release": (lambda v=value, t=text: self._menu_pick(on_pick, v, t))}
                 for text, value in entries]
        menu = MDDropdownMenu(caller=caller, items=items)
        self._menus = [menu]
        menu.open()

    def _menu_pick(self, on_pick, value, text) -> None:
        for m in self._menus:
            m.dismiss()
        on_pick(value, text)

    def _open_player_menu(self, caller) -> None:
        entries: list[tuple[str, object]] = [("alle", "alle")]
        entries += [(self._owner_title(o), o) for o in self._owners()]
        self._open_menu(caller, entries, self._pick_player)

    def _open_pocket_menu(self, caller) -> None:
        labels = pocket_labels_for(self._all_rows, self._player_filter)
        self._open_menu(caller, [(lbl, lbl) for lbl in labels], self._pick_pocket)

    def _pick_player(self, value, text) -> None:
        self._player_filter = str(value)
        self.player_button_text.text = f"Spieler: {text}"
        # Spielerwechsel kann andere Editionen/Pockets sichtbar machen
        if self._pocket_label not in pocket_labels_for(self._all_rows, self._player_filter):
            self._pocket_label = "alle"
            self.pocket_button_text.text = "Pocket: alle"
        self._apply_filters()

    def _pick_pocket(self, value, text) -> None:
        self._pocket_label = str(value)
        self.pocket_button_text.text = f"Pocket: {text}"
        self._apply_filters()

    @staticmethod
    def _owner_title(owner: str) -> str:
        slot = PokedexDB.owner_slot(owner)
        return f"Spieler {slot}" if slot is not None else str(owner)

    def _apply_filters(self) -> None:
        pocket_key = None if self._pocket_label == "alle" else pocket_key_from_label(self._pocket_label)
        try:
            filtered = filter_bag_rows(
                self._all_rows, self._player_filter, pocket_key,
                bool(self.balls_only_switch.active), self.search_input.text or "",
            )
            self.count_label.text = f"{len(filtered)} Einträge"
            self._set_hint("" if filtered else "Keine Items gefunden.")
            self._render_sections(group_bag(filtered))
            self._rendered = True
        except Exception as err:
            logger.error(f"BagMenu Aufbereitung fehlgeschlagen: {type(err)},{err}")
            logger.error(f"{traceback.format_exc()}")
            self._set_hint("Fehler beim Aufbereiten der Tasche (siehe Log).")

    def _set_hint(self, text: str) -> None:
        self.empty_hint.text = text
        self.empty_hint.height = dp(50) if text else 0
        self.empty_hint.opacity = 1 if text else 0

    # --- Rendern -----------------------------------------------------------------

    def _render_sections(self, sections: list[dict]) -> None:
        self.content_box.clear_widgets()
        for section in sections:
            self.content_box.add_widget(self._build_section(section))

    def _build_section(self, section: dict) -> MDBoxLayout:
        owner = str(section["owner"])
        edition = str(section["edition"])
        block = MDBoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6))
        block.bind(minimum_height=block.setter("height"))

        slot = PokedexDB.owner_slot(owner)
        owner_txt = slot if slot is not None else owner
        title_text = f"Spieler {owner_txt} — Edition {edition}"
        if section.get("edition_name"):
            title_text += f" ({section['edition_name']})"
        can_write = self._can_write_for_owner(owner, edition)

        title_bar = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                                height=dp(56) if can_write else dp(32), spacing=dp(6))
        title_bar.add_widget(_label(title_text, bold=True))
        if can_write:
            title_bar.add_widget(self._build_candy_controls(owner, edition))
        block.add_widget(title_bar)

        grid = MDGridLayout(cols=1, size_hint_y=None, spacing=dp(8))
        grid.bind(minimum_height=grid.setter("height"))
        grid.bind(width=lambda g, w: setattr(g, "cols", max(1, int(w / CARD_MIN_WIDTH))))
        grid.cols = max(1, int((self.width or CARD_MIN_WIDTH) / CARD_MIN_WIDTH))

        rando_tm, rando_hm = self._rando_moves_for_owner(owner)
        for pocket in section["pockets"]:
            card = PocketCard(pocket["label"])
            for row in pocket["items"]:
                card.add_widget(self._make_item_row(edition, row, rando_tm, rando_hm))
            grid.add_widget(card)
        block.add_widget(grid)
        return block

    def _make_item_row(self, edition: str, row: dict, rando_tm, rando_hm) -> MDBoxLayout:
        line = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=ITEM_ROW_H, spacing=dp(6))
        item_id = int(row.get("item_id") or 0)
        qty = int(row.get("qty") or 0)
        items_path = (self.sp.get("items_path") if self.sp else "") or ""

        slug = item_slug(edition, item_id) if items_path else None
        if slug:
            slug = resolve_tm_hm_sprite(safe_int(edition), slug, rando_tm, rando_hm)
            line.add_widget(FitImage(source=f"{items_path}/{slug}.png", fit_mode="contain",
                                     size_hint=(None, None), size=(ICON_W, ICON_W),
                                     pos_hint={"center_y": .5}))
        line.add_widget(_label(item_display_name(edition, item_id)))
        if qty > 1:
            qty_lbl = MDLabel(text=f"×{qty}", size_hint_x=None, width=dp(48),
                              halign="right", valign="center")
            line.add_widget(qty_lbl)
        return line

    # --- Sonderbonbon-Schreiben (1:1 Klassik) -------------------------------------

    @staticmethod
    def _owner_slot(owner: str) -> int | None:
        """Extrahiert Player-Slot aus build_owner-Format 'name_<slot>'.

        Legacy-Rows ohne Suffix (rein-numerischer Owner) werden auch
        unterstuetzt.
        """
        if not owner:
            return None
        idx = owner.rfind("_")
        candidate = owner[idx + 1:] if idx >= 0 else owner
        if not candidate.isdigit():
            return None
        return int(candidate)

    def _can_write_for_owner(self, owner: str, edition: str) -> bool:
        """True, wenn dieser Spieler einen Sonderbonbon-Knopf bekommt.

        Bedingungen:
          - Spieler ist nicht remote (pl[remote_N] != True)
          - Passende Backend-Instanz ist initialisiert: BizHawk fuer Edition
            < 60 (Gen 1-5), Citra fuer Edition >= 60 (Gen 6/7).
        """
        slot = self._owner_slot(owner)
        if slot is None:
            return False
        try:
            edition_int = int(edition)
        except (TypeError, ValueError):
            return False
        if self.pl.get(f"remote_{slot}", False):
            return False
        if edition_int < 60:
            return self.bizhawk is not None
        return self.citra is not None

    def _is_citra_edition(self, edition: str) -> bool:
        try:
            return int(edition) >= 60
        except (TypeError, ValueError):
            return False

    def _build_candy_controls(self, owner: str, edition: str) -> MDBoxLayout:
        bar = MDBoxLayout(orientation="horizontal", size_hint_x=None, width=dp(300), spacing=dp(6))
        lbl = MDLabel(text="Sonderbonbons:", size_hint_x=None, width=dp(120),
                      halign="right", valign="center")
        bar.add_widget(lbl)
        key = (owner, edition)
        initial = self._candy_count_per_section.get(key, "1")
        count_input = MDTextField(text=initial, input_filter="int", mode="outlined",
                                  size_hint_x=None, width=dp(80), pos_hint={"center_y": .5})
        # Tipp-Stand zwischen Auto-Reloads konservieren.
        count_input.bind(
            text=lambda _i, v: self._candy_count_per_section.__setitem__(key, v),
        )
        bar.add_widget(count_input)
        btn = MDButton(MDButtonText(text="+"), style="tonal", pos_hint={"center_y": .5},
                       on_release=lambda _i: self._on_add_rare_candies(owner, edition, count_input))
        bar.add_widget(btn)
        return bar

    def _spawn(self, coro) -> None:
        """Task mit Strong-Ref halten, bis er fertig ist (GC-Schutz)."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._on_spawned_done)

    def _on_spawned_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        # Fehler in _after_write (show_snackbar/Clock) o.ä. liegen außerhalb des
        # try/except der Coroutine — ohne dieses Logging stürbe der Task still.
        err = task.exception()
        if err is not None:
            logger.error(f"BagMenu Schreib-Task fehlgeschlagen: {type(err)},{err}")
            logger.error("".join(traceback.format_exception(type(err), err, err.__traceback__)))

    def _on_add_rare_candies(self, owner: str, edition: str, count_input: MDTextField) -> None:
        try:
            count = int((count_input.text or "0").strip())
        except ValueError:
            count = 0
        if count <= 0:
            show_snackbar("Anzahl muss > 0 sein.", level='error')
            return
        slot = self._owner_slot(owner)
        if slot is None:
            show_snackbar("Owner-Slot nicht ermittelbar.", level='error')
            return
        if self._is_citra_edition(edition):
            # Citra hat genau einen lokalen Spieler (player_number)
            if self.citra is None or self.citra.player_number != slot:
                show_snackbar("Citra-Spieler nicht aktiv.", level='error')
                return
            self._spawn(self._do_add_rare_candies_citra(count))
        else:
            client_id = f"player{slot:03d}"
            self._spawn(self._do_add_rare_candies_bizhawk(client_id, count))

    async def _do_add_rare_candies_bizhawk(self, client_id: str, count: int) -> None:
        try:
            from frontend.bh_dispatch import bh_submit
            success, msg = await bh_submit(self.bizhawk.add_rare_candies(client_id, count))
        except Exception as err:
            logger.error(f"add_rare_candies (bizhawk) failed: {type(err)},{err}")
            logger.error(traceback.format_exc())
            show_snackbar("Schreiben fehlgeschlagen — Logs prüfen.", level='error')
            return
        logger.info(
            f"add_rare_candies bizhawk({client_id}, +{count}): "
            f"success={success}, msg={msg}"
        )
        self._after_write(success, msg)

    async def _do_add_rare_candies_citra(self, count: int) -> None:
        try:
            success, msg = await self.citra.add_rare_candies(count)
        except Exception as err:
            logger.error(f"add_rare_candies (citra) failed: {type(err)},{err}")
            logger.error(traceback.format_exc())
            show_snackbar("Schreiben fehlgeschlagen — Logs prüfen.", level='error')
            return
        logger.info(f"add_rare_candies citra(+{count}): success={success}, msg={msg}")
        self._after_write(success, msg)

    def _after_write(self, success: bool, msg: str) -> None:
        show_snackbar(str(msg), level='success' if success else 'error')
        if success:
            # Bag-Reader-Tick (5 s) braucht ggf. einen Moment, bis das
            # geschriebene Pocket in der DB landet — kurz warten, dann neu laden.
            Clock.schedule_once(lambda _dt: self._reload(), 0.5)
