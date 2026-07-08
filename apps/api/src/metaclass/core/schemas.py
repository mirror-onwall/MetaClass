from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SchemaModel(BaseModel):
    """Base class for every persisted or API-facing contract."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )
