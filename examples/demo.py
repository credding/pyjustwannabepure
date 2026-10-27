import effects
from effects import Dice
from effects import roll as r

from justwannabepure import pure, shell, side_effect


@side_effect
def now() -> int:
    import time

    return int(time.time())


def helper(x: int) -> int:
    return x + 1


@pure
async def fetch() -> int:  # JWP001
    return 1


@pure
def score(d: Dice) -> int:
    a = now()  # JWP002 (same file)
    b = r(6)  # JWP002 (aliased import)
    c = effects.roll(20)  # JWP002 (module attribute)
    e = d.roll()  # JWP002 (method)
    f = list(map(r, [6]))  # JWP002 (reference)
    print(a)  # JWP005
    return helper(a + b + c + e + f[0])  # fine: unannotated


total = 0


@pure
def tally(counts: dict[str, int], key: str) -> int:
    global total  # JWP006
    counts[key] = 1  # JWP007
    main()  # JWP008
    total = len(counts)
    return total


@pure
@side_effect  # JWP004
def confused() -> None: ...


@shell
def main() -> None:
    x = score(Dice()) if True else 0  # fine: ternary
    ys = [y for y in range(x)]  # fine: comprehension
    for y in ys:  # JWP003
        print(y)
    try:  # JWP003
        pass
    except ValueError:
        raise
