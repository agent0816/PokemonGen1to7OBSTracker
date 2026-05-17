from kivy.clock import Clock
from kivy.graphics import Color
from kivy.graphics import Ellipse
from kivy.properties import ListProperty
from kivy.properties import StringProperty
from kivy.uix.widget import Widget

class ConnectionStatusCircle(Widget):
    connection_state = StringProperty("disconnected")

    circle_color = ListProperty([1, 0, 0, 1])

    STATE_COLORS = {
        "connected": [0, 1, 0, 1],
        "warning": [1, 1, 0, 1],
        "disconnected": [1, 0, 0, 1],
    }

    def __init__(self, **kwargs):
        super(ConnectionStatusCircle, self).__init__(**kwargs)
        self.bind(pos=self.update_circle, size=self.update_circle) #type: ignore
        self.bind(connection_state=self.update_color) #type: ignore
        self.update_color()

    def update_circle(self, *args):
        self.canvas.clear() #type: ignore
        with self.canvas: #type: ignore
            Color(*self.circle_color)
            Ellipse(pos=self.pos, size=self.size)

    def update_color(self, *args):
        self.circle_color = self.STATE_COLORS.get(
            self.connection_state, [1, 0, 0, 1]
        )
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
        status = self.backend_obj.is_connected
        if status and status != "warning":
            self.connection_state = "connected"
        elif status == "warning":
            self.connection_state = "warning"
        else:
            self.connection_state = "disconnected"

class ValueConnectionStatusCircle(ConnectionStatusCircle):
    def __init__(self, client_id, backend_dict, **kwargs):
        super(ValueConnectionStatusCircle, self).__init__(**kwargs)
        self.backend_dict = backend_dict
        self.client_id = client_id
        self.start_polling()

    def poll_backend_status(self, dt):
        try:
            status = self.backend_dict[self.client_id]
            if status and status != "warning":
                self.connection_state = "connected"
            elif status == "warning":
                self.connection_state = "warning"
            else:
                self.connection_state = "disconnected"
        except KeyError:
            self.connection_state = "disconnected"