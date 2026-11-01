"""Gemini generateContent with function calling. Used only for live runs."""

import json
import urllib.error
import urllib.request

from citywheels.model import MissingApiKey, ModelTurn, ToolCall
from citywheels.settings import Settings


class GeminiModel:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    @classmethod
    def from_settings(cls, settings: Settings) -> "GeminiModel":
        if not settings.gemini_key:
            raise MissingApiKey("Set GEMINI_API_KEY in .env, or run with --provider fake.")
        return cls(settings.gemini_key, settings.gemini_model)

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
        system, contents = _to_gemini_contents(messages)
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "tools": [{"functionDeclarations": [_to_gemini_tool(tool) for tool in tools]}],
            "generationConfig": {"temperature": 0},
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").replace(self.api_key, "[redacted]")
            raise RuntimeError(f"Gemini returned HTTP {exc.code}: {detail[:500]}") from None
        candidates = data.get("candidates") or []
        if not candidates:
            reason = data.get("promptFeedback") or data.get("error") or "no candidate"
            raise RuntimeError(f"Gemini returned no candidate: {reason}")
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        texts = []
        tool_calls = []
        for index, part in enumerate(parts):
            if part.get("text"):
                texts.append(part["text"])
            call = part.get("functionCall")
            if not call:
                continue
            arguments = call.get("args") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append(
                ToolCall(
                    id=str(call.get("id") or f"gemini-{index}"),
                    name=call.get("name") or "",
                    arguments=arguments,
                    provider_part=part,
                )
            )
        return ModelTurn(content="".join(texts) or None, tool_calls=tool_calls)


def _to_gemini_tool(tool: dict) -> dict:
    parameters = dict(tool["parameters"])
    parameters.pop("additionalProperties", None)
    return {
        "name": tool["name"],
        "description": tool["description"],
        "parameters": parameters,
    }


def _to_gemini_contents(messages: list[dict]) -> tuple[str, list[dict]]:
    system = []
    contents: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            if message.get("content"):
                system.append(message["content"])
            continue
        if role == "user":
            contents.append({"role": "user", "parts": [{"text": message.get("content") or ""}]})
            continue
        if role == "assistant":
            parts = []
            if message.get("content"):
                parts.append({"text": message["content"]})
            for call in message.get("tool_calls") or []:
                if call.get("provider_part"):
                    parts.append(call["provider_part"])
                    continue
                function_call = {"name": call["name"], "args": call.get("arguments") or {}}
                if call.get("id"):
                    function_call["id"] = call["id"]
                parts.append({"functionCall": function_call})
            contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            continue
        if role == "tool":
            try:
                payload = json.loads(message.get("content") or "{}")
            except json.JSONDecodeError:
                payload = {"raw": message.get("content") or ""}
            if not isinstance(payload, dict):
                payload = {"result": payload}
            function_response = {"name": message.get("name") or "", "response": payload}
            if message.get("tool_call_id"):
                function_response["id"] = message["tool_call_id"]
            part = {"functionResponse": function_response}
            if contents and contents[-1]["role"] == "user" and "functionResponse" in contents[-1]["parts"][-1]:
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})
    return "\n\n".join(system), contents
