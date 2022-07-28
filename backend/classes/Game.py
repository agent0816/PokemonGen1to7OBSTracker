class Game:
    def __init__(self, edition: str):
        self.edition: str = edition
        self.spritePath: str = self.setSpritePath(edition)

    def setSpritePath(self, edition: str) -> str:
        if edition == 'red' or edition == 'blue':
            folder: str = 'Z:/Raketenagenten/Icons/Pokemon/PokemonSprites/Gen1RB/rb/Slot01/'
        elif edition == 'yellow':
            folder: str = 'Z:/Raketenagenten/Icons/Pokemon/PokemonSprites/Gen1RB/gelb/Slot01/'
        elif edition == 'gold':
            folder: str = 'Z:/Raketenagenten/Icons/Pokemon/PokemonSprites/Gen2GS/gold/Slot1/'
        elif edition == 'silver':
            folder: str = 'Z:/Raketenagenten/Icons/Pokemon/PokemonSprites/Gen2GS/silber/Slot1/'
        elif edition == 'crystal':
            folder: str = 'Z:/Raketenagenten/Icons/Pokemon/PokemonSprites/Gen2GS/kristall/Slot1/'
        elif edition == 'ruby' or edition == 'sapphire':
            folder: str = 'Z:/Raketenagenten/Icons/Pokemon/PokemonSprites/Gen3RS/rs/Slot1/'
        elif edition == 'emerald':
            folder: str = 'Z:/Raketenagenten/Icons/Pokemon/PokemonSprites/Gen3RS/e/Slot1/'
        elif edition == 'fire red' or edition == 'leaf green':
            folder: str = 'Z:/Raketenagenten/Icons/Pokemon/PokemonSprites/Gen3FRBG/frbg/Slot1/'
        elif edition == 'diamond' or edition == 'pearl':
            folder: str = 'Z:/Raketenagenten/Icons/Pokemon/PokemonSprites/Gen4DP/dp/Slot1/'
        elif edition == 'platinum':
            folder: str = 'Z:/Raketenagenten/Icons/Pokemon/PokemonSprites/Gen4DP/sprites-platin/Slot1/'
        elif edition == 'heart gold' or edition == 'soul silver':
            folder: str = 'Z:/Raketenagenten/Icons/Pokemon/PokemonSprites/Gen4HGSS/sprites-hgss/Slot1/'
        elif edition == 'black' or edition == 'white':
            folder: str = 'Z:/Raketenagenten/Icons/Pokemon/PokemonSprites/Gen5BW/sprites-bw/Slot1/'
        else:
            folder: str = 'Z:/Raketenagenten/Icons/Pokemon/PokemonSprites/Gen6_7_8/normal'

        return folder

    def getSpritePath(self) -> str:
        return self.spritePath

    def getEdition(self) -> str:
        return self.edition

    def __repr__(self) -> str:
        return self.edition
