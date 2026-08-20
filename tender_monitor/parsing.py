"""HTML parsing and date/text extraction primitives. Pure functions/classes: no network, no I/O."""
import html
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

from .config import TENDER_WORDS


class LinkTextParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self._href=None; self._parts=[]
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href"); self._parts=[]
    def handle_data(self, data):
        if self._href: self._parts.append(data)
    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._parts).strip()))
            self._href=None; self._parts=[]


class OfficialDirectoryParser(HTMLParser):
    """Extract local-level rows and website links from the official Ministry directory."""
    def __init__(self):
        super().__init__(); self.rows=[]; self._in_row=False; self._in_cell=False; self._cells=[]; self._cell_parts=[]; self._links=[]; self._link=None
    def handle_starttag(self, tag, attrs):
        if tag == "tr": self._in_row=True; self._cells=[]; self._links=[]
        elif self._in_row and tag in ("td", "th"): self._in_cell=True; self._cell_parts=[]
        elif self._in_row and tag == "a": self._link=dict(attrs).get("href")
    def handle_data(self, data):
        if self._in_cell: self._cell_parts.append(data)
    def handle_endtag(self, tag):
        if tag == "a":
            if self._link: self._links.append(self._link)
            self._link=None
        elif self._in_row and tag in ("td", "th"):
            self._cells.append(clean(" ".join(self._cell_parts))); self._in_cell=False
        elif tag == "tr" and self._in_row:
            if self._cells: self.rows.append((self._cells, self._links))
            self._in_row=False


def clean(text): return re.sub(r"\s+", " ", html.unescape(text)).strip()


DATE_PATTERNS = (
    r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b",
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b",
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4}\b",
    r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{4}\b",
    r"[०-९]{4}[/-][०-९]{1,2}[/-][०-९]{1,2}",
)


def first_date(text):
    for pattern in DATE_PATTERNS:
        match=re.search(pattern, text, re.IGNORECASE)
        if match: return match.group(0)
    return None


# Milestone 7: turning one of first_date()'s extracted strings into a real date, for the rare
# caller (deadline-reminder scheduling) that needs to do date arithmetic rather than just display
# the string. Deliberately conservative and separate from first_date() itself: only the numeric
# and month-name formats are attempted (never the Devanagari-digit pattern -- see below), and a
# successful parse is still discarded unless it falls in a plausible window around today.
#
# Nepali government sites routinely publish dates in the Bikram Sambat calendar (currently ~56-57
# years ahead of Gregorian) with nothing in the source text marking which calendar a given date
# uses. A BS date string parsed as if it were Gregorian lands decades in the future/past --
# the plausibility bound is what catches that (and any other parse gone wrong) rather than
# silently trusting a date arithmetic can't actually justify (never fabricate).
MONTH_NUMBERS = {
    "jan":1,"january":1,"feb":2,"february":2,"mar":3,"march":3,"apr":4,"april":4,"may":5,
    "jun":6,"june":6,"jul":7,"july":7,"aug":8,"august":8,"sep":9,"sept":9,"september":9,
    "oct":10,"october":10,"nov":11,"november":11,"dec":12,"december":12,
}
_MONTH_FIRST = re.compile(r"^([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})$")
_DAY_FIRST_MONTH = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})$")
PLAUSIBLE_PAST = timedelta(days=365)
PLAUSIBLE_FUTURE = timedelta(days=1095)


def to_calendar_date(date_string):
    """A real date.date, or None if date_string can't be confidently parsed as one. Assumes
    day-before-month for the numeric d/m/y pattern (the regional convention DATE_PATTERNS itself
    already follows) and never guesses at the alternative."""
    if not date_string: return None
    text = clean(date_string)
    candidate = None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            candidate = datetime.strptime(text, fmt).date(); break
        except ValueError:
            continue
    if candidate is None:
        for pattern, order in ((_MONTH_FIRST, "mdy"), (_DAY_FIRST_MONTH, "dmy")):
            match = pattern.match(text)
            if not match: continue
            month_name, day, year = (match.group(1), match.group(2), match.group(3)) if order == "mdy" \
                else (match.group(2), match.group(1), match.group(3))
            month = MONTH_NUMBERS.get(month_name.lower())
            if not month: continue
            try:
                candidate = date(int(year), month, int(day)); break
            except ValueError:
                continue
    if candidate is None: return None
    today = date.today()
    if not (today - PLAUSIBLE_PAST <= candidate <= today + PLAUSIBLE_FUTURE):
        return None
    return candidate


def context_snippet(body, href):
    """The ~1.6KB of listing-page text around a link, HTML-stripped and cleaned, or "" if the href
    isn't found verbatim in body. This is the richest text we extract that ISN'T part of a
    notice's identity digest (source_id+url+title) -- see content_hash in collector.py, which
    hashes this specifically so it can later detect "something about this listing entry changed"
    even when the title/URL that define identity stayed the same."""
    position=body.find(href)
    if position < 0: return ""
    return clean(re.sub(r"<[^>]+>", " ", body[max(0,position-700):position+900]))


def published_date(body, href, title):
    """Return a published-date string only when the source page exposes one near its link."""
    for candidate in (title, context_snippet(body, href)):
        date=first_date(candidate)
        if date: return date
    return None


def relevant(text, source):
    lower=text.lower()
    return any(word.lower() in lower for word in TENDER_WORDS + tuple(source.get("keywords", [])))


# Rule-based, title-only classification -- no document intelligence required (that's Milestone 3).
# Checked in this order because a notice can contain multiple signal words (e.g. a corrigendum
# about a cancelled tender); the most specific/actionable classification wins.
NOTICE_TYPE_KEYWORDS = (
    ("cancellation", ("cancel", "cancelled", "cancellation", "रद्द")),
    ("award", ("award", "awarded", "स्वीकृत")),
    ("corrigendum", ("corrigendum", "addendum", "amendment", "संशोधन", "थपघट")),
)
NOTICE_STATUS_BY_TYPE = {"cancellation": "cancelled", "award": "awarded"}


def classify_notice_type(title):
    """One of 'cancellation', 'award', 'corrigendum', or the default 'tender_notice'."""
    lower=title.lower()
    for notice_type, keywords in NOTICE_TYPE_KEYWORDS:
        if any(keyword.lower() in lower for keyword in keywords):
            return notice_type
    return "tender_notice"


def status_for_notice_type(notice_type):
    """Derived purely from notice_type, since we don't yet extract a real deadline to compute an
    open/closed status from (that needs document intelligence -- Milestone 3). 'active' is a
    default meaning "still being collected as relevant", not a claim about a real deadline."""
    return NOTICE_STATUS_BY_TYPE.get(notice_type, "active")


# Rule-based, title-only category classification (Milestone 4). A notice can match several
# categories (e.g. "CCTV and networking equipment for..." is both CCTV and Networking) -- every
# match is kept, not just the first. Confidence is flat and deliberately unambitious: 0.6 means
# "a keyword matched", nothing more precise than that is honestly knowable from title text alone.
# Most real notices collected so far are civil-works (roads, buildings, water supply), which is why
# those categories' keyword lists are the most battle-tested against real data; the IT/tech
# categories exist per the target taxonomy but will fire rarely until the source registry expands
# beyond local governments into ministries/departments/PSEs where tech tenders are more common.
CATEGORY_KEYWORDS = (
    ("CCTV", ("cctv", "camera", "सिसिटिभी")),
    ("Networking", ("network", "networking", "wifi", "नेटवर्क")),
    ("Telecommunication", ("telecom", "telephone", "दूरसञ्चार")),
    ("Software", ("software", "सफ्टवेयर")),
    ("Cybersecurity", ("cybersecurity", "firewall")),
    ("Cloud", ("cloud", "क्लाउड")),
    ("Server", ("server",)),
    ("Data Center", ("data center", "data centre")),
    ("Hardware", ("hardware", "laptop", "computer", "ल्यापटप", "कम्प्युटर")),
    ("IT", ("आईटी", "सूचना प्रविधि", "information technology")),
    ("Solar", ("solar", "सोलार", "सौर्य")),
    ("Electrical", ("electrical", "electricity", "विद्युत")),
    ("Road", ("road", "सडक", "बाटो")),
    ("Water Supply", ("water supply", "खानेपानी")),
    ("Building", ("building", "भवन")),
    ("Civil Construction", ("construction", "निर्माण")),
    ("Medical", ("medical", "hospital", "स्वास्थ्य", "अस्पताल")),
    ("Agriculture", ("agriculture", "कृषि")),
    ("Education", ("school", "शिक्षा", "विद्यालय")),
    ("Consulting", ("consulting", "consultancy", "परामर्श")),
    ("Vehicles", ("vehicle", "सवारी")),
    ("Furniture", ("furniture", "फर्निचर")),
    ("Printing", ("printing", "छपाई")),
    ("Garments", ("garment", "uniform", "पोशाक")),
    ("Office Supplies", ("stationery", "स्टेशनरी")),
)
CATEGORY_MATCH_CONFIDENCE = 0.6
UNCATEGORIZED_CONFIDENCE = 0.5


def classify_categories(title):
    """List of (category, confidence_score) tuples. Never empty: a title matching nothing gets a
    single ('Other', 0.5) row, so every notice has at least one category to query on."""
    lower=title.lower()
    matches=[category for category, keywords in CATEGORY_KEYWORDS if any(k.lower() in lower for k in keywords)]
    if not matches: return [("Other", UNCATEGORIZED_CONFIDENCE)]
    return [(category, CATEGORY_MATCH_CONFIDENCE) for category in matches]
