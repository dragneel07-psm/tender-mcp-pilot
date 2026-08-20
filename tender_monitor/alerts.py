"""Outbound notification delivery. Milestone 7: AlertProvider abstracts channel-specific
formatting/delivery so collector.py and reminders.py -- both callers of send_alert() -- don't need
to know WhatsApp's specific 3-parameter template shape or how "why this alert fired" maps onto it.
WhatsAppAlertProvider is the only implementation today, same as before this milestone; adding a
real second channel (email, Slack, ...) means adding a class here, not touching either caller.
"""
import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

# What each delivery `reason` (also stored verbatim in deliveries.reason -- see storage.py) means
# for display purposes. "new_notice" -> no prefix, the original unannotated alert shape from
# before change detection existed. Anything not listed here (a typo'd or future reason) also gets
# no prefix rather than raising -- degrade gracefully instead of failing a collection cycle over a
# cosmetic label.
REASON_LABELS = {
    "TENDER_CANCELLED": "CANCELLED",
    "DEADLINE_CHANGED": "DEADLINE CHANGED",
    "CORRIGENDUM": "CORRIGENDUM",
    "deadline_reminder": "DEADLINE REMINDER",
}


class AlertProvider(ABC):
    @abstractmethod
    def is_configured(self):
        """True if this provider has everything it needs (credentials, recipient, ...) to
        actually attempt a send. Doesn't touch the network."""
        raise NotImplementedError

    @abstractmethod
    def send(self, notice, reason):
        """Send one alert about `notice` for the given `reason` (a deliveries.reason value, e.g.
        "new_notice" or a notice_changes.change_type). Must never raise -- returns (status, detail)
        even on failure, same contract collector.py already depends on."""
        raise NotImplementedError


class WhatsAppAlertProvider(AlertProvider):
    """Wraps the single approved WhatsApp template (3 body params: local government, notice
    title, notice link) that has been this project's only alert channel since before this
    milestone. A `reason` other than "new_notice" is folded into the title parameter as a
    bracketed prefix (e.g. "[DEADLINE CHANGED] ...") -- the one honest way to hint "this is an
    update" within a fixed-shape template that a distinct per-reason template would properly
    solve; that's real future scope (a second, purpose-built template), not this milestone's."""
    REQUIRED_ENV = ("WHATSAPP_API_URL", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_RECIPIENT", "WHATSAPP_TEMPLATE_NAME")

    def is_configured(self):
        return all(os.getenv(key) for key in self.REQUIRED_ENV)

    def send(self, notice, reason):
        if not self.is_configured():
            return "skipped", "WhatsApp is not configured"
        label = REASON_LABELS.get(reason)
        title = f"[{label}] {notice['title']}" if label else notice["title"]
        payload = {
            "messaging_product": "whatsapp", "to": os.environ["WHATSAPP_RECIPIENT"], "type": "template",
            "template": {
                "name": os.environ["WHATSAPP_TEMPLATE_NAME"],
                "language": {"code": os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en_US")},
                "components": [{"type": "body", "parameters": [
                    {"type": "text", "text": notice["authority"]},
                    {"type": "text", "text": title},
                    {"type": "text", "text": notice["url"]}
                ]}]
            }
        }
        request = urllib.request.Request(
            os.environ["WHATSAPP_API_URL"], data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": "Bearer " + os.environ["WHATSAPP_ACCESS_TOKEN"], "Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return "sent", response.read().decode(errors="replace")[:500]
        except urllib.error.HTTPError as exc:
            return "error", exc.read().decode(errors="replace")[:500]
        except urllib.error.URLError as exc:
            return "error", str(exc)


# The collection pipeline and reminders.py always use this today -- a module-level list (not a
# factory), mirroring adapters.DEFAULT_ADAPTER's singleton pattern. Easy to extend to more than
# one provider later without touching either caller.
PROVIDERS = [WhatsAppAlertProvider()]


def send_alert(notice, reason="new_notice"):
    """Dispatch one notice/reason through the registered provider(s). Only one provider exists
    today, so this returns that provider's (status, detail) directly rather than a list -- keeps
    the deliveries table's existing (status, detail) shape unchanged. Revisit this return shape
    if/when a second provider is actually added, not speculatively now."""
    return PROVIDERS[0].send(notice, reason)
