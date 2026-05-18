import weakref
from kivy.clock import Clock
from kivy.graphics import Color
from kivy.graphics import Rectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from backend.classes.munchlax import Munchlax
from backend.classes.obs import OBS

STATUS_LABELS = {
    'freeze': ('[color=3068B0]FRZ[/color]', (0.19, 0.41, 0.69, 1)),
    'burn':   ('[color=E85030]BRN[/color]', (0.91, 0.31, 0.19, 1)),
    'para':   ('[color=FAD300]PAR[/color]', (0.98, 0.83, 0.0, 1)),
    'toxic':  ('[color=A040A0]TOX[/color]', (0.63, 0.25, 0.63, 1)),
    'poison': ('[color=A040A0]PSN[/color]', (0.63, 0.25, 0.63, 1)),
    'sleep':  ('[color=98D8D8]SLP[/color]', (0.6, 0.85, 0.85, 1)),
}
STATUS_PRIORITY = ('freeze', 'burn', 'para', 'toxic', 'poison', 'sleep')

class PokemonBox(ButtonBehavior, BoxLayout):
    def __init__(self, obs_websocket: OBS, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "horizontal"

        info_box = BoxLayout(orientation="vertical")
        
        nickname = Label(text="nickname")
        self.ids["Nickname"] = weakref.proxy(nickname)
        info_box.add_widget(nickname)

        level = Label(text="lvl. 0")
        self.ids["Level"] = weakref.proxy(level)
        info_box.add_widget(level)

        hp = Label(text="1/1")
        self.ids["hp_text"] = weakref.proxy(hp)
        info_box.add_widget(hp)

        progress_bar = ProgressBar(max=1, value=1, size_hint_x=0.8, pos_hint={'center_x':0.5})
        self.ids["hp_bar"] = weakref.proxy(progress_bar)
        info_box.add_widget(progress_bar)

        item_name = Label(text="-")
        self.ids["Item_Name"] = weakref.proxy(item_name)
        info_box.add_widget(item_name)

        status_label = Label(text="", markup=True, font_size="12sp", size_hint_y=0.4)
        self.ids["Status"] = weakref.proxy(status_label)
        info_box.add_widget(status_label)

        self.add_widget(info_box)

        sprite_box = BoxLayout(orientation = "vertical")

        sprite = Image(
            source=f"{obs_websocket.conf['common_path']}/{obs_websocket.conf['red']}/0.png",
            fit_mode="contain",
        )
        self.ids["Sprite"] = weakref.proxy(sprite)
        sprite_box.add_widget(sprite)
        
        item_image = Image(
            source=f"{obs_websocket.conf['items_path']}/0.png", fit_mode="contain",
            size_hint=(1,0.4)
        )
        self.ids["Item_Image"] = weakref.proxy(item_image)
        sprite_box.add_widget(item_image)

        self.add_widget(sprite_box)

class TrainerBox(BoxLayout):
    def __init__(self, player_id, munchlax, obs_websocket, screen, color, **kwargs):
        super().__init__(**kwargs)

        self.player_id = player_id
        self.munchlax: Munchlax = munchlax
        self.obs_websocket: OBS = obs_websocket
        self.pokemon_boxes = {}
        self.badges = {}
        self.badge_lut = {
            11: "kanto",
            12: "kanto",
            13: "kanto",
            21: "johto",
            22: "johto",
            23: "johto",
            31: "hoenn",
            32: "hoenn",
            33: "hoenn",
            34: "kanto",
            35: "kanto",
            41: "sinnoh",
            42: "sinnoh",
            43: "sinnoh",
            44: "johto",
            45: "johto",
            51: "unova",
            52: "unova",
            53: "unova2",
            54: "unova2",
            61: "kalos",
            62: "kalos",
            63: "hoenn",
            64: "hoenn",
            71:'alola',
            72:'alola',
            73:'alola',
            74:'alola',
        }
        self.edition_lut = {
            "Rot" : 11,
            "Blau": 12,
            "Gelb": 13,
            "Gold": 21,
            "Silber": 22,
            "Kristall": 23,
            "Rubin": 31,
            "Saphir": 32,
            "Smaragd": 33,
            "Feuerrot": 34,
            "Blattgrün": 35,
            "Diamant": 41,
            "Perl": 42,
            "Platin": 43,
            "Herzgold": 44,
            "Seelensilber": 45,
            "Schwarz": 51,
            "Weiß": 52,
            "Schwarz 2": 53,
            "Weiß 2": 54,
            "X": 61,
            "Y": 62,
            "Omega Rubin": 63,
            "Alpha Saphir": 64
        }
        self.screen = screen
        self.screen.ids[f"trainer_box_{player_id}"] = weakref.proxy(self)

        decimal_color = (
            int(color[0:2], 16) / 255,
            int(color[2:4], 16) / 255,
            int(color[4:6], 16) / 255,
            int(color[6:8], 16) / 255,
        )

        with self.canvas.before:  # type: ignore
            Color(*decimal_color)
            self.rect = Rectangle(size=self.size, pos=self.pos)

        self.bind(size=self._update_rect, pos=self._update_rect)  # type: ignore

        self.name_label = Label(text=f"Spieler {self.player_id}", size_hint=(1, 0.3))
        self.add_widget(self.name_label)

        for slot in range(6):
            pokemon_box = PokemonBox(self.obs_websocket)
            pokemon_box.bind(on_press=lambda instance, s=slot: self._show_detail(s))
            self.pokemon_boxes[f"slot{slot}"] = pokemon_box
            self.add_widget(pokemon_box)

        badge_box = self.create_badge_box()

        self.add_widget(badge_box)

        Clock.schedule_interval(self.team_aktualisieren, 1)

    def _show_detail(self, slot):
        if self.player_id not in self.munchlax.sorted_teams:
            return
        team = self.munchlax.sorted_teams[self.player_id]
        if slot >= len(team):
            return
        pokemon = team[slot]
        edition = self.munchlax.editions.get(self.player_id, 0)
        rando_data = None
        rando_getter = None
        if hasattr(self.screen, 'randomizer'):
            rando_data = self.screen.randomizer.get_log_data()
            rando_getter = self.screen.randomizer.get_log_data
        detail = self.screen.pokemon_detail_screen
        detail.show(
            pokemon, edition, rando_data,
            back_callback=lambda: setattr(self.screen.pokemon_sm, 'current', 'TeamOverview'),
        )
        detail.set_refresh_source(self.player_id, slot, self.munchlax, rando_getter)
        self.screen.pokemon_sm.current = "PokemonDetail"

    def _update_rect(self, instance, value):
        self.rect.pos = instance.pos
        self.rect.size = instance.size

    def create_badge_box(self):
        badge_box = BoxLayout(orientation='vertical')
        first_half = BoxLayout(orientation='horizontal')
        second_half = BoxLayout(orientation='horizontal')

        edition = self.edition_lut.get(self.munchlax.pl.get("session_game"), 11)
        region = self.badge_lut.get(edition)

        for badge in range(1,9):
            badge_image = Image(source=f"{self.obs_websocket.conf['badges_path']}/{region}{badge}empty.png", fit_mode='contain')
            self.badges[badge] = badge_image
            if badge < 5:
                first_half.add_widget(badge_image)
            else:
                second_half.add_widget(badge_image)

        badge_box.add_widget(first_half)
        badge_box.add_widget(second_half)

        return badge_box


    def team_aktualisieren(self, instance):
        display_name = self.munchlax.player_names.get(self.player_id, f"Spieler {self.player_id}")
        if self.name_label.text != display_name:
            self.name_label.text = display_name

        if self.screen.name == "SessionMenu" or self.player_id not in self.munchlax.sorted_teams or self.player_id not in self.munchlax.editions or self.player_id not in self.munchlax.badges:
            return
        else:
            self.old_team = self.munchlax.sorted_teams[self.player_id]

        new_team = self.munchlax.sorted_teams[self.player_id]

        for slot, pokemon in enumerate(new_team):
            slot_box = self.pokemon_boxes[f"slot{slot}"]

            slot_box.ids["Level"].text = f"lvl {pokemon.lvl}"
            slot_box.ids["Nickname"].text = pokemon.nickname
            sprite_widget = slot_box.ids["Sprite"]
            sprite_path = self.obs_websocket.get_sprite(pokemon,False,self.munchlax.editions[self.player_id], two_pc=False) # self.obs_websocket.conf["animated"]
            if sprite_widget.source != sprite_path:
                sprite_widget.source = sprite_path
            slot_box.ids["Item_Name"].text = "-" if pokemon.item == 0 else f"{pokemon.item}"
            slot_box.ids["Item_Image"].source = f"{self.obs_websocket.conf['items_path']}/{pokemon.item}.png"
            slot_box.ids["hp_bar"].max = pokemon.max_hp
            slot_box.ids["hp_bar"].value = pokemon.cur_hp
            slot_box.ids["hp_text"].text = f"{pokemon.cur_hp}/{pokemon.max_hp}"

            fainted = pokemon.cur_hp == 0 and pokemon.dexnr not in (0, 'egg') and pokemon.max_hp > 0
            sprite_widget.color = (0.4, 0.4, 0.4, 1) if fainted else (1, 1, 1, 1)

            pct = pokemon.cur_hp / max(pokemon.max_hp, 1)
            if pct > 0.5:
                hp_color = (0.3, 0.69, 0.31, 1)
            elif pct > 0.2:
                hp_color = (1.0, 0.6, 0.0, 1)
            else:
                hp_color = (0.96, 0.26, 0.21, 1)
            hp_bar = slot_box.ids["hp_bar"]
            hp_bar.canvas.after.clear()
            with hp_bar.canvas.after:
                Color(*hp_color)
                Rectangle(
                    pos=hp_bar.pos,
                    size=(hp_bar.width * pct, hp_bar.height),
                )

            status = getattr(pokemon, 'status', {})
            status_text = ''
            for key in STATUS_PRIORITY:
                if status.get(key):
                    status_text = STATUS_LABELS[key][0]
                    break
            slot_box.ids["Status"].text = status_text

        self.old_team = new_team

        if (hasattr(self.screen, 'pokemon_sm')
                and self.screen.pokemon_sm.current == "PokemonDetail"
                and hasattr(self.screen, 'pokemon_detail_screen')
                and self.screen.pokemon_detail_screen._refresh_player_id == self.player_id):
            self.screen.pokemon_detail_screen.refresh_if_active()

        badges = self.munchlax.badges[self.player_id]
        badge_string = f"{self.obs_websocket.conf['badges_path']}"

        for badge in range(8):
            if badges & 2**badge:
                self.badges[badge+1].source = f"{badge_string}/{self.badge_lut[self.munchlax.editions[self.player_id]]}{badge+1}.png"
            else:
                self.badges[badge+1].source = f"{badge_string}/{self.badge_lut[self.munchlax.editions[self.player_id]]}{badge+1}empty.png"