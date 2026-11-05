"""OpenAI-compatible chat completions with tool calling. Used only for live runs."""

import json
import urllib.error
import urllib.request

from citywheels.model import MissingApiKey, ModelTurn, ToolCall
from citywheels.settings import Settings


class OpenAIModel:
    def __init__(self, api_key: str, model: str, base_url: str, headers: dict | None = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers or {})

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAIModel":
        if not settings.openai_key:
            raise MissingApiKey("Set OPENAI_API_KEY in .env, or run with --provider fake.")
        return cls(settings.openai_key, settings.openai_model, settings.openai_base_url)

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [_to_openai_message(message) for message in messages],
        }
        if tools:
            payload["tools"] = [_to_openai_tool(tool) for tool in tools]
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                **self.headers,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").replace(self.api_key, "[redacted]")
            raise RuntimeError(f"Model provider returned HTTP {exc.code}: {detail[:500]}") from None
        message = data["choices"][0]["message"]
        tool_calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append(
                ToolCall(
                    id=call.get("id") or "call",
                    name=function.get("name") or "",
                    arguments=arguments,
                )
            )
        return ModelTurn(content=message.get("content"), tool_calls=tool_calls)


def _to_openai_tool(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["parameters"],
        },
    }


def _to_openai_message(message: dict) -> dict:
    role = message["role"]
    if role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message["tool_call_id"],
            "content": message["content"],
        }
    if role == "assistant" and message.get("tool_calls"):
        calls = []
        for call in message["tool_calls"]:
            calls.append(
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call["arguments"]),
                    },
                }
            )
        return {
            "role": "assistant",
            "content": message.get("content") or None,
            "tool_calls": calls,
        }
    return {"role": role, "content": message.get("content") or ""}
