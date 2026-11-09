def normalize_teamogy_job_id(value):
    """Store a missing Teamogy job ID as NULL, never as an empty string."""
    if value is None:
        return None
    value = value.strip()
    return value or None
