import os
import traceback
import weakref
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
from kivy.uix.screenmanager import Screen, ScreenManager, NoTransition
from frontend.widgets.pokemon_detail import PokemonDetailPanel
from kivy.uix.scrollview import ScrollView
from kivy.uix.togglebutton import ToggleButton
from frontend.widgets.connectionstatus import ObjectConnectionStatusCircle
from frontend.widgets.connectionstatus import ValueConnectionStatusCircle
from frontend.widgets.trainerbox import TrainerBox
from backend import pokedecoder
from backend.classes.obs import OBS
from backend.controller.connection_controller import ConnectionController
from backend.controller.randomizer_controller import RandomizerController
from backend.controller.settings_controller import SettingsController
import frontend.UIFactory as UI
from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/frontend.log')


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


class CrashReportPopup(Popup):
    def __init__(self, crash_log_path, **kwargs):
        super().__init__(**kwargs)
        self.title = "Crash erkannt"
        self.size_hint = (0.8, 0.4)
        self.auto_dismiss = False

        layout = BoxLayout(orientation="vertical", spacing="10dp")
        layout.add_widget(Label(text="Beim letzten Start ist ein Fehler aufgetreten.\nCrash-Log wurde gespeichert."))
        path_label = Label(text=os.path.abspath(crash_log_path), font_size="12sp")
        layout.add_widget(path_label)

        btn_layout = BoxLayout(size_hint_y=None, height="50dp", spacing="5dp")
        btn_copy = Button(text="Pfad kopieren", on_press=lambda _: self._copy_path(crash_log_path))
        btn_close = Button(text="Schließen", on_press=lambda _: self.dismiss())
        btn_layout.add_widget(btn_copy)
        btn_layout.add_widget(btn_close)
        layout.add_widget(btn_layout)
        self.content = layout

    def _copy_path(self, path):
        from kivy.core.clipboard import Clipboard
        Clipboard.copy(os.path.abspath(path))


class MainMenu(Screen):
    def __init__(
        self,
        arceus,
        bizhawk,
        citra,
        bizhawk_instances,
        munchlax,
        obs_websocket,
        overlay_server,
        configsave,
        sp,
        rem,
        obs,
        bh,
        pl,
        rnd,
        ov,
        app_version,
        **kwargs,
    ):
        self.arceus = arceus
        self.bizhawk = bizhawk
        self.citra = citra
        self.bizhawk_instances = bizhawk_instances
        self.munchlax = munchlax
        self.obs_websocket: OBS = obs_websocket
        self.overlay_server = overlay_server
        # Zähler für Button-Text-Downgrade (3-Poll-Grace gegen Flackern beim Verbinden)
        self._btn_sync_strikes = {"client": 0, "obs": 0}
        self.sp = sp
        self.rem = rem
        self.bh = bh
        self.pl = pl
        self.rnd = rnd
        self.ov = ov
        self.selected_session = ""
        self.app_version = app_version
        self.connectors = set()

        self.controller = SettingsController(configsave, sp, rem, obs, bh, pl, rnd, arceus, bizhawk, munchlax, obs_websocket, ov, overlay_server)
        self.connection = ConnectionController(arceus, bizhawk, citra, bizhawk_instances, munchlax, obs_websocket, bh, pl, overlay_server)
        self.randomizer = RandomizerController(rnd, pl, configsave=configsave)

        super().__init__(**kwargs)
        self.name = "MainMenu"

        self.clear_button = Button(
            text="Alle Clients zurücksetzen", on_press=self.clear_clients
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

        # Nested ScreenManager: TeamOverview ↔ eingebettetes PokemonDetailPanel.
        # Detail sitzt so nur im rechten Frame-Bereich (nicht fullscreen wie
        # der Top-Level-PokemonDetailScreen, den BoxMenu nutzt).
        team_screen = Screen(name="TeamOverview")
        team_screen.add_widget(self.pokemon_frame)
        detail_screen = Screen(name="PokemonDetailInline")
        self.pokemon_panel = PokemonDetailPanel(self.obs_websocket)
        detail_screen.add_widget(self.pokemon_panel)
        self.pokemon_sm = ScreenManager(transition=NoTransition())
        self.pokemon_sm.add_widget(team_screen)
        self.pokemon_sm.add_widget(detail_screen)

        frame.add_widget(self.pokemon_sm)
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
        self.ids["obs_button"] = weakref.proxy(obs_connect)
        buttons_box.add_widget(obs_connect)

        self.overlay_button = Button(text="Overlay starten", on_press=self.toggle_overlay)
        buttons_box.add_widget(self.overlay_button)

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

        UI.create_connection_status_with_state_text(
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
        UI.create_connection_status_with_state_text(
            obs_box, ObjectConnectionStatusCircle, self.obs_websocket
        )

        status_box.add_widget(obs_box)

        overlay_status_box = BoxLayout(orientation="horizontal")
        overlay_status_box.add_widget(Label(text="Overlay status:"))
        UI.create_connection_status_with_state_text(
            overlay_status_box, ObjectConnectionStatusCircle, self.overlay_server
        )
        status_box.add_widget(overlay_status_box)

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
            self.emulator_box.add_widget(Label(text="Emulator Status"))
        
        emulator_status_box = BoxLayout(orientation="horizontal")

        if game in ['X','Y','Omega Rubin','Alpha Saphir','Sonne', 'Mond','Ultra Sonne', 'Ultra Mond']:
            UI.create_connection_status_with_labels(
                emulator_status_box, ObjectConnectionStatusCircle, "Citra", self.citra
            )

        else:

            UI.create_connection_status_with_labels(
                emulator_status_box, ObjectConnectionStatusCircle, "Server", self.bizhawk
            )

            Clock.schedule_interval(
                lambda instance: self.change_bizhawk_status(emulator_status_box), 1
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

        checkmarks = (
            ("SaveRAM automatisch", "bizhawk_check"),
            ("Orden anzeigen", "badges_check"),
            ("Namen anzeigen", "names_check"),
            ("Items anzeigen", "items_check"),
            ("animierte Sprites", "animated_check"),
            ("KP-Leiste", "hp_bars_check"),
            ("Status-Effekte", "status_effects_check"),
            ("Verschiebung\nanimieren", "animate_reorder_check"),
        )
        show_layout = GridLayout(cols=2)

        for text, id in checkmarks:
            anchor = AnchorLayout(anchor_x="right", size_hint_x=0.3)
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

        info_buttons = BoxLayout(
            orientation="vertical", spacing="20dp", padding=("5dp", 0)
        )

        ueberschrift_info_buttons = Label(
            text=" Info & Rando",
            halign="left",
            size_hint_x=None,
            width=info_buttons.width,
            font_size="20sp",
        )
        info_buttons.add_widget(ueberschrift_info_buttons)

        pokedex_button = Button(text="Pokedex", on_press=self.switch_to_pokedex)
        info_buttons.add_widget(pokedex_button)

        box_button = Button(text="PC-Boxen", on_press=self.switch_to_boxes)
        info_buttons.add_widget(box_button)

        bag_button = Button(text="Tasche", on_press=self.switch_to_bag)
        info_buttons.add_widget(bag_button)

        encounter_button = Button(text="Encounters", on_press=self.switch_to_encounters)
        info_buttons.add_widget(encounter_button)

        nuzlocke_button = Button(text="Nuzlocke", on_press=self.switch_to_nuzlocke)
        info_buttons.add_widget(nuzlocke_button)

        self.randomize_button = Button(text="Randomisieren", on_press=self.start_randomization)
        info_buttons.add_widget(self.randomize_button)
        
        showing_frame.add_widget(info_buttons)

        showing_frame.add_widget(sort_layout)

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

    def _sync_connection_button_texts(self):
        """Setzt Verbindungs-Button-Texte zurück, wenn eine Verbindung endgültig weg ist
        (fehlgeschlagener Auto-Reconnect, OBS extern beendet). 3-Poll-Grace verhindert
        Flackern während laufender Connects; munchlax.reconnecting überbrückt den
        Auto-Reconnect (~35s)."""
        btn = self.ids.get("server_client_button")
        if (btn is not None and not self.rem["start_server"]
                and btn.text == "Client beenden"
                and not self.munchlax.is_connected
                and not self.munchlax.reconnecting):
            self._btn_sync_strikes["client"] += 1
            if self._btn_sync_strikes["client"] >= 3:
                btn.text = "Client starten"
                self._btn_sync_strikes["client"] = 0
                logger.info("Client-Button zurückgesetzt: Verbindung weg, kein Auto-Reconnect aktiv")
        else:
            self._btn_sync_strikes["client"] = 0

        obs_btn = self.ids.get("obs_button")
        if (obs_btn is not None and obs_btn.text == "OBS trennen"
                and not self.obs_websocket.is_connected):
            self._btn_sync_strikes["obs"] += 1
            if self._btn_sync_strikes["obs"] >= 3:
                obs_btn.text = "OBS verbinden"
                self._btn_sync_strikes["obs"] = 0
                logger.info("OBS-Button zurückgesetzt: WebSocket-Verbindung weg")
        else:
            self._btn_sync_strikes["obs"] = 0

    def change_munchlax_status(self, box):
        self._sync_connection_button_texts()
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
            for client_id, connected in self.munchlax.remote_connection_status.items():
                name = self.munchlax.remote_connection_names.get(client_id, client_id[:8])
                if client_id not in self.ids:
                    UI.create_connection_status_with_labels(
                        box,
                        ValueConnectionStatusCircle,
                        name,
                        client_id,
                        self.munchlax.remote_connection_status,
                        ids=self.ids,
                        id=client_id,
                    )
                else:
                    self.update_Munchlax_Connection_Label(client_id, name)

    def change_emulator_button(self):
        games_list = ['X','Y','Omega Rubin','Alpha Saphir','Sonne', 'Mond','Ultra Sonne', 'Ultra Mond']
        if self.pl["session_game"] in games_list and self.emulator.text.startswith("Bizhawk"):
            self.emulator.unbind(on_press=self.launchbh)
            self.emulator.text = "Citra verbinden"
            self.emulator.bind(on_press=self.connect_citra)
        elif self.emulator.text == "Citra verbinden" and self.pl["session_game"] not in games_list:
            self.emulator.unbind(on_press=self.connect_citra)
            self.emulator.text = "Bizhawk starten"
            self.emulator.bind(on_press=self.launchbh)

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
            self.emulator,
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
        self.connection.disconnect_all()

    def toggle_obs(self, instance):
        if instance.text == "OBS verbinden":
            task = self.connection.connect_obs()
            if task:
                self.connectors.add(task)
            instance.text = "OBS trennen"
        elif instance.text == "OBS trennen":
            task = self.connection.disconnect_obs()
            if task:
                self.connectors.add(task)
            instance.text = "OBS verbinden"

    def toggle_overlay(self, instance):
        if instance.text == "Overlay starten":
            task = self.connection.start_overlay()
            if task:
                self.connectors.add(task)
            instance.text = "Overlay beenden"
        elif instance.text == "Overlay beenden":
            task = self.connection.stop_overlay()
            if task:
                self.connectors.add(task)
            instance.text = "Overlay starten"

    def launchbh(self, instance):
        bizhawk_path = Path(self.bh["path"])
        if (
            bizhawk_path.exists()
            and bizhawk_path.is_file()
            and self.bh["path"].endswith(".exe")
        ):
            self.emulator.disabled = True
            if self.emulator.text == "Bizhawk starten":
                self.connection.start_bizhawk()
                self.emulator.text = "Bizhawk beenden"
            elif self.emulator.text == "Bizhawk beenden":
                popup = BizhawkSavePopup(self.bizhawk_instances, self.emulator, self.bizhawk)
                popup.open()

        Clock.schedule_once(lambda dt: setattr(self.emulator, 'disabled', False), 5)

    def connect_citra(self, instance):
        if not self.citra.started:
            self.citra.check_connection()
        if instance.text == "Citra verbinden" and self.citra.is_connected:
            instance.text = "Citra trennen"
            task = asyncio.create_task(self.citra.start(self.munchlax, instance))
            self.connectors.add(task)
        else:
            instance.text = "Citra verbinden"
            if not self.citra.player_number:
                self.citra.is_connected = False
                box = BoxLayout(orientation='vertical')
                box.add_widget(Label(text="Es wurde kein Spieler ausgewählt."))
                btn = Button(text='OK',size_hint=(.5,.4),pos_hint={'center_x':.5})
                box.add_widget(btn)

                popup = Popup(title='Spieler auswählen', content=box, size_hint=(None, None), size=(400, 150))

                btn.bind(on_release=popup.dismiss, on_press=self.switch_to_settings)
                popup.open()
            asyncio.create_task(self.citra.stop())

    def start_randomization(self, instance):
        self.randomize_button.disabled = True
        self.randomize_button.text = "Randomisierung..."
        task = asyncio.create_task(self._run_randomization())
        self.connectors.add(task)

    async def _run_randomization(self):
        try:
            success, message = await self.randomizer.randomize()
            if success:
                self._sync_rando_to_munchlax()
            title = "Randomisierung" if success else "Fehler"

            box = BoxLayout(orientation='vertical')
            box.add_widget(Label(text=message))
            btn = Button(text='OK', size_hint=(.5, .4), pos_hint={'center_x': .5})
            box.add_widget(btn)
            popup = Popup(title=title, content=box, size_hint=(None, None), size=(500, 300))
            btn.bind(on_release=popup.dismiss)
            popup.open()
        except Exception as err:
            logger.error(f"Fehler bei der Randomisierung: {type(err)}, {err}")
            logger.error(traceback.format_exc())
        finally:
            self.randomize_button.disabled = False
            self.randomize_button.text = "Randomisieren"

    def _sync_rando_to_munchlax(self):
        rando_data = self.randomizer.get_log_data()
        if rando_data:
            self.munchlax.rando_tm_moves = rando_data.tm_moves or None
            self.munchlax.rando_hm_moves = rando_data.hm_moves or None
            self._sync_rando_abilities(rando_data)
        else:
            self.munchlax.rando_tm_moves = None
            self.munchlax.rando_hm_moves = None
            pokedecoder.set_gen3_abilities(None)

    def _sync_rando_abilities(self, rando_data):
        lut: dict[int, list[int]] = {}
        for dexnr, poke in rando_data.pokemon.items():
            ids = []
            for name in poke.abilities:
                aid = pokedecoder.resolve_ability_name(name)
                if aid is not None:
                    ids.append(aid)
            if ids:
                lut[dexnr] = ids
        pokedecoder.set_gen3_abilities(lut if lut else None)

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
                self.connection.stop_server()
        if not initializing:
            self.save_changes(instance)

    def clear_clients(self, instance):
        self.munchlax.clear_everything()
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
            task = self.connection.connect_client()
            if task:
                self.connectors.add(task)
            if instance.text == "Client starten":
                instance.text = "Client beenden"
        elif instance.text == "Client beenden":
            self.connection.disconnect_client()
            instance.text = "Client starten"

    def launchserver(self, instance, *args):
        if instance.text == "Server starten":
            task = self.connection.start_server()
            if task:
                self.connectors.add(task)
            self.connect_client(instance)
            instance.text = "Server beenden"
        elif instance.text == "Server beenden":
            self.connection.stop_server()
            instance.text = "Server starten"

    def init_config(self, initializing=False):
        sp = self.controller.load_sprites()
        bh = self.controller.load_bizhawk()
        rem = self.controller.load_remote()
        pl = self.controller.load_player()

        self.ids.animated_check.state = "down" if sp["animated"] else "normal"
        self.ids.names_check.state = "down" if sp["show_nicknames"] else "normal"
        self.ids.items_check.state = "down" if sp["show_items"] else "normal"
        self.ids.hp_bars_check.state = "down" if sp.get("show_hp_bars") else "normal"
        self.ids.status_effects_check.state = "down" if sp.get("show_status_effects") else "normal"
        self.ids.animate_reorder_check.state = "down" if sp.get("animate_obs_reorder") else "normal"
        if pl["session_game"] in ['Sonne', 'Mond', 'Ultra Sonne', 'Ultra Mond']:
            self.ids.badges_check.disabled = True
            self.ids.badges_check.state = "normal"
            self.ids.bizhawk_check.disabled = True
            self.ids.bizhawk_check.state = "normal"
            self.sp["show_badges"] = False
        elif pl["session_game"] in ['X','Y','Alpha Saphir', 'Omega Rubin']:
            self.ids.bizhawk_check.disabled = True
            self.ids.bizhawk_check.state = "normal"
        else:
            self.ids.badges_check.disabled = False
            self.ids.badges_check.state = "down" if sp["show_badges"] else "normal"
        self.ids.bizhawk_check.state = "down" if bh["save_automatically"] else "normal"
        self.ids[sp["order"]].state = "down"
        self.ids["start_server"].state = "down" if rem["start_server"] else "normal"
        self.settings.text = f"Einstellungen\n{self.selected_session}"
        self.change_emulator_button()
        self.emulator_status_box(pl["session_game"])
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
        order = next((sorts[b.text] for b in toggle_widgets if b.state == "down"), None)
        del toggle_widgets

        animate_reorder = self.ids.animate_reorder_check.state == "down"
        values = {
            'animated': self.ids.animated_check.state == "down",
            'show_nicknames': self.ids.names_check.state == "down",
            'show_items': self.ids.items_check.state == "down",
            'show_badges': self.ids.badges_check.state == "down",
            'show_hp_bars': self.ids.hp_bars_check.state == "down",
            'show_status_effects': self.ids.status_effects_check.state == "down",
            'animate_obs_reorder': animate_reorder,
            'save_automatically': self.ids.bizhawk_check.state == "down",
            'start_server': self.ids["start_server"].state == "down",
        }
        if order:
            values['order'] = order

        self.controller.save_main_menu_settings(values)
        self.munchlax.change_order()

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

    def switch_to_pokedex(self, instance):
        self.manager.current = "PokedexMenu"

    def switch_to_boxes(self, instance):
        self.manager.current = "BoxMenu"

    def switch_to_bag(self, instance):
        self.manager.current = "BagMenu"

    def switch_to_encounters(self, instance):
        self.manager.current = "EncounterMenu"

    def switch_to_nuzlocke(self, instance):
        self.manager.current = "NuzlockeMenu"

    def switch_to_settings(self, instance):
        settings_menu = self.manager.get_screen("SettingsMenu")
        name_text_box = settings_menu.scrollview.ids.get("your_name", None)
        if name_text_box:
            name_text_box.disabled = self.munchlax.is_connected
        self.manager.current = "SettingsMenu"
