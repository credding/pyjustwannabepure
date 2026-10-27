import random

from justwannabepure import side_effect


@side_effect
def roll(sides: int) -> int:
    return random.randint(1, sides)


class Dice:
    @side_effect
    def roll(self) -> int:
        return roll(6)
