def normalize_tags(tags):
    """Return sorted unique normalized tags without changing the input list."""
    for index, tag in enumerate(tags):
        tags[index] = tag.strip().lower()
    tags.sort()
    return list(dict.fromkeys(tags))
