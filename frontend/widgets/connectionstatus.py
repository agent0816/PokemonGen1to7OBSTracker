from kivy.clock import Clock
from kivy.graphics import Color
from kivy.graphics import Ellipse
from kivy.properties import BooleanProperty
from kivy.properties import ListProperty
from kivy.uix.widget import Widget

class ConnectionStatusCircle(Widget):
    # Wenn `True`, dann ist die Verbindung hergestellt, sonst nicht.
    is_connected = BooleanProperty(False)

    # Farbe des Kreises
    circle_color = ListProperty([1, 0, 0, 1])  # Default ist Rot

    def __init__(self, **kwargs):
        super(ConnectionStatusCircle, self).__init__(**kwargs)
        self.is_connected = False
        self.bind(pos=self.update_circle, size=self.update_circle) #type: ignore
        self.bind(is_connected=self.update_color) #type: ignore
        self.update_color()

    def update_circle(self, *args):
        # Zeichnet den Kreis neu, wenn sich die Größe oder Position ändert
        self.canvas.clear() #type: ignore
        with self.canvas: #type: ignore
            Color(*self.circle_color)
            Ellipse(pos=self.pos, size=self.size)

    def update_color(self, *args):
        # Aktualisiert die Farbe, wenn sich `is_connected` ändert
        if self.is_connected:
            self.circle_color = [0, 1, 0, 1]  # Grün
        else:
            self.circle_color = [1, 0, 0, 1]  # Rot
        self.update_circle()

    def start_polling(self, interval=1):
        Clock.schedule_interval(self.poll_backend_status, interval)
    
    def poll_backend_status(self, dt):
        pass

class ObjectConnectionStatusCircle(ConnectionStatusCircle):
    def __init__(self, backend_obj, **kwargs):
        super(ObjectConnectionStatusCircle, self).__init__(**kwargs)
        self.backend_obj = backend_obj
        self.start_polling()

    def poll_backend_status(self, dt):
        self.is_connected = self.backend_obj.is_connected

class ValueConnectionStatusCircle(ConnectionStatusCircle):
    def __init__(self,value, **kwargs):
        super(ValueConnectionStatusCircle, self).__init__(**kwargs)
        self.is_connected = value
        self.start_polling()

    def poll_backend_status(self, dt):
        # Aktualisiert `is_connected` basierend auf dem Status des Backends
        self.is_connected = self.is_connected == 'connected'