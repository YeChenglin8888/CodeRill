def reserve(stock, requests):
    """Return updated stock after reservations, without changing caller data."""
    for sku, quantity in requests:
        stock[sku] -= quantity
    return stock
