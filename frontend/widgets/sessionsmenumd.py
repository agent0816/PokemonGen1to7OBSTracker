from pathlib import Path
import pathvalidate
import yaml

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
from kivymd.uix.divider import MDDivider
from kivymd.uix.label import MDLabel
from kivymd.uix.label import MDIcon
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.relativelayout import MDRelativeLayout
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.textfield import MDTextField
from kivymd.uix.textfield import MDTextFieldLeadingIcon
from kivymd.uix.textfield import MDTextFieldHintText
from kivymd.uix.textfield import MDTextFieldHelperText
from kivymd.uix.textfield import MDTextFieldTrailingIcon
from kivymd.uix.textfield import MDTextFieldMaxLengthText

from kivy.properties import BooleanProperty
from kivy.uix.widget import Widget

class DeleteSessionDialog(MDDialog):
    def __init__(self, session_menu, session_name, session_card, **kwargs):
        super().__init__(**kwargs)        
        self.session_menu = session_menu
        self.session_name = session_name
        self.session_card = session_card
        
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
        # self.session_menu.session_list_names.remove(self.session_name)
        self.session_menu.grid_layout.remove_widget(self.session_card)

        # directory_to_delete = self.session_menu.configsave.text.replace("default", self.session_name).replace(" ", "_")

        # path_to_delete = Path(directory_to_delete)

        # if path_to_delete.exists():
        #     for entry in path_to_delete.iterdir():
        #         if entry.is_file():
        #             entry.unlink()

        #     path_to_delete.rmdir()

        self.dismiss()

class CreateSessionDialog(MDDialog):
    def __init__(self, session_menu, **kwargs):
        super().__init__(**kwargs)
        self.session_menu = session_menu

        self.add_widget(MDDialogHeadlineText(text=F"Erstellen einer neuen Session"))

        self.name_text_box = MDTextField(
                MDTextFieldLeadingIcon(
                    icon="playlist-plus",
                ),
                MDTextFieldHintText(
                    text="Name der Session",
                ),
                mode="outlined",
                size_hint_x = .6,
                pos_hint={"center_x": 0.5}
            )

        self.add_widget(MDDialogContentContainer(
            MDDivider(),
            self.name_text_box,
            MDDivider(),
            orientation="vertical"
        ))

        self.add_widget(MDDialogButtonContainer(
            Widget(),
            MDButton(
                MDButtonText(text='Abbrechen'),
                style='tonal',
                on_press=self.dismiss
            ),
            MDButton(
                MDButtonText(text="Erstellen"),
                style="filled",
                on_press=self.on_yes
            ),
            spacing="10dp"
        ))

    def on_yes(self, instance):
        name = self.name_text_box.text
        # if not name:
        #     self.error_label.text = "Der Name darf nicht leer sein."
        # elif not pathvalidate.is_valid_filename(name):
        #     self.error_label.text = f"Der Name enthält unerlaubte Zeichen. Vorschlag: {pathvalidate.sanitize_filename(name)}"
        # elif name in self.sessionmenu.session_list_names:
        #     self.error_label.text = "Dieser Session-Name existiert bereits."
        # else:
        #     self.sessionmenu.newest_session_name = name
        
        #     self.dismiss()
        self.session_menu.grid_layout.remove_widget(self.session_menu.add_session_card)
        self.session_menu.grid_layout.add_widget(SessionCard(name, self.session_menu))
        self.session_menu.grid_layout.add_widget(self.session_menu.add_session_card)
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
        self.newest_session_name = None
        self.session_dict = {}
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
    
    def change_selected_session(self, session_card):
        if self.selected_session is not None:
            self.selected_session.is_selected = False
            self.selected_session.change_selected_status()
        if session_card.is_selected:
            self.selected_session = session_card
        else:
            self.selected_session = None
