import weakref
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput

def create_label_and_Textbox(rootwidget, ids, 
                            box_size=(0,"30dp"),box_padding=("5dp",0),box_spacing="5dp", box_size_hint_y=None,
                            label_text='label_text', label_size_hint=(.2,1), 
                            multiline=False, password=False, text_size_hint=(.8,1),
                            text_box_id=None, text_validate_function=None,
                            is_port=False):
    
    box = BoxLayout(orientation='horizontal', size_hint_y=box_size_hint_y, size=box_size, padding=box_padding, spacing=box_spacing)
    box.add_widget(Label(text=label_text, size_hint=label_size_hint))
    textInput = TextInput(size_hint=text_size_hint, password=password, multiline=multiline, write_tab=False)
    if text_validate_function is not None:
        textInput.bind(on_text_validate=text_validate_function) #type: ignore
    if text_box_id is not None:
        ids[text_box_id] = weakref.proxy(textInput)
    box.add_widget(textInput)
    if is_port:
        box.add_widget(Label(size_hint_x=.7))
    rootwidget.add_widget(box)

def create_label_and_checkboxes(rootwidget, ids, 
                                checkbox_id_name=None, checkbox_on_press=None, checkbox_active=True, checkbox_pos_hint={"center_y": .5},
                                checkbox_size_hint = [None, None], checkbox_size=["20dp", "20dp"], checkbox_disabled=False,
                                label_id_name=None,label_text='label_text', label_pos_hint={"center_y": .5}, 
                                label_size_hint=[None, None], label_size=["60dp", "20dp"]):
    
    box = BoxLayout(orientation='horizontal', size_hint_x=.3)

    checkBox = CheckBox(on_press=checkbox_on_press,active=checkbox_active, pos_hint=checkbox_pos_hint, size_hint=checkbox_size_hint, size=checkbox_size, disabled=checkbox_disabled)
    checkboxLabel = Label(text=label_text, pos_hint=label_pos_hint, size_hint=label_size_hint, size=label_size)
    box.add_widget(checkBox)
    if checkbox_id_name is not None:
        ids[checkbox_id_name] = weakref.proxy(checkBox)
    box.add_widget(checkboxLabel)
    if label_id_name is not None:
        ids[label_id_name] = weakref.proxy(checkboxLabel)

    rootwidget.add_widget(box)

def create_text_and_browse_button(rootwidget,ids,
                                box_id_name=None, box_size=(0,"30dp"),box_padding=("5dp",0),box_spacing="5dp", box_size_hint_y=None,
                                label_id_name=None, label_text='', label_size_hint=(.2,1),
                                text_id_name=None, text_size_hint=(.6,1), text_validate_function=None, password=False, multiline=False,
                                browse_id_name=None, browse_function=None, browse_text="Durchsuchen", browse_size_hint=(.2,1), browse_modus='directory'):
    
    box = BoxLayout(orientation='horizontal', size_hint_y=box_size_hint_y, size=box_size, padding=box_padding, spacing=box_spacing)
    if box_id_name is not None:
        ids[box_id_name] = weakref.proxy(box)
    rootwidget.add_widget(box)

    label = Label(text=label_text, size_hint=label_size_hint)
    if label_id_name is not None:
        ids[label_id_name] = weakref.proxy(label)
    box.add_widget(label)

    text_input = TextInput(size_hint=text_size_hint, password=password, multiline=multiline, write_tab=False)
    if text_validate_function is not None:
        text_input.bind(on_text_validate=text_validate_function) #type: ignore
    if text_id_name is not None:
        ids[text_id_name] = weakref.proxy(text_input)
    box.add_widget(text_input)

    browse = Button(text=browse_text, size_hint=browse_size_hint)
    if browse_function is not None:
        browse.bind(on_press=lambda instance: browse_function(text_input, browse_modus)) #type: ignore
    if browse_id_name is not None:
        ids[browse_id_name] = weakref.proxy(browse)
    box.add_widget(browse)
