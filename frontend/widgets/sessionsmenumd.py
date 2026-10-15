import asyncio
import traceback
from typing import Callable

import pathvalidate

from kivymd.uix.behaviors.toggle_behavior import MDToggleButton
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDIconButton
from kivymd.uix.button import MDButton
from kivymd.uix.button import MDButtonText
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog
from kivymd.uix.dialog import MDDialogIcon
from kivymd.uix.dialog import MDDialogHeadlineText
from kivymd.uix.dialog import MDDialogSupportingText
from kivymd.uix.dialog import MDDialogButtonContainer
from kivymd.uix.dialog import MDDialogContentContainer
from kivymd.uix.label import MDLabel
from kivymd.uix.label import MDIcon
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.navigationbar import MDNavigationBar
from kivymd.uix.navigationbar import MDNavigationItem
from kivymd.uix.navigationbar import MDNavigationItemLabel
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.relativelayout import MDRelativeLayout
from kivymd.uix.screen import MDScreen
from kivymd.uix.screenmanager import MDScreenManager
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.textfield import MDTextField
from kivymd.uix.textfield import MDTextFieldHintText

from kivy.properties import StringProperty
from kivy.uix.widget import Widget

from backend import session_store
from backend.logging_setup import get_logger
from frontend.widgets.md_common import show_snackbar

logger = get_logger(__name__, 'logs/frontend.log')


class DeleteSessionDialog(MDDialog):
    def __init__(self, session_menu, session_name, session_card, **kwargs):
        super().__init__(**kwargs)
        self.session_menu = session_menu
        self.session_name = session_name
        self.session_card = session_card

        self.auto_dismiss = False

        self.add_widget(MDDialogIcon(icon="delete-forever-outline"))
        self.add_widget(MDDialogHeadlineText(text=f"Wirklich die Session '{self.session_name}' löschen?"))
        self.add_widget(MDDialogSupportingText(
            text="Alle Einstellungen, der Pokedex und die Run-Historie (ROMs, Logs, Snapshots) dieser Session werden gelöscht."
        ))

        self.add_widget(MDDialogButtonContainer(
            Widget(),
            MDButton(
                MDButtonText(text='Abbrechen'),
                style='tonal',
                on_press=self.dismiss
            ),
            MDButton(
                MDButtonText(text="Löschen"),
                style="filled",
                on_press=self.on_yes
            ),
            spacing="10dp"
        ))

    def on_yes(self, instance):
        self.dismiss()
        self.session_menu.delete_session(self.session_name, self.session_card)


class GameNavigationItem(MDNavigationItem):
    screen_name = StringProperty()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_widget(MDNavigationItemLabel(text=self.screen_name))


class GameNavigationBar(MDNavigationBar):
    def __init__(self, screen_manager, item_list: list[dict], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.screen_manager = screen_manager
        for item in item_list:
            self.add_widget(GameNavigationItem(**item))

    def on_switch_tabs(
        self,
        item: GameNavigationItem,
        item_icon: str,
        item_display_name: str,
    ):
        self.screen_manager.current = item.screen_name


class GenToggleButton(MDButton, MDToggleButton):
    def __init__(self, *args, text="", **kwargs):
        super().__init__(*args, **kwargs)
        self.group = "games"
        self.allow_no_selection = False
        self.game_text = MDButtonText(text=text)
        self.add_widget(self.game_text)


class GenScreen(MDScreen):
    def __init__(self, name, games, callback, **kwargs):
        super().__init__(**kwargs)
        self.name = name

        box = MDBoxLayout(orientation='horizontal', pos_hint={'center_x': .5}, spacing="3dp")
        box.add_widget(Widget())
        for game in games:
            toggle = GenToggleButton(text=game[0], on_press=callback)
            box.add_widget(toggle)
        box.add_widget(Widget())
        self.add_widget(box)


class GameSelectionScreen(MDScreenManager):
    def __init__(self, callback, **kwargs):
        super().__init__(**kwargs)

        self.games = {
            'Gen 1': [('Rot', 'Rot'), ('Blau', 'Blau'), ('Gelb', 'Gelb')],
            'Gen 2': [('Gold', 'Gold'), ('Silber', 'Silber'), ('Kristall', 'Kristall')],
            'Gen 3': [('Rubin', 'Rubin'), ('Saphir', 'Saphir'), ('Smaragd', 'Smaragd'), ('Feuerrot', 'Feuerrot'), ('Blattgrün', 'Blattgruen')],
            'Gen 4': [('Diamant', 'Diamant'), ('Perl', 'Perl'), ('Platin', 'Platin'), ('Herzgold', 'Herzgold'), ('Seelensilber', 'Seelensilber')],
            'Gen 5': [('Schwarz', 'Schwarz'), ('Weiß', 'Weiss'), ('Schwarz 2', 'Schwarz2'), ('Weiß 2', 'Weiss2')],
            'Gen 6': [('X', 'X'), ('Y', 'Y'), ('Omega Rubin', 'Omega_Rubin'), ('Alpha Saphir', 'Alpha_Saphir')],
            'Gen 7': [('Sonne', 'Sonne'), ('Mond', 'Mond'), ('Ultra Sonne', 'Ultrasonne'), ('Ultra Mond', 'Ultramond')]
        }
        for gen, gameslist in self.games.items():
            self.add_widget(GenScreen(gen, gameslist, callback))

        self.current = 'Gen 1'


class CreateSessionDialog(MDDialog):
    def __init__(self, session_menu, **kwargs):
        super().__init__(**kwargs)
        self.session_menu = session_menu
        self.auto_dismiss = False

        self.screen_list = ["create", "player_name", "game_select"]
        self.current_screen_index = 0

        self.add_widget(MDDialogHeadlineText(text="Erstellen einer neuen Session"))

        self.error_label = MDLabel(
            text='-',
            pos_hint={"center_x": 0.5},
            size_hint_x=.6,
            adaptive_height=True,
            text_color="firebrick",
            halign="center",
            opacity=0
        )

        self.session_name_text_box = MDTextField(
            MDTextFieldHintText(
                text="Name der Session",
            ),
            mode="outlined",
            size_hint_x=.6,
            pos_hint={"center_x": .5},
        )

        self.name_text_box = MDTextField(
            MDTextFieldHintText(
                text="Dein Name",
            ),
            mode="outlined",
            size_hint_x=.6,
            pos_hint={"center_x": .5},
        )

        self.screen_manager = MDScreenManager(size_hint_y=None)

        self.create_screen = MDScreen(
            self.session_name_text_box,
            name="create"
        )

        self.screen_manager.add_widget(
            self.create_screen
        )
        self.screen_manager.add_widget(
            MDScreen(
                self.name_text_box,
                name="player_name"
            )
        )

        self.game_selection_manager = GameSelectionScreen(self.change_selected_game)

        item_list = [{"screen_name": generation} for generation in self.game_selection_manager.games]
        self.navbar = GameNavigationBar(self.game_selection_manager, item_list, size_hint_y=.5)

        self.screen_manager.add_widget(
            MDScreen(
                MDBoxLayout(
                    self.navbar,
                    self.game_selection_manager,
                    orientation="vertical"
                ),
                name="game_select"
            )
        )

        self.dialog_container = MDDialogContentContainer(
            self.screen_manager,
            self.error_label,
            spacing="10dp",
            orientation="vertical"
        )

        self.add_widget(self.dialog_container)

        self.next_button_text = MDButtonText(text="Nächstes")
        self.next_button = MDButton(
            self.next_button_text,
            style="filled",
            on_press=self.on_next
        )

        self.previous_button = MDButton(
            MDButtonText(text="Zurück"),
            style="tonal",
            on_press=self.on_previous
        )

        self.selected_game = MDLabel(
            text='-',
            pos_hint={"center_x": 0.5},
            size_hint_x=.6,
            halign="center",
        )

        self.add_widget(MDDialogButtonContainer(
            MDButton(
                MDButtonText(text='Abbrechen'),
                style='elevated',
                on_press=self.dismiss
            ),
            self.selected_game,
            self.previous_button,
            self.next_button,
            spacing="10dp"
        ))

        self.change_screen()

    def show_error(self, text: str) -> None:
        self.error_label.text = text
        self.error_label.opacity = 1

    def on_previous(self, instance):
        self.current_screen_index -= 1
        self.change_screen()

    def on_next(self, instance):
        if self.validate():
            self.current_screen_index += 1
            self.change_screen()

    def change_screen(self):
        if self.current_screen_index < 0:
            self.current_screen_index = 0
        elif self.current_screen_index == 2:
            self.error_label.opacity = 0
            self.next_button_text.text = "Erstellen"
            self.next_button.unbind(on_press=self.on_next)
            self.next_button.bind(on_press=self.on_confirmation)
        elif self.current_screen_index >= 0:
            self.next_button_text.text = "Weiter"
            self.error_label.opacity = 0
            self.next_button.unbind(on_press=self.on_confirmation)
            self.next_button.bind(on_press=self.on_next)

        self.screen_manager.current = self.screen_list[self.current_screen_index]

    def validate(self):
        if self.current_screen_index == 0:
            name = self.session_name_text_box.text
            if not name:
                self.show_error("Der Name darf nicht leer sein.")
                return False
            if not pathvalidate.is_valid_filename(name):
                self.show_error(f"Der Name enthält unerlaubte Zeichen. Vorschlag: {pathvalidate.sanitize_filename(name)}")
                return False
            if name in self.session_menu.session_list_names:
                self.show_error("Dieser Session-Name existiert bereits.")
                return False
        elif self.current_screen_index == 1:
            if not self.name_text_box.text:
                self.show_error("Der Name darf nicht leer sein.")
                return False
            if "_" in self.name_text_box.text:
                self.show_error("Der Name darf keinen '_' enthalten.")
                return False
        elif self.current_screen_index == 2:
            if self.selected_game.text == "-":
                return False

        return True

    def change_selected_game(self, instance):
        self.selected_game.text = instance.game_text.text

    def on_confirmation(self, instance):
        if not self.validate():
            self.show_error("Bitte ein Spiel auswählen")
            return
        error = self.session_menu.create_session(
            self.session_name_text_box.text,
            self.name_text_box.text,
            self.selected_game.text,
        )
        if error:
            # Dialog offen lassen, damit der User den Namen korrigieren kann
            self.show_error(error)
            return
        self.dismiss()


class AddSessionCard(MDCard):
    def __init__(self, session_menu, **kwargs):
        super().__init__(**kwargs)
        self.session_menu = session_menu
        layout = MDRelativeLayout()

        self.size_hint_y = None
        self.size = (1, "40dp")

        icon_button = MDIcon(
            icon="plus",
            pos_hint={"center_y": .5, "center_x": .5}
        )

        layout.add_widget(icon_button)

        self.add_widget(layout)

    def on_release(self, *args):
        super().on_release(*args)
        CreateSessionDialog(self.session_menu).open()


class SessionCard(MDCard):
    def __init__(self, session_name, session_menu, **kwargs):
        super().__init__(**kwargs)
        self.session_name = session_name
        self.session_menu = session_menu

        self.size_hint_y = None
        self.size = (1, "40dp")

        layout = MDRelativeLayout()

        self.icon_button = MDIconButton(
            icon="dots-vertical",
            pos_hint={"top": 1, "right": 1}
        )
        self.session_label = MDLabel(
            text=self.session_name,
            pos_hint={"center_y": .5, "left": 1},
            padding=("10dp", 0),
            adaptive_size=True,
        )

        menu_items = [
            {
                "text": "Auswählen / Abwählen",
                "on_release": self.menu_select,
            },
            {
                "text": "Löschen",
                "on_release": self.menu_delete,
            }
        ]
        self.menu = MDDropdownMenu(
            caller=self.icon_button, items=menu_items
        )

        self.icon_button.on_release = self.menu.open

        layout.add_widget(self.session_label)
        layout.add_widget(self.icon_button)

        self.add_widget(layout)

    def on_release(self, *args):
        super().on_release(*args)
        self.session_menu.toggle_session(self)

    def menu_select(self, *args):
        self.menu.dismiss()
        self.session_menu.toggle_session(self)

    def menu_delete(self, *args):
        self.menu.dismiss()
        DeleteSessionDialog(self.session_menu, self.session_name, self).open()

    def set_selected(self, selected: bool) -> None:
        self.style = "filled" if selected else "outlined"


class SessionMenu(MDScreen):
    """Session-Verwaltung (KivyMD). Dateilogik liegt in backend/session_store.

    Aktive Session = Karte "filled". Abwählen schaltet zurück auf die
    Standard-Einstellungen (``backend/config/default/``).
    """

    def __init__(self, session_list_names, configsave, sp, rem, obs, bh, pl, rnd, ov, nuz, app_version,
                 on_session_changed: Callable[[str | None], None] | None = None, **kwargs):
        super().__init__(**kwargs)

        self.name = "SessionMenu"
        self.configsave = configsave
        self.default_config = configsave.text
        self.configs = session_store.SessionConfigs(
            sp=sp, bh=bh, obs=obs, rem=rem, pl=pl, rnd=rnd, ov=ov, nuz=nuz,
        )
        self.app_version = app_version
        self.on_session_changed = on_session_changed

        self.session_list_names = session_list_names
        self.selected_card: SessionCard | None = None
        # Strong-Refs auf laufende Lösch-Tasks (sonst GC-Risiko)
        self._tasks: set[asyncio.Task] = set()

        scroll_view = MDScrollView(do_scroll_x=False)

        grid_layout = MDGridLayout(
            cols=3,
            padding="12dp",
            spacing="12dp",
            adaptive_height=True
        )

        self.grid_layout = grid_layout

        for session_name in self.session_list_names:
            grid_layout.add_widget(SessionCard(session_name, self, style="outlined"))

        self.add_session_card = AddSessionCard(self, style="outlined")

        grid_layout.add_widget(self.add_session_card)
        scroll_view.add_widget(grid_layout)

        self.add_widget(scroll_view)

    # --- Auswahl --------------------------------------------------------------

    @property
    def active_session_name(self) -> str | None:
        return self.selected_card.session_name if self.selected_card else None

    def toggle_session(self, session_card: SessionCard) -> None:
        if self.selected_card is session_card:
            self.activate_session(None)
        else:
            self.activate_session(session_card)

    def activate_session(self, session_card: SessionCard | None) -> None:
        """Session wechseln (``None`` = Standard-Einstellungen)."""
        if session_card is None:
            new_path = self.default_config
        else:
            new_path = session_store.session_path(self.default_config, session_card.session_name)
        old_path = self.configsave.text
        try:
            # Probelesen in frische Dicts: ist eine YAML kaputt/fehlt, bleiben
            # die Live-Dicts der bisherigen Session unangetastet.
            session_store.read_session(new_path)
        except Exception as err:
            logger.error(f"Session '{new_path}' nicht lesbar, Wechsel abgebrochen: {err}")
            logger.error(traceback.format_exc())
            show_snackbar(f"Session konnte nicht geladen werden: {err}", level='error', duration=6.0)
            return
        try:
            # Ungespeicherte Änderungen der bisherigen Session sichern, bevor
            # die Dicts überschrieben werden. Schlägt das fehl, NICHT wechseln —
            # sonst wären die Änderungen verloren.
            session_store.write_session_files(old_path, self.configs)
        except Exception as err:
            logger.error(f"Speichern von '{old_path}' vor Session-Wechsel fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
            show_snackbar(f"Aktuelle Session konnte nicht gespeichert werden, Wechsel abgebrochen: {err}",
                          level='error', duration=6.0)
            return
        try:
            session_store.load_session_into(new_path, self.configs)
        except Exception as err:
            logger.error(f"Session-Wechsel nach '{new_path}' fehlgeschlagen: {err}")
            logger.error(traceback.format_exc())
            # Dicts evtl. teilweise neu → bisherige Session zurückladen (wurde
            # eben gespeichert, also verlustfrei).
            try:
                session_store.load_session_into(old_path, self.configs)
            except Exception as reload_err:
                logger.error(f"Zurückladen von '{old_path}' fehlgeschlagen: {reload_err}")
                logger.error(traceback.format_exc())
            show_snackbar(f"Session konnte nicht geladen werden: {err}", level='error', duration=6.0)
            return

        self.configsave.text = new_path
        if self.selected_card is not None:
            self.selected_card.set_selected(False)
        self.selected_card = session_card
        if session_card is not None:
            session_card.set_selected(True)

        name = self.active_session_name
        logger.info(f"Session gewechselt: {name or 'Standard-Einstellungen'} ({new_path})")
        show_snackbar(f"Session: {name}" if name else "Standard-Einstellungen aktiv", level='success')
        if self.on_session_changed is not None:
            try:
                self.on_session_changed(name)
            except Exception as err:
                logger.error(f"on_session_changed-Callback fehlgeschlagen: {err}")
                logger.error(traceback.format_exc())

    # --- Anlegen --------------------------------------------------------------

    def create_session(self, session_name: str, player_name: str, session_game: str) -> str | None:
        """Neue Session anlegen. Gibt Fehlermeldung zurück oder ``None`` bei Erfolg.

        Vorlage sind die Standard-Einstellungen: ist gerade keine Session aktiv,
        die Live-Dicts (enthalten ungespeicherte Änderungen), sonst die
        Default-YAMLs von der Platte.
        """
        try:
            if self.selected_card is None:
                template = self.configs
            else:
                template = session_store.read_session(self.default_config)
            session_store.create_session(self.default_config, session_name, template,
                                         player_name=player_name, session_game=session_game)
        except FileExistsError:
            return "Ein Session-Ordner mit diesem Namen existiert bereits."
        except Exception as err:
            logger.error(f"Session '{session_name}' konnte nicht angelegt werden: {err}")
            logger.error(traceback.format_exc())
            return f"Anlegen fehlgeschlagen: {err}"

        self.session_list_names.append(session_name)
        self._save_session_list()
        self.grid_layout.remove_widget(self.add_session_card)
        self.grid_layout.add_widget(SessionCard(session_name, self, style="outlined"))
        self.grid_layout.add_widget(self.add_session_card)
        logger.info(f"Session angelegt: {session_name}")
        show_snackbar(f"Session '{session_name}' angelegt", level='success')
        return None

    # --- Löschen --------------------------------------------------------------

    def delete_session(self, session_name: str, session_card: SessionCard) -> None:
        if session_card is self.selected_card:
            # Aktive Session nicht löschen: exit_check schreibt beim Beenden
            # alle Configs in den aktuellen Session-Ordner.
            show_snackbar("Aktive Session kann nicht gelöscht werden. Erst eine andere wählen.", level='error')
            return
        path = session_store.session_path(self.default_config, session_name)
        show_snackbar(f"Session '{session_name}' wird gelöscht …")
        task = asyncio.create_task(self._delete_session_async(session_name, session_card, path))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _delete_session_async(self, session_name: str, session_card: SessionCard, path: str) -> None:
        try:
            # rmtree im Executor: runs/ mit ROMs/Snapshots kann groß sein
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, session_store.delete_session_dir, path)
        except Exception as err:
            logger.error(f"Session-Ordner '{path}' konnte nicht gelöscht werden: {err}")
            logger.error(traceback.format_exc())
            show_snackbar(
                f"Löschen fehlgeschlagen: {session_name} — {err}. Datei evtl. gesperrt (Antivirus, offene ROM/DB).",
                level='error', duration=6.0,
            )
            return
        # UI erst nach erfolgreichem Löschen anpassen
        try:
            self.session_list_names.remove(session_name)
        except ValueError:
            pass
        self._save_session_list()
        self.grid_layout.remove_widget(session_card)
        show_snackbar(f"Session '{session_name}' gelöscht", level='success')

    def _save_session_list(self) -> None:
        try:
            session_store.save_session_list(self.default_config, self.session_list_names)
        except Exception as err:
            logger.error(f"session_list.yml konnte nicht gespeichert werden: {err}")
            logger.error(traceback.format_exc())
            show_snackbar(f"Session-Liste nicht gespeichert: {err}", level='error', duration=6.0)
