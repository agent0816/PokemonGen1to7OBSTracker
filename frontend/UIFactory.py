import weakref
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput

def create_label_and_Textbox(grid, ids, id_name, 
                            label_text='label_text', label_size_hint=(.2,1), 
                            multiline=False, password=False, text_size=("20dp","40dp"), text_size_hint=(1,None),
                            text_validate_function=None):
    
    grid.add_widget(Label(text=label_text, size_hint=label_size_hint))
    anchor=AnchorLayout(anchor_x='left')
    grid.add_widget(anchor)
    textInput = TextInput(size_hint=text_size_hint, size=text_size, password=password, multiline=multiline, write_tab=False)
    if text_validate_function is not None:
        textInput.bind(on_text_validate=text_validate_function) #type: ignore
    ids[id_name] = weakref.proxy(textInput)
    anchor.add_widget(textInput)

def create_label_and_checkboxes(box, ids, 
                                checkbox_id_name='', checkbox_on_press=None, checkbox_active=True, checkbox_pos_hint={"center_y": .5},
                                checkbox_size_hint = [None, None], checkbox_size=["20dp", "20dp"], checkbox_disabled=False,
                                label_id_name='',label_text='label_text', label_pos_hint={"center_y": .5}, 
                                label_size_hint=[None, None], label_size=["60dp", "20dp"]):
    
    checkBox = CheckBox(on_press=checkbox_on_press,active=checkbox_active, pos_hint=checkbox_pos_hint, size_hint=checkbox_size_hint, size=checkbox_size, disabled=checkbox_disabled)
    checkboxLabel = Label(text=label_text, pos_hint=label_pos_hint, size_hint=label_size_hint, size=label_size)
    box.add_widget(checkBox)
    if checkbox_id_name is not None:
        ids[checkbox_id_name] = weakref.proxy(checkBox)
    box.add_widget(checkboxLabel)
    if label_id_name is not None:
        ids[label_id_name] = weakref.proxy(checkboxLabel)