"""Util for obfuscating error in stack trace."""


def process_df(data, **kwargs):
    """Process data with given kwargs"""
    if kwargs.get("raise_error"):
        raise ValueError("This is an error")
    return data
