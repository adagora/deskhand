import argparse
import os
import sys
import urllib.error
from pathlib import Path

from citywheels.agent import run_agent
from citywheels.fake_model import FakeModel
from citywheels.gemini_model import GeminiModel
from citywheels.model import MissingApiKey
from citywheels.openai_model import OpenAIModel
from citywheels.paths import default_data_dir
from citywheels.settings import Settings, current
from citywheels.surface import (
    ASK,
    CAPABILITIES,
    CONTRACT_VERSION,
    GUIDE,
    HELP,
    PROVIDERS,
    TRIAGE,
    VERBS,
    capabilities_payload,
    dumps,
    error_payload,
    guide_text,
    prepare_argv,
    triage_payload,
    triage_text,
    unique_near,
    wants_json,
)

EPILOG = """
Agent commands:
  python3 -m citywheels
  python3 -m citywheels capabilities
  python3 -m citywheels robot-docs guide
  python3 -m citywheels --robot-triage

Exit codes:
  0  success
  1  user-input-error
  2  safety-block (reserved; no command returns this today)
  3  tool-environment-error
  4  transient-failure
""".strip()


def build_model(settings: Settings):
    if settings.provider == "fake":
        return FakeModel()
    if settings.provider == "openai":
        return OpenAIModel.from_settings(settings)
    if settings.provider == "gemini":
        return GeminiModel.from_settings(settings)
    raise ValueError(f"Unknown provider: {settings.provider}")


def teach(message: str, where: str, hint: str, category: str, code: int, json_mode: bool) -> int:
    print(f"{message} Where: {where}. Next: {hint}", file=sys.stderr)
    if json_mode:
        sys.stdout.write(dumps(error_payload(category, message, hint)))
    return code


class CitywheelsParser(argparse.ArgumentParser):
    json_mode = False

    def error(self, message: str) -> None:
        where = "arguments"
        for part in message.replace(":", " ").split():
            if part.startswith("--") or part == "question":
                where = part.strip(",")
                break
        hint = ASK if "question" in message else HELP
        teach(message, where, hint, "user-input-error", 1, self.json_mode)
        self.exit(1)


def build_parser(json_mode: bool) -> CitywheelsParser:
    parser = CitywheelsParser(
        prog="citywheels",
        description="Answer one CityWheels customer question.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.json_mode = json_mode
    parser.add_argument(
        "question",
        nargs="?",
        help="Customer question. With no arguments, citywheels prints a short triage.",
    )
    parser.add_argument("--json", action="store_true", help="Print the answer as one JSON object.")
    parser.add_argument(
        "--provider",
        default=None,
        help="fake, openai, or gemini. fake needs no API key.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory that contains policies/ and bookings.json.",
    )
    parser.add_argument(
        "--robot-triage",
        action="store_true",
        help="Print health, commands, and recommendations as JSON. Does not call a model.",
    )
    parser.add_argument("--robot-help", action="store_true", help="Print the agent handbook.")
    parser.add_argument("--no-color", action="store_true", help="Accepted. Output has no color codes.")
    parser.add_argument("--version", action="store_true", help="Print contract version 1 and exit.")
    return parser


def _exit_code(exc: SystemExit) -> int:
    if exc.code in (None, 0):
        return 0
    if isinstance(exc.code, int):
        return exc.code
    return 1


def _take_data_dir(argv: list[str]) -> tuple[Path | None, list[str]]:
    output: list[str] = []
    data_dir = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--data-dir":
            if index + 1 >= len(argv):
                raise ValueError("--data-dir needs a directory path.")
            data_dir = Path(argv[index + 1])
            index += 2
            continue
        if token.startswith("--data-dir="):
            data_dir = Path(token.split("=", 1)[1])
            index += 1
            continue
        output.append(token)
        index += 1
    return data_dir, output


def _missing_dir(path: Path, hint: str, json_mode: bool) -> int:
    return teach(
        f"Data directory does not exist: {path}",
        "--data-dir",
        hint,
        "user-input-error",
        1,
        json_mode,
    )


def _emit_triage(data_dir: Path | None, json_mode: bool) -> int:
    directory = data_dir or default_data_dir()
    if not directory.is_dir():
        return _missing_dir(directory, TRIAGE, json_mode)
    sys.stdout.write(dumps(triage_payload(data_dir)))
    return 0


def run_verb(argv: list[str], json_mode: bool) -> int:
    verb = argv[0]
    rest = argv[1:]
    if verb == "help":
        extra = [token for token in rest if token != "--no-color"]
        if extra:
            return teach(
                f"Unexpected argument {extra[0]!r} after help.",
                "help",
                HELP,
                "user-input-error",
                1,
                json_mode,
            )
        build_parser(json_mode).print_help()
        return 0
    if verb == "capabilities":
        extra = [token for token in rest if token not in ("--json", "--no-color")]
        if extra:
            return teach(
                f"Unexpected argument {extra[0]!r} after capabilities.",
                "capabilities",
                CAPABILITIES,
                "user-input-error",
                1,
                json_mode,
            )
        sys.stdout.write(dumps(capabilities_payload()))
        return 0
    if verb == "robot-docs":
        if "guide" not in rest:
            return teach(
                "robot-docs needs the guide topic.",
                "robot-docs",
                GUIDE,
                "user-input-error",
                1,
                json_mode,
            )
        extra = [token for token in rest if token not in ("guide", "--json", "--no-color")]
        if extra:
            return teach(
                f"Unexpected argument {extra[0]!r} after robot-docs guide.",
                "robot-docs",
                GUIDE,
                "user-input-error",
                1,
                json_mode,
            )
        text = guide_text()
        if "--json" in rest:
            sys.stdout.write(dumps({"contract_version": CONTRACT_VERSION, "guide": text, "ok": True}))
        else:
            sys.stdout.write(text)
        return 0
    try:
        data_dir, leftover = _take_data_dir(rest)
    except ValueError as exc:
        return teach(str(exc), "--data-dir", TRIAGE, "user-input-error", 1, json_mode)
    extra = [token for token in leftover if token not in ("--json", "--no-color")]
    if extra:
        return teach(
            "--robot-triage does not take a question.",
            "--robot-triage",
            TRIAGE,
            "user-input-error",
            1,
            json_mode,
        )
    return _emit_triage(data_dir, json_mode)


def _env_provider_override() -> tuple[str | None, str | None]:
    from citywheels.dotenv import load_dotenv

    load_dotenv()
    raw = os_environ_provider()
    if not raw:
        return None, None
    lowered = raw.lower()
    if lowered in PROVIDERS:
        return None, None
    match = unique_near(lowered, PROVIDERS)
    if match:
        return match, f"corrected CITYWHEELS_PROVIDER {raw!r} to {match!r}"
    return None, None


def os_environ_provider() -> str:
    return os.environ.get("CITYWHEELS_PROVIDER", "").strip()


def _key_where(provider: str) -> str:
    if provider == "openai":
        return "OPENAI_API_KEY"
    if provider == "gemini":
        return "GEMINI_API_KEY"
    return "provider"


def run_parsed(argv: list[str], json_mode: bool) -> int:
    parser = build_parser(json_mode)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return _exit_code(exc)
    if args.version:
        sys.stdout.write(CONTRACT_VERSION + "\n")
        return 0
    if args.robot_help:
        sys.stdout.write(guide_text())
        return 0
    if args.robot_triage:
        if args.question:
            return teach(
                "--robot-triage does not take a question.",
                "--robot-triage",
                TRIAGE,
                "user-input-error",
                1,
                json_mode,
            )
        return _emit_triage(args.data_dir, json_mode)
    if not args.question:
        return teach(
            "A question is required with these flags.",
            "question",
            ASK,
            "user-input-error",
            1,
            json_mode,
        )
    explicit = args.provider
    if explicit is None:
        override, note = _env_provider_override()
        if note:
            print(note, file=sys.stderr)
        if override:
            explicit = override
    settings = current(explicit)
    if settings.provider not in PROVIDERS:
        flagged = args.provider is not None
        return teach(
            f"Unknown provider {settings.provider!r}. Choices: fake, openai, gemini.",
            "--provider" if flagged else "CITYWHEELS_PROVIDER",
            ASK,
            "user-input-error" if flagged else "tool-environment-error",
            1 if flagged else 3,
            json_mode,
        )
    if args.data_dir is not None and not args.data_dir.is_dir():
        return _missing_dir(args.data_dir, ASK, json_mode)
    if args.data_dir is None and not default_data_dir().is_dir():
        return _missing_dir(default_data_dir(), ASK, json_mode)
    try:
        model = build_model(settings)
    except MissingApiKey as exc:
        return teach(str(exc), _key_where(settings.provider), ASK, "tool-environment-error", 3, json_mode)
    try:
        result = run_agent(args.question, model, data_dir=args.data_dir)
    except urllib.error.URLError as exc:
        return teach(
            f"The model provider could not be reached: {exc.reason}",
            "network",
            ASK,
            "transient-failure",
            4,
            json_mode,
        )
    if args.json:
        sys.stdout.write(
            dumps(
                {
                    "answer": result.answer,
                    "contract_version": CONTRACT_VERSION,
                    "ok": True,
                    "provider": settings.provider,
                    "tool_trace": result.tool_trace,
                    "write_intents": result.write_intents,
                }
            )
        )
        return 0
    print(result.answer)
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    prepared, notes, ambiguous, where = prepare_argv(list(argv))
    json_mode = wants_json(prepared)
    for note in notes:
        print(note, file=sys.stderr)
    if ambiguous:
        return teach(ambiguous, where or "arguments", HELP, "user-input-error", 1, json_mode)
    if prepared and prepared[0] in VERBS:
        return run_verb(prepared, json_mode)
    if not prepared:
        sys.stdout.write(triage_text())
        return 0
    return run_parsed(prepared, json_mode)
