class Pokemon:
    def __init__(self, dexnr: int, shiny: bool = False, female = False, form = '', **kwargs):
        self.dexnr: int = dexnr
        self.nickname: str = ''
        self.nickname: str = kwargs.get('nickname')
        self.lvl = kwargs.get('lvl')
        self.shiny: bool = shiny
        self.female = female
        self.route = 0
        self.form = form
        if 'route' in kwargs:
            self.route = kwargs.get('route')

    def __repr__(self):
        return f'<{self.dexnr}, {self.nickname}, {self.lvl}, {self.shiny=}, {self.female=}, {self.route}>'

    def __eq__(self, other) -> bool:
        if self.dexnr != other.dexnr:
            return False
        if self.nickname != other.nickname:
            return False
        if self.shiny != other.shiny:
            return False
        return True

    def __lt__(self, other):
        if self.dexnr == 0:
            return False
        if other.dexnr == 0:
            return True
        if self.dexnr == 'egg':
            return False
        if other.dexnr == 'egg':
            return True
        return self.dexnr < other.dexnr
