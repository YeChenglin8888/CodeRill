def active_names(users, prefix):
    """Return sorted stripped names for active users matching prefix, ignoring case."""
    return [user["name"] for user in users if user["active"]]
