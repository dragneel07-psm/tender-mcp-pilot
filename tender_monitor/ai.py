"""Milestone 10: AI-assisted extraction of fields document intelligence alone can't reliably
attribute -- estimated_amount, bid_security_amount, eligibility. Milestone 3 deliberately left
these null: a tender document routinely states several monetary figures (document fee, bid
security, estimated cost), and picking the right one via regex without real language
understanding is unreliable enough that a wrong-but-confident number is worse than none.

Strictly optional (engineering rule #13): collection, search, and alerts all keep working with
zero AI configured, exactly like alerts.send_alert()'s "skipped" path when WhatsApp isn't set up.
AIProvider is provider-independent (target spec §8) -- AnthropicProvider is the only
implementation today, built on plain urllib rather than an SDK (the same choice alerts.py already
made for WhatsApp's Graph API), so this milestone adds zero new dependencies. A second provider
means adding a class to PROVIDERS, not touching collector.py.

Every result is provenance-tagged (target spec §29): the notices columns this writes to
(estimated_amount, bid_security_amount, eligibility_summary, ai_provider, ai_extraction_status,
ai_extracted_at) have no other writer anywhere in this codebase, so their presence unambiguously
means an AI model produced them -- collector.py never overwrites an already-set value with a new
AI guess (see collect_one's ai extraction block), and nothing here ever touches a source-derived
or rule-based field.
"""
import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

# Instructs the model to return null rather than infer/estimate anything not explicitly present --
# the same "never fabricate" discipline this codebase already applies to regex-based extraction
# (documents.extract_submission_deadline's keyword-gating is the non-AI version of this same rule).
EXTRACTION_PROMPT = """You are extracting structured facts from a Nepali government tender document. Only extract information that is EXPLICITLY stated in the text below. If a field is not clearly and explicitly stated, return null for it -- never guess, infer, or estimate a value that is not directly present in the text.

Respond with ONLY a JSON object, no other text, with exactly these three keys:
{{"estimated_amount": "<the estimated contract/project cost as stated, including currency, or null>", "bid_security_amount": "<the bid security / earnest money amount as stated, including currency, or null>", "eligibility_summary": "<a one-sentence summary of stated eligibility criteria, or null>"}}

Document text:
{text}"""

MAX_INPUT_CHARS = 8000  # bounds prompt size and per-call cost
MAX_FIELD_CHARS = 300  # a field value this long is almost certainly a parsing/prompt-following failure, not a real answer


class AIProvider(ABC):
    @abstractmethod
    def is_configured(self):
        raise NotImplementedError

    @abstractmethod
    def extract(self, document_text):
        """Return a dict with a "status" key ("ok"/"parse_failed"/"error") and, when "ok",
        estimated_amount/bid_security_amount/eligibility_summary (each a string or None). Never
        raises -- every outcome, including failure, is a typed status, same contract
        documents.download_and_extract already follows."""
        raise NotImplementedError


def _clean_field(value):
    if not isinstance(value, str): return None
    value = value.strip()
    if not value or value.lower() == "null": return None
    return value[:MAX_FIELD_CHARS]


class AnthropicProvider(AIProvider):
    API_URL = "https://api.anthropic.com/v1/messages"
    DEFAULT_MODEL = "claude-haiku-4-5-20251001"  # cheap/fast -- this is bounded extraction, not conversation

    def is_configured(self):
        return bool(os.getenv("ANTHROPIC_API_KEY"))

    def extract(self, document_text):
        if not self.is_configured():
            return {"status": "not_configured"}
        payload = {
            "model": os.getenv("AI_MODEL", self.DEFAULT_MODEL), "max_tokens": 500,
            "messages": [{"role": "user", "content": EXTRACTION_PROMPT.format(text=document_text[:MAX_INPUT_CHARS])}],
        }
        request = urllib.request.Request(
            self.API_URL, data=json.dumps(payload).encode(), method="POST",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return {"status": "error", "detail": exc.read().decode(errors="replace")[:300]}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return {"status": "error", "detail": str(exc)}
        try:
            parsed = json.loads(body["content"][0]["text"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            return {"status": "parse_failed"}
        if not isinstance(parsed, dict):
            return {"status": "parse_failed"}
        return {
            "status": "ok",
            "estimated_amount": _clean_field(parsed.get("estimated_amount")),
            "bid_security_amount": _clean_field(parsed.get("bid_security_amount")),
            "eligibility_summary": _clean_field(parsed.get("eligibility_summary")),
        }


PROVIDERS = {"anthropic": AnthropicProvider}


def configured_provider():
    """The provider named by AI_PROVIDER, or None if it's unset, names an unknown provider, or
    that provider isn't configured (missing API key) -- the single place every caller checks
    before doing anything AI-related, mirroring alerts.py's provider-selection pattern."""
    provider_cls = PROVIDERS.get(os.getenv("AI_PROVIDER", "").strip().lower())
    if provider_cls is None: return None
    provider = provider_cls()
    return provider if provider.is_configured() else None
