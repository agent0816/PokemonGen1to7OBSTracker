import os
import subprocess
import sys
import weakref
import yaml
import asyncio
from pathlib import Path
from kivy.clock import Clock
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.togglebutton import ToggleButton
from frontend.widgets.connectionstatus import ObjectConnectionStatusCircle
from frontend.widgets.connectionstatus import ValueConnectionStatusCircle
from frontend.widgets.trainerbox import TrainerBox
from backend.classes.obs import OBS
import frontend.UIFactory as UI
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging_formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")

file_handler = logging.FileHandler("logs/frontend.log", "w")
file_handler.setFormatter(logging_formatter)
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging_formatter)
logger.addHandler(stream_handler)


class BizhawkSavePopup(Popup):
    def __init__(self, bizhawk_instances, bizhawk_button, bizhawk, **kwargs):
        super().__init__(**kwargs)
        self.bizhawk_instances = bizhawk_instances
        self.bizhawk_button = bizhawk_button
        self.bizhawk = bizhawk
        self.canceled = False

        self.title = "Speichern nicht vergessen!"
        self.size_hint = (0.8, 0.4)

        layout = BoxLayout(orientation="vertical")
        layout.add_widget(Label(text="Hast du gespeichert??"))

        btn_layout = BoxLayout(size_hint_y=None, height="50dp", spacing="5dp")
        btn_yes = Button(text="Ja", on_press=self.on_yes)
        btn_no = Button(text="Nein", on_press=self.on_cancel)

        btn_layout.add_widget(btn_yes)
        btn_layout.add_widget(btn_no)
        layout.add_widget(btn_layout)

        self.content = layout

    def on_yes(self, instance):
        asyncio.create_task(self.bizhawk.stop_and_terminate(self.bizhawk_instances))

        self.bizhawk_button.text = "Bizhawk starten"
        self.dismiss()

    def on_cancel(self, instance):
        self.canceled = True
        self.dismiss()


class MainMenu(Screen):
    def __init__(
        self,
        arceus,
        bizhawk,
        citra,
        bizhawk_instances,
        munchlax,
        obs_websocket,
        configsave,
        sp,
        rem,
        obs,
        bh,
        pl,
        app_version,
        **kwargs,
    ):
        self.arceus = arceus
        self.bizhawk = bizhawk
        self.citra = citra
        self.bizhawk_instances = bizhawk_instances
        self.munchlax = munchlax
        self.obs_websocket: OBS = obs_websocket
        self.configsave = configsave
        self.sp = sp
        self.rem = rem
        self.obs = obs
        self.bh = bh
        self.pl = pl
        self.selected_session = ""
        self.app_version = app_version
        self.connectors = set()

        super().__init__(**kwargs)
        self.name = "MainMenu"

        self.clear_button = Button(
            text="Clients bereinigen", on_press=self.clear_clients
        )

        frame = BoxLayout(orientation="horizontal")
        click_frame = BoxLayout(orientation="vertical")

        control_frame = self.create_control_frame()
        showing_frame = self.create_showing_frame()

        click_frame.add_widget(control_frame)
        click_frame.add_widget(showing_frame)

        frame.add_widget(click_frame)

        self.pokemon_frame = ScrollView(do_scroll_y=False, do_scroll_x=True)
        self.create_pokemon_frame()

        frame.add_widget(self.pokemon_frame)
        self.add_widget(frame)

    def create_control_frame(self):
        control_frame = BoxLayout(orientation="horizontal")

        logo_settings = BoxLayout(orientation="vertical", size_hint=(0.3, 1))
        logo = Label(text=f"Logo\nVersion {self.app_version}")
        logo_settings.add_widget(logo)

        self.settings = Button(
            text=f"Einstellungen\n{self.selected_session}",
            on_press=self.switch_to_settings,
        )
        logo_settings.add_widget(self.settings)

        change_session = Button(text="Session wechseln", on_press=self.change_session)
        logo_settings.add_widget(change_session)

        control_frame.add_widget(logo_settings)

        connections = BoxLayout(orientation="horizontal")

        buttons_box = BoxLayout(orientation="vertical", size_hint= (0.4,1))
        self.ids["buttons_box"] = weakref.proxy(buttons_box)

        server_client_button = Button(text="Server/Client", on_press=self.launchserver)
        self.ids["server_client_button"] = weakref.proxy(server_client_button)
        buttons_box.add_widget(server_client_button)

        obs_connect = Button(text="OBS verbinden", on_press=self.toggle_obs)
        buttons_box.add_widget(obs_connect)

        self.emulator = Button(text="Bizhawk starten", on_press=self.launchbh)
        buttons_box.add_widget(self.emulator)

        connections.add_widget(buttons_box)

        status_box = BoxLayout(orientation="vertical")

        server_status_box = BoxLayout(orientation="vertical")
        status_label = Label(text="Status Server")

        server_status_box.add_widget(status_label)

        server_box = BoxLayout(orientation="horizontal")

        server_or_client_label = Label(text="Server?")
        server_box.add_widget(server_or_client_label)

        server_or_client_check = CheckBox(
            on_press=lambda instance: self.toggle_server_client(
                instance, server_client_button
            )
        )
        self.ids["start_server"] = weakref.proxy(server_or_client_check)

        server_box.add_widget(server_or_client_check)

        UI.create_connection_status(
            server_box,
            ObjectConnectionStatusCircle,
            self.arceus,
            ids=self.ids,
            id="arceus_status",
        )

        server_status_box.add_widget(server_box)

        status_box.add_widget(server_status_box)

        obs_box = BoxLayout(orientation="horizontal")

        obs_box.add_widget(Label(text="OBS status:"))
        UI.create_connection_status(
            obs_box, ObjectConnectionStatusCircle, self.obs_websocket
        )

        status_box.add_widget(obs_box)

        # verbundene Munchlaxes

        munchlax_box = BoxLayout(orientation="vertical")
        munchlax_box_label = Label(text="Client Status")
        munchlax_box.add_widget(munchlax_box_label)

        munchlax_status_box = BoxLayout(orientation="horizontal")
        self.ids["munchlax_status_box"] = weakref.proxy(munchlax_status_box)
        UI.create_connection_status_with_labels(
            munchlax_status_box,
            ObjectConnectionStatusCircle,
            self.munchlax.client_id[0],
            self.munchlax,
            ids=self.ids,
            id=self.munchlax.client_id,
        )

        Clock.schedule_interval(
            lambda instance: self.change_munchlax_status(munchlax_status_box), 1
        )

        munchlax_box.add_widget(munchlax_status_box)

        status_box.add_widget(munchlax_box)

        # lokale Bizhawks und Bizhawk-server

        self.emulator_box = BoxLayout(orientation="vertical")

        self.emulator_box.add_widget(Label(text="Emulator Status"))

        status_box.add_widget(self.emulator_box)

        connections.add_widget(status_box)

        control_frame.add_widget(connections)

        return control_frame

    def emulator_status_box(self, game):
        if self.emulator_box.children:
            self.emulator_box.clear_widgets()
        
        emulator_status_box = BoxLayout(orientation="horizontal")

        if game in ['X','Y','Omega Rubin','Alpha Saphir','Sonne', 'Mond','Ultra Sonne', 'Ultra Mond']:
            UI.create_connection_status_with_labels(
                emulator_status_box, ObjectConnectionStatusCircle, "Citra", self.citra
            )

            emulator_status_check = self.change_citra_status

        else:

            UI.create_connection_status_with_labels(
                emulator_status_box, ObjectConnectionStatusCircle, "Server", self.bizhawk
            )

            emulator_status_check = self.change_bizhawk_status

        Clock.schedule_interval(
            lambda instance: emulator_status_check(emulator_status_box), 1
        )

        self.emulator_box.add_widget(emulator_status_box)

    def create_showing_frame(self):
        showing_frame = BoxLayout(orientation="horizontal")
        sort_layout = BoxLayout(
            orientation="vertical", spacing="20dp", padding=("5dp", 0)
        )

        ueberschrift_sortierung = Label(
            text="Sortierung",
            halign="left",
            size_hint_x=None,
            width=sort_layout.width,
            font_size="20sp",
        )
        sort_layout.add_widget(ueberschrift_sortierung)

        sorts = (
            ("DexNr.", "dexnr"),
            ("Team", "team"),
            ("Level", "lvl"),
            ("Route", "route"),
        )
        for text, id in sorts:
            toggler = ToggleButton(
                text=text,
                group="sort",
                allow_no_selection=False,
                on_press=self.save_changes,
            )
            self.ids[id] = weakref.proxy(toggler)
            sort_layout.add_widget(toggler)

        showing_frame.add_widget(sort_layout)

        checkmarks = (
            ("SaveRAM automatisch", "bizhawk_check"),
            ("Orden anzeigen", "badges_check"),
            ("Namen anzeigen", "names_check"),
            ("Items anzeigen", "items_check"),
            ("animierte Sprites", "animated_check"),
        )
        show_layout = GridLayout(cols=2)

        for text, id in checkmarks:
            anchor = AnchorLayout(anchor_x="right", size_hint_x=0.5)
            checkbox = CheckBox(
                size_hint=(None, None),
                size=("20dp", "20dp"),
                on_press=self.save_changes,
            )
            self.ids[id] = weakref.proxy(checkbox)

            anchor.add_widget(checkbox)
            show_layout.add_widget(anchor)

            label = Label(text=text)
            show_layout.add_widget(label)

        showing_frame.add_widget(show_layout)

        return showing_frame

    def create_pokemon_frame(self):
        box = BoxLayout(
            orientation="horizontal",
            size_hint_x=None,
            spacing="20dp",
            padding=(0, "20dp"),
        )
        box.bind(minimum_width=box.setter("width"))  # type: ignore

        color_lut = {1: "1a4d9a7f", 2: "9a671a7f", 3: "9a9a1a7f", 4: "1a9a1a7f"}

        for player in range(1, self.pl["player_count"] + 1):
            trainer = TrainerBox(
                player,
                self.munchlax,
                self.obs_websocket,
                self,
                color_lut[player],
                orientation="vertical",
                size_hint=(None, 1),
                size=("140dp", 0),
            )
            box.add_widget(trainer)

        self.pokemon_frame.add_widget(box)

    def change_munchlax_status(self, box):
        if self.rem["start_server"]:
            for client_id in self.arceus.munchlax_status:
                name = self.arceus.munchlax_names[client_id]
                if client_id not in self.ids:
                    UI.create_connection_status_with_labels(
                        box,
                        ValueConnectionStatusCircle,
                        name,
                        client_id,
                        self.arceus.munchlax_status,
                        ids=self.ids,
                        id=client_id,
                    )
                else:
                    self.update_Munchlax_Connection_Label(client_id, name)
        else:
            if (
                self.munchlax.is_connected
                and self.ids["server_client_button"].text != "Client beenden"
            ):
                self.ids["server_client_button"].text = "Client beenden"

    def change_emulator_button(self):
        if self.pl["session_game"] in ['X','Y','Omega Rubin','Alpha Saphir','Sonne', 'Mond','Ultra Sonne', 'Ultra Mond']:
            self.emulator.unbind(on_press=self.launchbh)
            self.emulator.text = "Citra verbinden"
            self.emulator.bind(on_press=self.connect_citra)

    def change_citra_status(self, box):
        self.citra.check_connection()
    
    def change_bizhawk_status(self, box):
        for client_id in self.bizhawk.bizhawks_status:
            if client_id not in self.ids:
                UI.create_connection_status_with_labels(
                    box,
                    ValueConnectionStatusCircle,
                    client_id,
                    client_id,
                    self.bizhawk.bizhawks_status,
                    ids=self.ids,
                    id=client_id,
                )

    def change_session(self, instance):
        popup = BizhawkSavePopup(
            self.bizhawk_instances,
            instance,
            self.bizhawk,
            on_dismiss=lambda popup: self.session_popup(popup),
        )
        if self.bizhawk_instances:
            popup.open()
        else:
            self.session_popup(popup)

    def session_popup(self, popup):
        if not popup.canceled:
            self.disconnect_all()

            self.save_changes(popup)
            session_menu = self.manager.get_screen("SessionMenu")
            session_menu.load_session_config(default=True)

            self.manager.current = "SessionMenu"

    def disconnect_all(self):
        tasks = [
            asyncio.create_task(self.bizhawk.stop()),
            asyncio.create_task(self.obs_websocket.disconnect()),
            asyncio.create_task(self.munchlax.disconnect()),
            asyncio.create_task(self.arceus.stop()),
        ]
        asyncio.create_task(asyncio.wait(tasks, timeout=3))

    def toggle_obs(self, instance):
        if instance.text == "OBS verbinden":
            self.connectOBS()
            instance.text = "OBS trennen"
        elif instance.text == "OBS trennen":
            self.disconnectOBS()
            instance.text = "OBS verbinden"

    def connectOBS(self, *args):
        if not self.obs_websocket.is_connected:
            task = asyncio.create_task(self.obs_websocket.load_obsws())
            self.connectors.add(task)
            logger.info("OBS connected.")

    def disconnectOBS(self, *args):
        if self.obs_websocket.is_connected:
            task = asyncio.create_task(self.obs_websocket.disconnect())
            self.connectors.add(task)
            logger.info("OBS disconnected.")

    def launchbh(self, instance):
        bizhawk_path = Path(self.bh["path"])
        if (
            bizhawk_path.exists()
            and bizhawk_path.is_file()
            and self.bh["path"].endswith(".exe")
        ):
            self.emulator.disabled = True
            if self.emulator.text == "Bizhawk starten":
                if not self.bizhawk.server:
                    task = asyncio.create_task(self.bizhawk.start(self.munchlax))
                    self.connectors.add(task)
                for i in range(self.pl["player_count"]):
                    if not self.pl[f"remote_{i+1}"]:
                        process = subprocess.Popen(
                            [
                                self.bh["path"],
                                f'--lua={os.path.abspath(f"./backend/lua/Player{i+1}.lua")}',
                                f'--socket_ip={self.bh["host"]}',
                                f'--socket_port={self.bh["port"]}',
                                # f'--chromeless',
                            ]
                        )
                        self.bizhawk_instances.append(process)
                self.emulator.text = "Bizhawk beenden"
            elif self.emulator.text == "Bizhawk beenden":
                popup = BizhawkSavePopup(self.bizhawk_instances, self.emulator, self.bizhawk)
                popup.open()

        def enable_button(button):
            button.disabled = False

        Clock.schedule_once(lambda dt: enable_button(self.emulator), 5)

    def connect_citra(self, instance):
        self.citra.check_connection()
        if instance.text == "Citra verbinden" and self.citra.is_connected:
            instance.text = "Citra trennen"
            task = asyncio.create_task(self.citra.start(self.munchlax))
            self.connectors.add(task)
        else:
            instance.text = "Citra verbinden"
            if self.citra.is_connected:
                asyncio.create_task(self.citra.stop())

    def toggle_server_client(self, instance, button, initializing=False):
        if instance.state == "down":
            button.text = "Server starten"
            button.unbind(on_press=self.connect_client)
            button.bind(on_press=self.launchserver)
            self.ids["arceus_status"].opacity = 1

            if self.clear_button not in self.ids["buttons_box"].children:
                self.ids["buttons_box"].add_widget(self.clear_button)
        else:
            button.text = "Client starten"
            button.unbind(on_press=self.launchserver)
            button.bind(on_press=self.connect_client)
            self.ids["arceus_status"].opacity = 0
            if not initializing and self.clear_button in self.ids["buttons_box"].children:
                self.ids["buttons_box"].remove_widget(self.clear_button)
            if self.rem["start_server"]:
                asyncio.create_task(self.munchlax.disconnect())
                asyncio.create_task(self.arceus.stop())
        if not initializing:
            self.save_changes(instance)

    def clear_clients(self, instance):
        box = self.ids["munchlax_status_box"]
        id_list = [id for id in self.arceus.munchlaxes]
        if not self.munchlax.is_connected:
            id_list.append(self.munchlax.client_id)

        widgets_zu_entfernen = []
        ids_to_remove = []
        for id in box.ids:
            if id not in id_list:
                widgets_zu_entfernen.append(box.ids[id])
                ids_to_remove.append(id)

        for widget in widgets_zu_entfernen:
            box.remove_widget(widget)

        for id in ids_to_remove:
            del box.ids[id]

    def connect_client(self, instance, *args):
        if instance.text.endswith("starten"):
            if not self.munchlax.is_connected:
                task = asyncio.create_task(self.munchlax.connect())
                self.connectors.add(task)
            if instance.text == "Client starten":
                instance.text = "Client beenden"
        elif instance.text == "Client beenden":
            if self.munchlax.is_connected:
                asyncio.create_task(self.munchlax.disconnect())
            instance.text = "Client starten"

    def launchserver(self, instance, *args):
        if instance.text == "Server starten":
            if not self.arceus.server:
                asyncio.create_task(self.arceus.start())
            self.connect_client(instance)
            instance.text = "Server beenden"
        elif instance.text == "Server beenden":
            if self.arceus.server:
                asyncio.create_task(self.munchlax.disconnect())
                asyncio.create_task(self.arceus.stop())
            instance.text = "Server starten"

    def init_config(self, initializing=False):
        self.ids.animated_check.state = "down" if self.sp["animated"] else "normal"
        self.ids.names_check.state = "down" if self.sp["show_nicknames"] else "normal"
        self.ids.items_check.state = "down" if self.sp["show_items"] else "normal"
        if self.pl["session_game"] in ['Sonne', 'Mond','Ultra Sonne', 'Ultra Mond']:
            self.ids.badges_check.disabled = True
            self.ids.badges_check.state = "normal"
            self.sp["show_badges"] = False
        else:
            self.ids.badges_check.state = "down" if self.sp["show_badges"] else "normal"
        self.ids.bizhawk_check.state = (
            "down" if self.bh["save_automatically"] else "normal"
        )
        self.ids[self.sp["order"]].state = "down"
        self.ids["start_server"].state = (
            "down" if self.rem["start_server"] else "normal"
        )
        self.settings.text = f"Einstellungen\n{self.selected_session}"
        self.change_emulator_button()
        self.toggle_server_client(
            self.ids["start_server"],
            self.ids["server_client_button"],
            initializing=initializing,
        )
        self.munchlax.change_order()
        self.update_munchlax_connections()

    def save_changes(self, instance):
        sorts = {"DexNr.": "dexnr", "Team": "team", "Level": "lvl", "Route": "route"}
        toggle_widgets = ToggleButton.get_widgets("sort")
        for button in toggle_widgets:
            if button.state == "down":
                self.sp["order"] = sorts[button.text]

        del toggle_widgets

        self.sp["animated"] = self.ids.animated_check.state == "down"
        self.sp["show_nicknames"] = self.ids.names_check.state == "down"
        self.sp["show_items"] = self.ids.items_check.state == "down"
        self.sp["show_badges"] = self.ids.badges_check.state == "down"
        self.munchlax.change_order()
        asyncio.create_task(self.obs_websocket.redraw_obs())
        with open(f"{self.configsave}sprites.yml", "w") as file:
            yaml.dump(self.sp, file)

        self.bh["save_automatically"] = self.ids.bizhawk_check.state == "down"

        with open(f"{self.configsave}bh_config.yml", "w") as file:
            yaml.dump(self.bh, file)

        self.rem["start_server"] = self.ids["start_server"].state == "down"

        self.update_munchlax_connections()

        with open(f"{self.configsave}remote.yml", "w") as file:
            yaml.dump(self.rem, file)

    def update_munchlax_connection_circle(self):
        client_id = self.munchlax.client_id
        name = self.pl.get("your_name", "TBD")
        self.update_Munchlax_Connection_Label(client_id, name)

    def update_Munchlax_Connection_Label(self, client_id, name):
        if client_id in self.ids:
            circle = self.ids.get(client_id, None)
            if circle:
                for widget in circle.children:
                    if type(widget) == Label:
                        widget.text = name

    def update_munchlax_connections(self):
        ip_to_connect = (
            "127.0.0.1" if self.rem["start_server"] else self.rem["server_ip_adresse"]
        )
        port_to_connect = (
            self.rem["client_port"]
            if self.rem["start_server"]
            else self.rem["server_port"]
        )

        self.munchlax.host = ip_to_connect
        self.munchlax.port = port_to_connect

    def start_or_end_arceus(self, instance):
        if instance.state == "down":
            if not self.rem["start_server"]:
                asyncio.create_task(self.munchlax.disconnect())
                asyncio.create_task(self.arceus.stop())

    def switch_to_settings(self, instance):
        settings_menu = self.manager.get_screen("SettingsMenu")
        name_text_box = settings_menu.scrollview.ids.get("your_name", None)
        if name_text_box:
            name_text_box.disabled = self.munchlax.is_connected
        self.manager.current = "SettingsMenu"
