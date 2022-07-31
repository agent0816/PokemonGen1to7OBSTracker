class Game:
    def __init__(self, edition: str):
        self.edition: str = edition
        self.spritePath: str = self.setSpritePath(edition)
        self.path = self.setSpritePath(self.edition)
    def setSpritePath(self, edition: str) -> str:
        path = '/pokemon/versions/generation-'

        if edition == 'red' or edition == 'blue':
            path += 'i/red-blue/transparent/'
        elif edition == 'yellow':
            path += 'i/yellow/transparent/'
        elif edition == 'gold':
            path += 'ii/gold/transparent/'
        elif edition == 'silver':
            path += 'ii/silver/transparent/'
        elif edition == 'crystal':
            path += 'ii/crystal/transparent/'
        elif edition == 'ruby' or edition == 'sapphire':
            path += 'iii/ruby-sapphire/'
        elif edition == 'emerald':
            path += 'iii/emerald/'
        elif edition == 'fire red' or edition == 'leaf green':
            path += 'iii/firered-leafgreen/'
        elif edition == 'diamond' or edition == 'pearl':
            path += 'iv/diamond-pearl/'
        elif edition == 'platinum':
            path += 'iv/platinum/'
        elif edition == 'heart gold' or edition == 'soul silver':
            path += 'iv/heartgold-soulsilver/'
        elif edition == 'black' or edition == 'white':
            path += 'v/black-white/'
        return path

    def __repr__(self) -> str:
        return self.edition
