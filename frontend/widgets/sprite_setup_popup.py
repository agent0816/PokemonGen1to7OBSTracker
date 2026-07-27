import asyncio
import os
import traceback
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from backend.sprite_repo import clone_sprite_repo, apply_sprite_paths
from backend.logging_setup import get_logger
from frontend.widgets.file_picker import pick_directory

logger = get_logger(__name__, 'logs/frontend.log')

DEFAULT_CLONE_PATH = os.path.abspath("sprites")


class SpriteSetupPopup(Popup):
    def __init__(self, sp: dict, configsave, save_callback=None, **kwargs):
        super().__init__(**kwargs)
        self.sp = sp
        self.configsave = configsave
        self.save_callback = save_callback

        self.title = "Sprite-Dateien herunterladen"
        self.size_hint = (None, None)
        self.size = (550, 300)
        self.auto_dismiss = False

        layout = BoxLayout(orientation='vertical', spacing="10dp", padding="10dp")

        info_text = (
            "Die Sprite-Dateien können automatisch von GitHub\n"
            "heruntergeladen werden. Wähle einen Ordner, in den\n"
            "das Sprite-Repository geklont werden soll.\n\n"
            "Vorgeschlagener Pfad: sprites/ (neben der Anwendung)"
        )
        layout.add_widget(Label(text=info_text, size_hint_y=0.5))

        self.status_label = Label(text="", size_hint_y=0.2, color=(1, 1, 0, 1))
        layout.add_widget(self.status_label)

        btn_layout = BoxLayout(size_hint_y=None, height="40dp", spacing="10dp")

        btn_download = Button(text="Herunterladen", on_press=self.on_download)
        btn_cancel = Button(text="Abbrechen", on_press=self.dismiss)

        btn_layout.add_widget(btn_download)
        btn_layout.add_widget(btn_cancel)

        layout.add_widget(btn_layout)
        self.content = layout

    def on_download(self, instance):
        pick_directory(
            on_select=lambda target: self._start_clone(instance, target),
            start_path=os.path.dirname(DEFAULT_CLONE_PATH),
            title="Zielordner für Sprite-Repository wählen",
        )

    def _start_clone(self, instance, target: str):
        if not target:
            return

        clone_target = os.path.join(target, "sprites")
        if os.path.exists(clone_target) and os.listdir(clone_target):
            self.status_label.text = f"Ordner existiert bereits: {clone_target}"
            self.status_label.color = (1, 0, 0, 1)
            return

        self.status_label.text = "Klone Repository... Bitte warten."
        self.status_label.color = (1, 1, 0, 1)
        instance.disabled = True

        asyncio.create_task(self._do_clone(clone_target))

    async def _do_clone(self, clone_target: str):
        try:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(None, clone_sprite_repo, clone_target)

            if success:
                apply_sprite_paths(self.sp, clone_target)
                if self.save_callback:
                    self.save_callback()
                self.status_label.text = "Erfolgreich heruntergeladen!"
                self.status_label.color = (0, 1, 0, 1)

                await asyncio.sleep(1.5)
                self.dismiss()
            else:
                self.status_label.text = "Fehler beim Klonen. Siehe Log."
                self.status_label.color = (1, 0, 0, 1)
        except Exception as err:
            logger.error(f"Fehler im Clone-Task: {err}")
            logger.error(traceback.format_exc())
            self.status_label.text = "Unerwarteter Fehler. Siehe Log."
            self.status_label.color = (1, 0, 0, 1)
