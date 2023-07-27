from kivy.uix.scrollview import ScrollView
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.app import App

class MyApp(App):
    def build(self):
        layout = BoxLayout(orientation='vertical')

        self.scrollview = ScrollView(size_hint=(1, 0.8))
        self.box = BoxLayout(orientation='vertical', size_hint_y=None)
        self.box.bind(minimum_height=self.box.setter('height'))

        for i in range(10):
            self.box.add_widget(Label(text=f'Item {i+1}', size_hint_y=None, height=40))
        self.scrollview.add_widget(self.box)

        self.button = Button(text='Add Item and Scroll to it', size_hint=(1, 0.2))
        self.button.bind(on_press=self.add_item)

        layout.add_widget(self.button)
        layout.add_widget(self.scrollview)

        return layout

    def add_item(self, instance):
        new_item = Label(text=f'Item {len(self.box.children)+1}', size_hint_y=None, height=40)
        self.box.add_widget(new_item)

        # Calculate scroll_y value
        for item in self.box.children:
            print(item.text, item.pos)
        self.scrollview.scroll_y = (new_item.pos[1] / self.box.height)

MyApp().run()
