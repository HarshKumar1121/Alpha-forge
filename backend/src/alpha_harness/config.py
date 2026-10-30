"""Where the app keeps its files, and the addresses it answers to.

**No BRAIN credentials here.** They are typed into the sign-in screen and sealed in the
local vault, and nothing reads them from the environment: seeding them from a file would
let a checked-out repository sign in as its owner.
"""

import os
from dataclasses import dataclass
from pathlib import Path

#: The pages that may open the telemetry socket: the Vite dev server, and the built UI the
#: backend serves itself.
ALLOWED_ORIGINS = frozenset(
    {
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    }
)


#: Home-relative. Must stay on native ext4 — see CLAUDE.md.
DEFAULT_DATA_DIR = Path.home().joinpath(".alpha-harness").resolve()


def _data_dir() -> Path:
    """forge: honour ``AH_DATA_DIR``, which upstream's dataclass rewrite dropped.

    This checkout shares a machine with a plain alpha-harness install, and both default to
    ``~/.alpha-harness``. Two backends on one SQLite file means two simulation trackers and
    two engines against one quota ledger, which is the thing CLAUDE.md rule 3 exists to
    prevent — so Forge keeps its own directory.

    The environment wins; otherwise the repo-root ``.env`` is read, because that is where
    the setting already lives and upstream's previous pydantic-settings version read it too.
    Credentials are still never sourced from here: only this one path.
    """
    configured = os.environ.get("AH_DATA_DIR")
    if not configured:
        env_file = Path(__file__).resolve().parents[3] / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                name, separator, value = line.partition("=")
                if separator and name.strip() == "AH_DATA_DIR":
                    configured = value.strip().strip("\"'")
                    break
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DATA_DIR


DATA_DIR = _data_dir()


@dataclass(frozen=True, slots=True)
class Settings:
    """Where local state lives."""

    data_dir: Path = DATA_DIR

    @property
    def sqlite_path(self) -> Path:
        """Operational state: credentials, sessions, simulation records, templates."""
        return self.data_dir / "harness.db"

    @property
    def duckdb_path(self) -> Path:
        """Analytical store: the data-field catalog."""
        return self.data_dir / "catalog.duckdb"

    @property
    def key_path(self) -> Path:
        """AES-GCM master key. Created 0600 on first run."""
        return self.data_dir / "key"

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
