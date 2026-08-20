"""Outbound notification delivery. Only WhatsApp exists today; keep the provider isolated here so
adding another channel later doesn't require touching the collection pipeline."""
import json
import os
import urllib.error
import urllib.request


def send_whatsapp_alert(notice):
    required = ("WHATSAPP_API_URL", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_RECIPIENT", "WHATSAPP_TEMPLATE_NAME")
    if not all(os.getenv(key) for key in required):
        return "skipped", "WhatsApp is not configured"
    payload = {
        "messaging_product": "whatsapp", "to": os.environ["WHATSAPP_RECIPIENT"], "type": "template",
        "template": {
            "name": os.environ["WHATSAPP_TEMPLATE_NAME"],
            "language": {"code": os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en_US")},
            "components": [{"type": "body", "parameters": [
                {"type": "text", "text": notice["authority"]},
                {"type": "text", "text": notice["title"]},
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
