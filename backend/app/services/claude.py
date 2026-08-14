import json
from functools import lru_cache

import anthropic
from anthropic import Anthropic

from app.config import settings


class ClaudeError(RuntimeError):
    """The Claude API is unavailable or declined — surfaced to the UI as 503."""


@lru_cache(maxsize=1)
def get_client() -> Anthropic:
    if not settings.anthropic_api_key:
        raise ClaudeError(
            "ANTHROPIC_API_KEY is not set. Copy backend/.env.example to backend/.env "
            "and add your key from console.anthropic.com."
        )
    return Anthropic(api_key=settings.anthropic_api_key)


def _friendly_api_error(e: anthropic.APIError) -> ClaudeError:
    if isinstance(e, anthropic.AuthenticationError):
        return ClaudeError(
            "Anthropic rejected the API key (401) — check ANTHROPIC_API_KEY in backend/.env "
            "against console.anthropic.com."
        )
    if isinstance(e, anthropic.APIStatusError):
        return ClaudeError(f"Anthropic API error {e.status_code}: {e.message}")
    return ClaudeError(f"Anthropic API error: {e.message}")


def generate_json(system: str, user_content: str, schema: dict, max_tokens: int = 16000) -> dict:
    """One Claude call constrained to a JSON schema. Returns the parsed object."""
    try:
        response = _create_json(system, user_content, schema, max_tokens)
    except anthropic.APIError as e:
        raise _friendly_api_error(e) from e
    if response.stop_reason == "refusal":
        raise ClaudeError("Claude declined this request (refusal stop reason).")
    text = "".join(block.text for block in response.content if block.type == "text")
    return json.loads(text)


def _create_json(system: str, user_content: str, schema: dict, max_tokens: int):
    return get_client().messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens,
        system=system,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user_content}],
    )


def generate_json_with_image(
    system: str, png_bytes: bytes, schema: dict, max_tokens: int = 8000
) -> dict:
    """Vision variant: one screenshot in, schema-constrained JSON out."""
    import base64

    try:
        response = get_client().messages.create(
            model=settings.claude_model,
            max_tokens=max_tokens,
            system=system,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.b64encode(png_bytes).decode(),
                            },
                        },
                        {"type": "text", "text": "Answer based on this screenshot."},
                    ],
                }
            ],
        )
    except anthropic.APIError as e:
        raise _friendly_api_error(e) from e
    if response.stop_reason == "refusal":
        raise ClaudeError("Claude declined this request (refusal stop reason).")
    return json.loads("".join(block.text for block in response.content if block.type == "text"))


def generate_text(system: str, user_content: str, max_tokens: int = 16000) -> str:
    try:
        with get_client().messages.stream(
            model=settings.claude_model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            response = stream.get_final_message()
    except anthropic.APIError as e:
        raise _friendly_api_error(e) from e
    if response.stop_reason == "refusal":
        raise ClaudeError("Claude declined this request (refusal stop reason).")
    return "".join(block.text for block in response.content if block.type == "text")
