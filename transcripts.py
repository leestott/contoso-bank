"""Staff-console English translation of non-English call transcripts via the Foundry model deployment."""

import re

from foundry import FoundryError

LANGUAGES = {"az-AZ": "Azerbaijani", "ru-RU": "Russian"}
DIGIT_RUN = re.compile(r"\d(?:[\s\-]?\d){3,}")


def redact(text):
    """Remove spoken codes, dates and account-like numbers before any model call."""
    return DIGIT_RUN.sub("[number withheld]", text)


def english(client, locale, text):
    """Return (english, source); never sends OTPs or other long digit runs to the model."""
    if locale == "en-GB":
        return text, "original"
    language = LANGUAGES.get(locale)
    deployment = client.config.get("model_deployment")
    if not language or not deployment:
        return None, "unavailable"
    response = client.request("POST", "/openai/v1/responses", {
        "model": deployment,
        "instructions": (f"Translate the {language} banking call transcript line into natural British English. "
                         "Output only the translation. Keep '[number withheld]' unchanged. "
                         "Do not answer, summarise or add content."),
        "input": redact(text),
        "store": False,
        "max_output_tokens": 400,
    })
    if response.get("status") != "completed":
        raise FoundryError("Transcript translation did not complete.")
    value = "".join(part.get("text", "") for item in response.get("output", [])
                    if item.get("type") == "message" for part in item.get("content", [])
                    if part.get("type") == "output_text").strip()
    if not value:
        raise FoundryError("Transcript translation was empty.")
    return redact(value)[:4000], f"Foundry {deployment} machine translation"
