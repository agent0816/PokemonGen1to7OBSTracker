from pathlib import Path
import pathvalidate
import yaml

from kivymd.uix.behaviors.toggle_behavior import MDToggleButtonBehavior
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDIconButton
from kivymd.uix.button import MDButton
from kivymd.uix.button import MDButtonText
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog
from kivymd.uix.dialog import MDDialogIcon
from kivymd.uix.dialog import MDDialogHeadlineText
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

from kivy.properties import BooleanProperty
from kivy.properties import StringProperty
from kivy.uix.widget import Widget

class DeleteSessionDialog(MDDialog):
    def __init__(self, session_menu, session_name, session_card, **kwargs):
        super().__init__(**kwargs)        
        self.session_menu = session_menu
        self.session_name = session_name
        self.session_card = session_card
        
        self.auto_dismiss = False

        self.add_widget(MDDialogIcon(icon="delete-forever-outline"))
        self.add_widget(MDDialogHeadlineText(text=F"Wirklich die Session '{self.session_name}' löschen?"))

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
        self.session_menu.session_list_names.remove(self.session_name)
        self.session_menu.grid_layout.remove_widget(self.session_card)

        directory_to_delete = self.session_menu.configsave.text.replace("default", self.session_name).replace(" ", "_")

        path_to_delete = Path(directory_to_delete)

        if path_to_delete.exists():
            for entry in path_to_delete.iterdir():
                if entry.is_file():
                    entry.unlink()

            path_to_delete.rmdir()

        self.dismiss()

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

class GenToggleButton(MDButton, MDToggleButtonBehavior):
    def __init__(self, *args, text="", **kwargs):
        super().__init__(self, *args, **kwargs)
        self.group = "games"
        self.allow_no_selection = False
        self.game_text = MDButtonText(text=text)
        self.add_widget(self.game_text)

class GenScreen(MDScreen):
    def __init__(self, name, games, callback, **kwargs):
        super().__init__(**kwargs)
        self.name = name

        box = MDBoxLayout(orientation='horizontal', pos_hint={'center_x':.5}, spacing="3dp")
        box.add_widget(Widget())
        for game in games:
            toggle = GenToggleButton(text=game[0], on_press=callback)
            box.add_widget(toggle)
        box.add_widget(Widget())
        self.add_widget(box)

class GameSelectionScreen(MDScreenManager):
    def __init__(self, callback, **kwargs):
        super().__init__(**kwargs)

        # self.transition = CardTransition()
        self.games={
            'Gen 1':[('Rot', 'Rot'), ('Blau', 'Blau'),('Gelb', 'Gelb')],
            'Gen 2':[('Gold', 'Gold'),('Silber', 'Silber'),('Kristall', 'Kristall')],
            'Gen 3':[('Rubin', 'Rubin'), ('Saphir', 'Saphir'),('Smaragd', 'Smaragd'),('Feuerrot', 'Feuerrot'), ('Blattgrün', 'Blattgruen')]
            ,'Gen 4':[('Diamant', 'Diamant'), ('Perl', 'Perl'),('Platin', 'Platin'),('Herzgold', 'Herzgold'), ('Seelensilber', 'Seelensilber')],
            'Gen 5':[('Schwarz', 'Schwarz'),('Weiß', 'Weiss'),('Schwarz 2', 'Schwarz2'), ('Weiß 2', 'Weiss2')],
            'Gen 6':[('X', 'X'),('Y', 'Y'), ('Omega Rubin', 'Omega_Rubin'),('Alpha Saphir', 'Alpha_Saphir')],
            'Gen 7':[('Sonne', 'Sonne') ,('Mond', 'Mond'), ('Ultra Sonne', 'Ultrasonne'), ('Ultra Mond', 'Ultramond')]
        }
        for gen, gameslist in self.games.items():
            self.add_widget(GenScreen(gen, gameslist, callback))

        self.current = 'Gen 1'

class CreateSessionDialog(MDDialog):
    def __init__(self, session_menu, **kwargs):
        super().__init__(**kwargs)
        self.session_menu = session_menu
        self.auto_dismiss = False

        self.screen_list = ["create","player_name","game_select"]
        self.current_screen_index = 0

        self.add_widget(MDDialogHeadlineText(text=F"Erstellen einer neuen Session"))

        self.error_label = MDLabel(
            text = '-', 
            pos_hint={"center_x": 0.5}, 
            size_hint_x = .6,
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
                size_hint_x = .6,
                pos_hint={"center_x": .5},
            )
        
        self.name_text_box = MDTextField(
                MDTextFieldHintText(
                    text="Dein Name",
                ),
                mode="outlined",
                size_hint_x = .6,
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
            text = '-', 
            pos_hint={"center_x": 0.5}, 
            size_hint_x = .6,
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
                self.error_label.text = "Der Name darf nicht leer sein."
                self.error_label.opacity = 1
                return False
            if not pathvalidate.is_valid_filename(name):
                self.error_label.text = f"Der Name enthält unerlaubte Zeichen. Vorschlag: {pathvalidate.sanitize_filename(name)}"
                self.error_label.opacity = 1
                return False
            if name in self.session_menu.session_list_names:
                self.error_label.text = "Dieser Session-Name existiert bereits."
                self.error_label.opacity = 1
                return False
        elif self.current_screen_index == 1:
            if not self.name_text_box.text:
                self.error_label.text = "Der Name darf nicht leer sein."
                self.error_label.opacity = 1
                return False
            if "_" in self.name_text_box.text:
                self.error_label.text = "Der Name darf keinen '_' enthalten."
                self.error_label.opacity = 1
                return False
        elif self.current_screen_index == 2:
            if self.selected_game.text == "-":
                return False

        return True
    
    def change_selected_game(self, instance):
        self.selected_game.text = instance.game_text.text

    def on_confirmation(self, instance):
        name = self.session_name_text_box.text
        
        if self.validate():
            self.session_menu.grid_layout.remove_widget(self.session_menu.add_session_card)
            self.session_menu.grid_layout.add_widget(SessionCard(name, self.session_menu, style="outlined"))
            self.session_menu.grid_layout.add_widget(self.session_menu.add_session_card)
            self.dismiss()
            self.create_new_session()
        else:
            self.error_label.text = "Bitte ein Spiel auswählen"
            self.error_label.opacity = 1

    def create_new_session(self):
        new_session_name = self.session_name_text_box.text.replace(" ", "_")
        player_name = self.name_text_box.text
        selected_game = self.selected_game.text

        new_config = self.session_menu.configsave.text.replace("default", new_session_name)
        new_session_path = Path(f"{new_config}")
        new_session_path.mkdir()

        old_player_name = self.session_menu.pl["your_name"]
        old_session_game = self.session_menu.pl["session_game"]

        sp, bh, obs, rem, pl = self.session_menu.load_config_to_save()

        self.session_menu.pl["your_name"] = player_name
        self.session_menu.pl["session_game"] = selected_game

        with open(f"{new_session_path}/sprites.yml", 'w') as file:
            yaml.dump(sp, file)

        with open(f"{new_session_path}/bh_config.yml", 'w') as file:
            yaml.dump(bh, file)

        with open(f"{new_session_path}/obs_config.yml", 'w') as file:
            yaml.dump(obs, file)

        with open(f"{new_session_path}/remote.yml", 'w') as file:
            yaml.dump(rem, file)

        with open(f"{new_session_path}/player.yml", 'w') as file:
            yaml.dump(pl, file)

        self.session_menu.pl["your_name"] = old_player_name
        self.session_menu.pl["session_game"] = old_session_game

    def on_press(self):
        self._state = 0.0
        self.set_properties_widget()

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

    def on_press(self):
        CreateSessionDialog(self.session_menu).open()

class SessionCard(MDCard):
    def __init__(self, session_name, session_menu, **kwargs):
        super().__init__(**kwargs)
        self.session_name = session_name
        self.session_menu = session_menu

        self.is_selected = BooleanProperty(False)
        
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
                "text":"Select/Unselect",
                "on_release": self.menu_select,
            },
            {
                "text":"Löschen",
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

    def on_press(self, *args):
        if self.style == "filled":
            self.is_selected = False
        else:
            self.is_selected = True
        self.change_selected_status()
        self.session_menu.change_selected_session(self)

    def menu_select(self, *args):
        self.trigger_action(duration=0)
        self.menu.dismiss()

    def menu_delete(self, *args):
        self.menu.dismiss()
        DeleteSessionDialog(self.session_menu, self.session_name, self).open()

    def change_selected_status(self):
        if self.is_selected:
            self.style = "filled"
        else:
            self.style = "outlined"

class SessionMenu(MDScreen):
    def __init__(self, session_list_names, main_menu, settings_menu, configsave, sp, rem, obs, bh, pl, app_version, **kwargs):
        super().__init__(**kwargs)

        self.name = "SessionMenu"
        self.main_menu = main_menu
        self.settings_menu = settings_menu
        self.configsave = configsave
        self.default_config = configsave.text
        self.sp = sp
        self.rem = rem
        self.obs = obs
        self.bh = bh
        self.pl = pl
        self.app_version = app_version

        self.session_list_names = session_list_names
        self.selected_session = None

        scroll_view = MDScrollView(do_scroll_x=False)

        grid_layout = MDGridLayout(
            cols=3,
            padding="12dp",
            spacing="12dp",
            adaptive_height=True
        )

        self.grid_layout = grid_layout

        for session_name in self.session_list_names:
            session_card = SessionCard(session_name, self, style="outlined")
            grid_layout.add_widget(session_card)

        self.add_session_card = AddSessionCard(self, style="outlined")

        grid_layout.add_widget(self.add_session_card)
        scroll_view.add_widget(grid_layout)

        self.add_widget(scroll_view)
    
    def load_session_config(self, config = None):
        if config is None:
            config_path = self.configsave
        else:
            config_path = config

        with open(f"{config_path}sprites.yml", 'r') as file:
            new_sp = yaml.safe_load(file)

        with open(f"{config_path}bh_config.yml", 'r') as file:
            new_bh = yaml.safe_load(file)

        with open(f"{config_path}obs_config.yml", 'r') as file:
            new_obs = yaml.safe_load(file)

        with open(f"{config_path}remote.yml", 'r') as file:
            new_rem = yaml.safe_load(file)

        with open(f"{config_path}player.yml", 'r') as file:
            new_pl = yaml.safe_load(file)
        
        if config is None:
            for key, value in new_sp.items():
                self.sp[key] = value

            for key, value in new_bh.items():
                self.bh[key] = value

            for key, value in new_obs.items():
                self.obs[key] = value

            for key, value in new_rem.items():
                self.rem[key] = value

            for key, value in new_pl.items():
                self.pl[key] = value
        else:
            return new_sp, new_bh, new_obs, new_rem, new_rem
        
    def load_config_to_save(self):
        if self.selected_session is not None:
            return self.load_session_config(config=self.default_config)
        
        return self.sp, self.bh, self.obs, self.rem, self.pl

    def change_selected_session(self, session_card: SessionCard):
        if self.selected_session is not None:
            self.selected_session.is_selected = False
            self.selected_session.change_selected_status()
        if session_card.is_selected:
            self.selected_session = session_card
            self.configsave.text = self.default_config.replace("default", f"{(session_card.session_name.replace(' ', '_'))}")
        else:
            self.selected_session = None
            self.configsave.text = self.default_config

        self.load_session_config()

    def save_session_list(self):
        new_config = self.default_config.replace("default/", "")
        with open(f"{new_config}/session_list.yml", "w") as file:
            yaml.dump(self.session_list_names, file)
