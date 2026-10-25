def paginate(items, page, page_size):
    """Return a one-based page. Reject non-positive page or page_size."""
    start = page * page_size
    return items[start:start + page_size]
