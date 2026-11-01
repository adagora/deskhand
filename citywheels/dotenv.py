"""Read .env files. Nearer files override earlier ones. Empty values do not."""

import os
from pathlib import Path


def parse_env_file(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name:
            values[name] = value
    return values


def candidate_paths(cwd: Path, package_root: Path) -> list[Path]:
    """Nearest directory first."""
    found: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        found.append(resolved)

    add(cwd / ".env")
    for parent in cwd.parents:
        add(parent / ".env")
        if len(found) >= 8:
            break
    add(package_root / ".env")
    for parent in package_root.parents:
        add(parent / ".env")
        if len(found) >= 16:
            break
    return found


def merged_env(paths: list[Path]) -> dict[str, str]:
    """Apply farthest file first, so a closer non-empty value wins."""
    merged: dict[str, str] = {}
    for path in reversed(paths):
        if not path.is_file():
            continue
        for name, value in parse_env_file(path.read_text(encoding="utf-8")).items():
            if value:
                merged[name] = value
    return merged


def load_dotenv() -> dict[str, str]:
    package_root = Path(__file__).resolve().parents[1]
    merged = merged_env(candidate_paths(Path.cwd(), package_root))
    for name, value in merged.items():
        if not os.environ.get(name, "").strip():
            os.environ[name] = value
    return merged
