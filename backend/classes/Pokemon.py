class Pokemon:
    def __init__(self, dexnr: int, shiny: bool = False, female = False, form = '', **kwargs):
        self.dexnr: int = dexnr
        self.nickname: str = ''
        self.nickname: str = kwargs.get('nickname')
        self.lvl = kwargs.get('lvl')
        self.shiny: bool = shiny
        self.female = female
        self.form = form
        self.route = 0
        if 'route' in kwargs:
            self.route = kwargs.get('route')
        self.item = 0
        if 'item' in kwargs:
            self.item = kwargs.get('item')

    def __repr__(self):
        return f'<{self.dexnr}, {self.nickname}, lvl.{self.lvl}, item:{self.item}>'

    def __eq__(self, other) -> bool:
        if self.dexnr != other.dexnr:
            return False
        if self.nickname != other.nickname:
            return False
        if self.shiny != other.shiny:
            return False
        if self.item != other.item:
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
    
    def isLegit(self):
        if self.nickname == '' and self.lvl not in range(101) and self.item not in range(639):
            return False
        return True
