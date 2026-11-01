"""Agent-facing command contract for the CityWheels CLI.

The ask path stays a plain answer on stdout. This module describes the
machine-readable surfaces and corrects a flag or provider value only when
one known spelling is exactly one edit away.
"""

import json
from pathlib import Path

from citywheels.bookings import load_bookings
from citywheels.paths import default_data_dir

CONTRACT_VERSION = "1"
PROVIDERS = ("fake", "openai", "gemini")
KNOWN_FLAGS = (
    "--json",
    "--provider",
    "--data-dir",
    "--robot-triage",
    "--robot-help",
    "--no-color",
    "--version",
    "--help",
)
VERBS = ("capabilities", "robot-docs", "robot-triage", "help")
ASK = (
    'python3 -m citywheels --provider fake '
    '"I want to cancel booking CW-1002. How much will I get back?"'
)
HELP = "python3 -m citywheels --help"
GUIDE = "python3 -m citywheels robot-docs guide"
CAPABILITIES = "python3 -m citywheels capabilities"
TRIAGE = "python3 -m citywheels --robot-triage"
COMMANDS = (ASK, CAPABILITIES, GUIDE, TRIAGE, HELP, "python3 -m citywheels")
EXIT_CODES = (
    {"code": 0, "name": "success", "meaning": "The command finished."},
    {"code": 1, "name": "user-input-error", "meaning": "The arguments were wrong."},
    {"code": 2, "name": "safety-block", "meaning": "Reserved. This command has no safety block."},
    {
        "code": 3,
        "name": "tool-environment-error",
        "meaning": "A key is missing, or CITYWHEELS_PROVIDER is not a known provider.",
    },
    {
        "code": 4,
        "name": "transient-failure",
        "meaning": "The model host could not be reached. The same command can work on a retry.",
    },
)
RECOMMENDATIONS = (
    "For a deterministic offline answer, run: " + ASK,
    "Read the contract with: " + CAPABILITIES,
    "Read the handbook with: " + GUIDE,
    "Read data-pack health with: " + TRIAGE,
)


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"


def edit_distance(left: str, right: str) -> int:
    """Edit distance. One adjacent transposition counts as one edit.

    ``--jsno`` and ``--json`` are one swap, so a plain insert/delete/substitute
    distance would miss that typo. Lengths more than one apart cannot be one edit.
    """
    if abs(len(left) - len(right)) > 1:
        return abs(len(left) - len(right))
    rows = len(left) + 1
    cols = len(right) + 1
    distance = [[0] * cols for _ in range(rows)]
    for index in range(rows):
        distance[index][0] = index
    for index in range(cols):
        distance[0][index] = index
    for index in range(1, rows):
        for other in range(1, cols):
            cost = 0 if left[index - 1] == right[other - 1] else 1
            distance[index][other] = min(
                distance[index - 1][other] + 1,
                distance[index][other - 1] + 1,
                distance[index - 1][other - 1] + cost,
            )
            if (
                index > 1
                and other > 1
                and left[index - 1] == right[other - 2]
                and left[index - 2] == right[other - 1]
            ):
                distance[index][other] = min(distance[index][other], distance[index - 2][other - 2] + 1)
    return distance[-1][-1]


def near_options(token: str, options: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(option for option in options if option != token and edit_distance(token, option) == 1)


def unique_near(token: str, options: tuple[str, ...]) -> str | None:
    hits = near_options(token, options)
    if len(hits) == 1:
        return hits[0]
    return None


def correct_provider_value(value: str) -> tuple[str, str | None]:
    """Return a provider spelling and a stderr note.

    Case folding of an already-known provider is silent. A one-edit typo is
    corrected only when a single provider is that close.
    """
    lowered = value.strip().lower()
    if lowered in PROVIDERS:
        return lowered, None
    match = unique_near(lowered, PROVIDERS)
    if match:
        return match, f"corrected provider value {value!r} to {match!r}"
    return value, None


def prepare_argv(argv: list[str]) -> tuple[list[str], list[str], str | None, str | None]:
    """Correct flags and the --provider value.

    The return is argv, stderr notes, an ambiguous-flag message, and the flag
    named in that message. A question after `--` is copied unchanged.
    """
    notes: list[str] = []
    output: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            output.extend(argv[index:])
            break
        if token.startswith("--"):
            name, separator, attached = token.partition("=")
            if name not in KNOWN_FLAGS:
                hits = near_options(name, KNOWN_FLAGS)
                match = hits[0] if len(hits) == 1 else None
                if match:
                    notes.append(f"corrected {name!r} to {match!r}")
                    name = match
                    token = match if not separator else f"{match}={attached}"
                elif len(hits) > 1:
                    listed = ", ".join(hits)
                    message = f"Flag {argv[index]!r} is equally close to {listed}."
                    return argv, notes, message, argv[index]
            if name == "--provider" and separator:
                corrected, note = correct_provider_value(attached)
                if note:
                    notes.append(note)
                output.append(f"--provider={corrected}")
                index += 1
                continue
            output.append(token if separator else name)
            if name == "--provider" and not separator:
                if index + 1 < len(argv) and not argv[index + 1].startswith("--"):
                    corrected, note = correct_provider_value(argv[index + 1])
                    if note:
                        notes.append(note)
                    output.append(corrected)
                    index += 2
                    continue
            index += 1
            continue
        output.append(token)
        index += 1
    return output, notes, None, None


def wants_json(argv: list[str]) -> bool:
    for token in argv:
        if token == "--":
            return False
        if token == "--json" or token.startswith("--json="):
            return True
    return False


def triage_text() -> str:
    lines = [
        "CityWheels answers one customer question.",
        "",
        "Ask:",
        f"  {ASK}",
        "",
        "Agent surfaces:",
        f"  {TRIAGE}",
        f"  {CAPABILITIES}",
        f"  {GUIDE}",
        f"  {HELP}",
        "",
        "Exit codes: 0 success, 1 user-input-error, 2 safety-block, "
        "3 tool-environment-error, 4 transient-failure.",
        "",
    ]
    return "\n".join(lines)


def guide_text() -> str:
    return "\n".join(
        [
            "CityWheels agent handbook",
            "",
            "Run the commands from the directory that contains the citywheels package.",
            "Stdout is the answer or a JSON document. Stderr is a correction or an error.",
            "The command never prints color codes. --no-color and NO_COLOR are accepted.",
            "",
            "Ask one question:",
            f"  {ASK}",
            "",
            "With no arguments, the command prints a short triage and exits 0.",
            "A flags-only invocation that does not ask a question exits 1 and names the ask command.",
            "",
            "Verbs are recognized only when they are the first argument:",
            f"  {CAPABILITIES}",
            "    Always one JSON document. Keys include ok, contract_version, commands, exit_codes, flags, providers.",
            f"  {GUIDE}",
            "    This handbook. Add --json to wrap it as {\"ok\": true, \"contract_version\": \"1\", \"guide\": \"...\"}.",
            "  python3 -m citywheels robot-triage",
            "    Same JSON document as --robot-triage. It does not call a model.",
            "  python3 -m citywheels help",
            "    Same text as --help.",
            "",
            "Put -- before a question that is exactly one of those verbs:",
            '  python3 -m citywheels --provider fake -- capabilities',
            "",
            "Flags:",
            "  --json          Wrap the answer as JSON. Keys: ok, contract_version, answer, provider, tool_trace.",
            "  --provider      fake, openai, or gemini.",
            "  --data-dir      Directory that contains policies/ and bookings.json.",
            "  --robot-triage  Health, commands, and recommendations. No model call.",
            "  --robot-help    This handbook.",
            "  --no-color      Accepted. Output is already plain text.",
            "  --version       Print the contract version 1 and exit.",
            "  -h, --help      Show the human help.",
            "",
            "JSON is one object, keys sorted, UTF-8, and a single trailing newline.",
            "contract_version is \"1\". An error on stdout is "
            "{\"ok\": false, \"contract_version\": \"1\", \"error\": {\"category\", \"hint\", \"message\"}}.",
            "The same error's teaching line is on stderr and ends with the exact next command.",
            "",
            "Exit codes:",
            "  0 success",
            "  1 user-input-error, including an unknown --provider value or a missing data directory",
            "  2 safety-block, reserved, no command returns it today",
            "  3 tool-environment-error, a missing API key or an unknown CITYWHEELS_PROVIDER value",
            "  4 transient-failure, the model host could not be reached; retry or run the fake command above",
            "",
            "A flag or provider value one edit away from exactly one known spelling is corrected.",
            "The correction is printed on stderr and the command continues.",
            "Two spellings at the same distance are not guessed.",
            "",
            "--robot-triage health keys: data_dir, policies, booking_count, bookings_readable.",
            "policies is a sorted list of policy file names. There is no timestamp in the document.",
            "",
        ]
    )


def capabilities_payload() -> dict:
    return {
        "ok": True,
        "contract_version": CONTRACT_VERSION,
        "tool": "citywheels",
        "invocation": "python3 -m citywheels",
        "commands": list(COMMANDS),
        "exit_codes": list(EXIT_CODES),
        "flags": [
            {"name": "--json", "meaning": "Print the answer as one JSON object."},
            {"name": "--provider", "values": list(PROVIDERS)},
            {"name": "--data-dir", "meaning": "Policy and booking directory."},
            {"name": "--robot-triage", "meaning": "Health, commands, and recommendations. No model call."},
            {"name": "--robot-help", "meaning": "Print the agent handbook."},
            {"name": "--no-color", "meaning": "Accepted. The command prints no color codes."},
            {"name": "--version", "meaning": "Print contract version 1."},
            {"name": "--help", "meaning": "Print human help."},
        ],
        "providers": list(PROVIDERS),
        "env": [
            "CITYWHEELS_PROVIDER",
            "OPENAI_API_KEY",
            "OPENAI_MODEL",
            "OPENAI_BASE_URL",
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
        ],
        "typo_rule": "Distance 1 from exactly one known flag or provider value is corrected. Two candidates are not guessed.",
    }


def triage_health(data_dir: Path | None) -> dict:
    directory = (data_dir or default_data_dir()).resolve()
    policies_dir = directory / "policies"
    if policies_dir.is_dir():
        policies = sorted(path.name for path in policies_dir.glob("*.md"))
    else:
        policies = []
    try:
        bookings = load_bookings(directory)
        count = len(bookings)
        readable = True
    except (OSError, ValueError, UnicodeError):
        count = 0
        readable = False
    return {
        "booking_count": count,
        "bookings_readable": readable,
        "data_dir": str(directory),
        "policies": policies,
    }


def triage_payload(data_dir: Path | None) -> dict:
    return {
        "commands": list(COMMANDS),
        "contract_version": CONTRACT_VERSION,
        "health": triage_health(data_dir),
        "ok": True,
        "quick_ref": (
            "CityWheels answers one customer question from the local policies and bookings. "
            "Use --provider fake for a deterministic offline answer. " + ASK
        ),
        "recommendations": list(RECOMMENDATIONS),
    }


def error_payload(category: str, message: str, hint: str) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "error": {"category": category, "hint": hint, "message": message},
        "ok": False,
    }
