def to_minor_units(value):
    """Convert a decimal string to integer hundredths, rounding halves away from zero."""
    return round(float(value) * 100)
