import weakref
from kivy.clock import Clock
from kivy.graphics import Color
from kivy.graphics import Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from backend.classes.munchlax import Munchlax
from backend.classes.obs import OBS

class PokemonBox(BoxLayout):
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
        }
        screen.ids[f"trainer_box_{player_id}"] = weakref.proxy(self)

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

        self.add_widget(Label(text=f"Spieler {self.player_id}", size_hint=(1, 0.3)))

        for pokemon in range(6):
            pokemon_box = PokemonBox(self.obs_websocket)
            self.pokemon_boxes[f"slot{pokemon}"] = pokemon_box

            self.add_widget(pokemon_box)

        badge_box = self.create_badge_box()

        self.add_widget(badge_box)

        Clock.schedule_interval(self.team_aktualisieren, 1)

    def _update_rect(self, instance, value):
        self.rect.pos = instance.pos
        self.rect.size = instance.size

    def create_badge_box(self):
        badge_box = BoxLayout(orientation='vertical')
        first_half = BoxLayout(orientation='horizontal')
        second_half = BoxLayout(orientation='horizontal')

        for badge in range(1,9):
            badge_image = Image(source=f"{self.obs_websocket.conf['badges_path']}/kanto{badge}empty.png", fit_mode='contain')
            self.badges[badge] = badge_image
            if badge < 5:
                first_half.add_widget(badge_image)
            else:
                second_half.add_widget(badge_image)

        badge_box.add_widget(first_half)
        badge_box.add_widget(second_half)

        return badge_box


    def team_aktualisieren(self, instance):
        if self.player_id not in self.munchlax.sorted_teams or self.player_id not in self.munchlax.editions or self.player_id not in self.munchlax.badges:
            return

        team = self.munchlax.sorted_teams[self.player_id]
        # items4 = yaml.safe_load(open('backend/data/items4.yml'))
        # team = [Pokemon(i+24, female=True, lvl=i+30, item=items4[i+22], nickname=f"nickname {i}") for i in range(1,7)]

        for slot, pokemon in enumerate(team):
            slot_box = self.pokemon_boxes[f"slot{slot}"]

            slot_box.ids["Level"].text = f"lvl {pokemon.lvl}"
            slot_box.ids["Nickname"].text = pokemon.nickname
            slot_box.ids["Sprite"].source = self.obs_websocket.get_sprite(pokemon,self.obs_websocket.conf["animated"],self.munchlax.editions[self.player_id], two_pc=False)
            slot_box.ids["Item_Name"].text = "-" if pokemon.item == 0 else f"{pokemon.item}"
            slot_box.ids["Item_Image"].source = f"{self.obs_websocket.conf['items_path']}/{pokemon.item}.png"
            slot_box.ids["hp_bar"].max = pokemon.max_hp
            slot_box.ids["hp_bar"].value = pokemon.cur_hp
            slot_box.ids["hp_text"].text = f"{pokemon.cur_hp}/{pokemon.max_hp}"

        badges = self.munchlax.badges[self.player_id]
        badge_string = f"{self.obs_websocket.conf['badges_path']}"

        for badge in range(8):
            if badges & 2**badge:
                self.badges[badge+1].source = f"{badge_string}/{self.badge_lut[self.munchlax.editions[self.player_id]]}{badge+1}.png"
            else:
                self.badges[badge+1].source = f"{badge_string}/{self.badge_lut[self.munchlax.editions[self.player_id]]}{badge+1}empty.png"