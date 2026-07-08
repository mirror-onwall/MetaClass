import re
from pathlib import Path

from fastapi import UploadFile


def safe_filename(filename: str | None) -> str:
    name = Path(filename or "source").name
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


async def save_upload(upload: UploadFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as target:
        while chunk := await upload.read(1024 * 1024):
            target.write(chunk)
