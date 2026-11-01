import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from citywheels.messages import contact_staff_message
from citywheels.model import ModelTurn
from citywheels.tools import TOOL_SPECS, run_tool

MAX_ROUNDS = 6


@dataclass
class AgentResult:
    answer: str
    tool_trace: list[dict] = field(default_factory=list)


def run_agent(
    question: str,
    model,
    *,
    data_dir: Path | None = None,
    now: datetime | None = None,
    max_rounds: int = MAX_ROUNDS,
    system: str | None = None,
) -> AgentResult:
    """Run tools the model asks for until it returns a final answer."""
    if system is None:
        from citywheels.briefing import load_system_prompt

        system = load_system_prompt(data_dir)
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
    trace: list[dict] = []
    for _ in range(max_rounds):
        turn: ModelTurn = model.complete(messages, TOOL_SPECS)
        if not turn.tool_calls:
            answer = (turn.content or "").strip() or contact_staff_message()
            return AgentResult(answer=answer, tool_trace=trace)
        messages.append(
            {
                "role": "assistant",
                "content": turn.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        **(
                            {"provider_part": call.provider_part}
                            if call.provider_part is not None
                            else {}
                        ),
                    }
                    for call in turn.tool_calls
                ],
            }
        )
        for call in turn.tool_calls:
            result = run_tool(call.name, call.arguments, data_dir=data_dir, now=now)
            trace.append(
                {"name": call.name, "arguments": call.arguments, "result": result}
            )
            messages.append(
                {
                    "role": "tool",
                    "name": call.name,
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
    return AgentResult(answer=contact_staff_message(), tool_trace=trace)
