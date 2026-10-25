from prices import total


def checkout(values, shipping=5):
    return total(values) + shipping
