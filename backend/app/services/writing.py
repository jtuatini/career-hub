"""AI-tell guardrails for generated prose (cover letters, answers).

Three layers, all cheap and local:
- AI_WRITING_RULES: a prompt block appended to prose-writing system prompts.
- scrub(): deterministic cleanup of tells that are pure punctuation (em/en
  dashes as clause separators) — safe to apply blindly.
- find_tells(): detector for the fuzzier tells (banned stock phrases, the
  rule-of-three, negative parallelism). Callers use a hit to trigger ONE
  revision round through the engine (REVISE_SYSTEM), then scrub again.
"""

import re

AI_WRITING_RULES = """\
## Writing rules — violations get the draft rejected
- NEVER use em-dashes (—) or dashes as clause separators. Use a comma, a \
period, or parentheses instead.
- Avoid the "rule of three": do not line up three parallel items \
("fast, reliable, and scalable"). Use one, two, or four, or restructure.
- No negative parallelism: never "not just X, but Y", "it's not X, it's Y", \
"not only ... but also".
- Never use these words/phrases: delve, tapestry, testament, leverage, \
utilize, seamless, robust, dynamic, passionate, thrilled, esteemed, \
spearheaded, honed, fostered, keen, showcase, underscore, "I am excited", \
"I am writing to express", "proven track record", "unique blend", \
"hit the ground running", "aligns perfectly", "fast-paced", "ever-evolving", \
"meaningful impact", "wealth of experience", "valuable insights", moreover, \
furthermore, "in conclusion", "in today's".
- Vary sentence length. Write like one specific person with something to \
say, not a press release."""

REVISE_SYSTEM = """\
You remove AI-writing tells from a draft without rewriting its substance.
Fix ONLY the listed problems. Keep the meaning, facts, first-person voice,
paragraph structure, and roughly the same length. Return only the revised
text — no commentary.

""" + AI_WRITING_RULES

# Phrases that read as machine-written filler. Lowercase; matched
# case-insensitively on word boundaries.
BANNED_PHRASES = [
    "delve",
    "tapestry",
    "testament to",
    "leverage",
    "leveraging",
    "utilize",
    "utilizing",
    "seamless",
    "robust",
    "passionate",
    "thrilled",
    "esteemed",
    "spearheaded",
    "honed",
    "fostered",
    "keen ",
    "showcase",
    "underscore",
    "i am excited",
    "i'm excited",
    "excited to apply",
    "i am writing to express",
    "proven track record",
    "unique blend",
    "hit the ground running",
    "aligns perfectly",
    "fast-paced",
    "ever-evolving",
    "meaningful impact",
    "wealth of experience",
    "valuable insights",
    "moreover",
    "furthermore",
    "in conclusion",
    "in today's",
]

_BANNED_RES = [
    (p, re.compile(r"(?<![\w'])" + re.escape(p), re.IGNORECASE)) for p in BANNED_PHRASES
]

# "word, word, and word" — the tricolon AI leans on. Single words on each leg
# keeps false positives low; one instance is human, repetition is the tell.
_TRIAD = re.compile(r"\b[\w'-]+,\s+[\w'-]+,\s+(?:and|or)\s+[\w'-]+\b", re.IGNORECASE)

_NEG_PARALLEL = re.compile(
    r"\bnot\s+(?:just|only|merely|simply)\b.{0,80}?\bbut\b|\bit'?s\s+not\b.{0,60}?\bit'?s\b",
    re.IGNORECASE | re.DOTALL,
)

_DASHES = "–—―"  # en dash, em dash, horizontal bar


def scrub(text: str) -> str:
    """Deterministically remove dash-tells. Safe on any prose."""
    # Numeric ranges keep a plain hyphen: 2024–2025 -> 2024-2025.
    out = re.sub(rf"(?<=\d)[{_DASHES}](?=\d)", "-", text)
    # Any remaining em/en dash is a clause separator: normalize to a comma.
    out = re.sub(rf"\s*[{_DASHES}]+\s*", ", ", out)
    # A spaced hyphen between letters is the ASCII spelling of the same tell.
    out = re.sub(r"(?<=[a-zA-Z])\s+-\s+(?=[a-zA-Z])", ", ", out)
    # Cleanup: collapse doubled commas/spaces the substitutions can leave.
    out = re.sub(r",\s*,", ", ", out)
    out = re.sub(r" {2,}", " ", out)
    return out


def find_tells(text: str) -> list[str]:
    """Human-readable list of AI tells present in `text`. Empty when clean."""
    tells: list[str] = []
    if re.search(rf"[{_DASHES}]", text):
        tells.append("uses em/en dashes as clause separators")
    for phrase, rx in _BANNED_RES:
        if rx.search(text):
            tells.append(f'banned phrase: "{phrase.strip()}"')
    triads = _TRIAD.findall(text)
    if len(triads) >= 2:
        tells.append(
            f"rule-of-three overuse ({len(triads)} triads, e.g. \"{triads[0]}\")"
        )
    m = _NEG_PARALLEL.search(text)
    if m:
        tells.append(f'negative parallelism ("{m.group(0)[:60]}")')
    return tells
