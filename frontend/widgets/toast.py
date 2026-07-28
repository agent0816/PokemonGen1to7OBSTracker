"""Nicht-modaler Toast am unteren Fensterrand mit Stack + FIFO-Queue.

Modi:
    - ``show_toast(text, level, duration)``      Fire-and-forget, faded weg
    - ``show_pending_toast(text, level)``        Bleibt sichtbar bis ``finish_toast``
    - ``update_toast(handle, text, level)``      Aktualisiert Text/Level live
    - ``finish_toast(handle, text, level, dur)`` Pending → Auto: Ergebnis + Fade

Layout: Bis zu ``MAX_VISIBLE`` Toasts stapeln sich bottom-up (Slot 0 unten).
Weitere Toasts landen in einer FIFO-Queue und rücken nach, sobald ein Slot
frei wird. Reflow der übrigen Toasts nach oben/unten via Animation.
"""

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.uix.label import Label

from backend.logging_setup import get_logger

logger = get_logger(__name__, 'logs/frontend.log')

MAX_VISIBLE = 3
BASE_Y = 40
SLOT_GAP = 10
DEFAULT_DURATION = 1.5
DEFAULT_FADE = 0.4
DOT_INTERVAL = 0.5

_LEVEL_COLORS = {
    'success': (0.30, 0.69, 0.31, 0.92),
    'error':   (0.83, 0.18, 0.18, 0.92),
    'warning': (1.00, 0.60, 0.00, 0.92),
    'info':    (0.38, 0.38, 0.38, 0.92),
}

_slots: list = [None] * MAX_VISIBLE
_queue: list = []


class ToastHandle:
    """Referenz auf einen Pending-Toast. Nutzt Modul-Funktionen intern."""

    def __init__(self, state):
        self._state = state

    def update(self, text=None, level=None):
        update_toast(self, text=text, level=level)

    def finish(self, text, level='success', duration=DEFAULT_DURATION):
        finish_toast(self, text, level=level, duration=duration)


class _ToastState:
    def __init__(self, text, level, kind):
        self.text = text
        self.level = level
        self.kind = kind  # 'auto' | 'pending'
        self.auto_duration = DEFAULT_DURATION
        self.label = None
        self.bg = None
        self.bg_color = None
        self.slot = None
        self.dot_event = None
        self.dot_phase = 0
        self.finished = False


def show_toast(text: str, level: str = 'info', duration: float = DEFAULT_DURATION) -> None:
    state = _ToastState(text, level, 'auto')
    state.auto_duration = duration
    _admit(state)


def show_pending_toast(text: str, level: str = 'info') -> ToastHandle:
    state = _ToastState(text, level, 'pending')
    _admit(state)
    return ToastHandle(state)


def update_toast(handle: ToastHandle, text: str = None, level: str = None) -> None:
    state = handle._state
    if text is not None:
        state.text = text
    if level is not None:
        state.level = level
    if state.label is None:
        return
    _refresh_label(state)


def finish_toast(handle: ToastHandle, text: str, level: str = 'success',
                  duration: float = DEFAULT_DURATION) -> None:
    state = handle._state
    if state.finished:
        return
    state.finished = True
    state.text = text
    state.level = level
    state.kind = 'auto'
    state.auto_duration = duration
    _stop_dots(state)
    if state.label is None:
        # Noch in Queue — beim Placement wird direkt als Auto-Toast behandelt.
        return
    _refresh_label(state)
    _schedule_fade(state, duration)


def _admit(state: _ToastState) -> None:
    for i, cur in enumerate(_slots):
        if cur is None:
            _place(state, i)
            return
    _queue.append(state)


def _place(state: _ToastState, slot_idx: int) -> None:
    _slots[slot_idx] = state
    state.slot = slot_idx
    try:
        label = Label(
            text=_render_text(state),
            size_hint=(None, None),
            font_size='14sp',
            color=(1, 1, 1, 1),
            padding=("14dp", "8dp"),
        )
        label.texture_update()
        tw, th = label.texture_size
        label.size = (tw + 28, th + 16)
        label.pos = ((Window.width - label.width) / 2, _slot_y(slot_idx, label.height))
        with label.canvas.before:
            state.bg_color = Color(*_LEVEL_COLORS.get(state.level, _LEVEL_COLORS['info']))
            state.bg = RoundedRectangle(pos=label.pos, size=label.size, radius=[8])

        def _sync_bg(_inst, _val):
            state.bg.pos = label.pos
            state.bg.size = label.size
        label.bind(pos=_sync_bg, size=_sync_bg)
        Window.add_widget(label)
        state.label = label

        if state.kind == 'pending':
            _start_dots(state)
        else:
            _schedule_fade(state, state.auto_duration)
    except Exception as err:
        logger.warning(f"toast placement fallback: {err}")
        _slots[slot_idx] = None
        _drain_queue()


def _slot_y(slot_idx: int, label_height: float) -> float:
    return BASE_Y + slot_idx * (label_height + SLOT_GAP)


def _render_text(state: _ToastState) -> str:
    if state.kind == 'pending':
        dots = '.' * state.dot_phase
        return f"{state.text}{dots}"
    return state.text


def _refresh_label(state: _ToastState) -> None:
    label = state.label
    if label is None:
        return
    label.text = _render_text(state)
    label.texture_update()
    tw, th = label.texture_size
    label.size = (tw + 28, th + 16)
    label.pos = ((Window.width - label.width) / 2, _slot_y(state.slot, label.height))
    if state.bg_color is not None:
        state.bg_color.rgba = _LEVEL_COLORS.get(state.level, _LEVEL_COLORS['info'])


def _start_dots(state: _ToastState) -> None:
    def _tick(_dt):
        state.dot_phase = (state.dot_phase + 1) % 4
        _refresh_label(state)
    state.dot_event = Clock.schedule_interval(_tick, DOT_INTERVAL)


def _stop_dots(state: _ToastState) -> None:
    if state.dot_event is not None:
        state.dot_event.cancel()
        state.dot_event = None
    state.dot_phase = 0


def _schedule_fade(state: _ToastState, duration: float) -> None:
    def _fade_out(_dt):
        try:
            anim = Animation(opacity=0, duration=DEFAULT_FADE)
            anim.bind(on_complete=lambda *_: _remove(state))
            anim.start(state.label)
        except Exception:
            _remove(state)
    Clock.schedule_once(_fade_out, duration)


def _remove(state: _ToastState) -> None:
    slot_idx = state.slot
    try:
        if state.label is not None:
            Window.remove_widget(state.label)
    except Exception as err:
        logger.warning(f"toast remove fallback: {err}")
    if slot_idx is not None and 0 <= slot_idx < MAX_VISIBLE:
        _slots[slot_idx] = None
    _reflow()
    _drain_queue()


def _reflow() -> None:
    """Kompaktiert Slots (Lücken nach oben schließen) und animiert Position."""
    compact = [s for s in _slots if s is not None]
    for i in range(MAX_VISIBLE):
        _slots[i] = compact[i] if i < len(compact) else None
        state = _slots[i]
        if state is None or state.label is None:
            continue
        state.slot = i
        target_y = _slot_y(i, state.label.height)
        if state.label.y != target_y:
            Animation(y=target_y, duration=0.2).start(state.label)


def _drain_queue() -> None:
    while _queue and any(s is None for s in _slots):
        state = _queue.pop(0)
        for i, cur in enumerate(_slots):
            if cur is None:
                _place(state, i)
                break
