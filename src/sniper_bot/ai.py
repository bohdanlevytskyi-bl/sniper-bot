from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx

from sniper_bot.config import AIConfig


REGIME_SCHEMA = {
    "name": "regime_observation",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "label": {"type": "string", "enum": ["uptrend", "downtrend", "range", "high_volatility"]},
            "confidence": {"type": "number"},
            "rationale": {"type": "string"},
            "risk_notes": {"type": "string"},
        },
        "required": ["label", "confidence", "rationale", "risk_notes"],
    },
}

SUMMARY_SCHEMA = {
    "name": "daily_summary",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary_text": {"type": "string"},
            "regime_recap": {"type": "string"},
            "pnl_recap": {"type": "string"},
            "notable_risks": {"type": "string"},
        },
        "required": ["summary_text", "regime_recap", "pnl_recap", "notable_risks"],
    },
}

HEALTH_SCHEMA = {
    "name": "healthcheck",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
    },
}


class OpenAIObserver:
    def __init__(self, config: AIConfig, api_key: str):
        self.config = config
        self.client = httpx.Client(
            base_url="https://api.openai.com/v1",
            timeout=30,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self.client.close()

    def classify_regime(self, pair: str, candles: list[dict], indicators: dict[str, Any], risk_context: dict[str, Any]) -> dict[str, Any]:
        prompt = {
            "pair": pair,
            "task": "Classify current market regime for observability only.",
            "candles": candles[-self.config.regime_lookback_bars :],
            "indicators": indicators,
            "risk_context": risk_context,
        }
        return self._structured_response(REGIME_SCHEMA, prompt)

    def generate_daily_summary(self, summary_date: date, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = {
            "summary_date": summary_date.isoformat(),
            "task": "Generate a concise operator summary for the previous trading day.",
            "payload": payload,
        }
        return self._structured_response(SUMMARY_SCHEMA, prompt)

    def healthcheck(self) -> dict[str, Any]:
        prompt = {"task": "Return a JSON status object with status='ok'."}
        return self._structured_response(HEALTH_SCHEMA, prompt, max_output_tokens=40)

    def _structured_response(
        self,
        schema: dict[str, Any],
        prompt: dict[str, Any],
        max_output_tokens: int = 400,
    ) -> dict[str, Any]:
        response = self.client.post(
            "/responses",
            json={
                "model": self.config.model,
                "input": [
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "You are an observability-only assistant for a crypto trading bot. "
                                    "Respond only with valid JSON that matches the requested schema. "
                                    "You cannot propose trade execution changes."
                                ),
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": json.dumps(prompt, ensure_ascii=True)}],
                    },
                ],
                "text": {"format": {"type": "json_schema", "name": schema["name"], "schema": schema["schema"], "strict": True}},
                "max_output_tokens": max_output_tokens,
            },
        )
        response.raise_for_status()
        payload = response.json()
        text = _extract_output_text(payload)
        return json.loads(text)


def _extract_output_text(payload: dict[str, Any]) -> str:
    if payload.get("output_text"):
        return payload["output_text"]
    chunks: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "refusal":
                raise RuntimeError("OpenAI refused the request")
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
            elif isinstance(text, dict):
                value = text.get("value") or text.get("text")
                if value:
                    chunks.append(value)
    if not chunks:
        raise RuntimeError("OpenAI response did not contain text output")
    return "".join(chunks)
