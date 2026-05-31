from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    country: str
    language: str
    token_file: Path
    state_file: Path
    receipts_dir: Path


def load() -> Config:
    return Config(
        country=os.environ.get("LIDL_COUNTRY", "PL"),
        language=os.environ.get("LIDL_LANGUAGE", "pl"),
        token_file=Path(os.environ.get("LIDL_TOKEN_FILE", "./data/refresh_token")),
        state_file=Path(os.environ.get("LIDL_STATE_FILE", "./data/state.json")),
        receipts_dir=Path(os.environ.get("LIDL_RECEIPTS_DIR", "./data/receipts")),
    )
