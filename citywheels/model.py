from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    # Provider-specific part to replay on the next call, such as a Gemini thought signature.
    provider_part: dict | None = None


@dataclass
class ModelTurn:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class MissingApiKey(RuntimeError):
    pass
