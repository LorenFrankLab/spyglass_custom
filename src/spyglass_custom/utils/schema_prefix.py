import datajoint as dj


def get_schema_prefix():
    user = dj.config.get("database.user")
    custom = dj.config.get("custom", {}).get("database.prefix")

    return custom or user


schema_prefix = get_schema_prefix()
