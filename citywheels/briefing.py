"""Read the policy files and ask the schedule rules what to tell the model."""

from pathlib import Path

from citywheels.clock import shop_now
from citywheels.paths import default_data_dir
from citywheels.prompts import system_prompt


def policy_text(data_dir: Path | None = None) -> str:
    directory = (data_dir or default_data_dir()) / "policies"
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.md"))
    )


def load_system_prompt(data_dir: Path | None = None) -> str:
    return system_prompt(policy_text(data_dir), shop_now())
