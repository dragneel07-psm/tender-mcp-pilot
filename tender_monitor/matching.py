"""Milestone 5: company-to-tender matching. Pure business logic over the Milestone 2-4 schema
(categories, province, notice_type/status) -- no new infrastructure, no I/O in this module.

Each dimension explains itself (explainable per-dimension scoring): a profile that leaves a
dimension unset -- no categories / no provinces / no keywords configured -- has that dimension
excluded from the score entirely, not scored 0 or 1. Fabricating a preference the company never
stated would be worse than not scoring it at all, same "never fabricate" rule Milestones 2/3
followed for fields with no honest data source yet (district, estimated_amount, ...).
"""

# Category carries the most signal (it's the one dimension backed by a real, if rule-based,
# classifier -- Milestone 4). Province is a hard fact when present but many notices have none yet
# (province is stamped from the source registry, not every source has one -- see storage.py).
# Keyword is the weakest signal (free-text substring match) so it's weighted between the two.
DIMENSION_WEIGHTS = {"category": 0.5, "province": 0.2, "keyword": 0.3}

# Notices in these terminal states can no longer be bid on -- callers (queries.matches_for_company)
# exclude them entirely rather than scoring them low, since a "70% match" on an already-awarded
# tender would mislead a company into chasing it.
NON_ACTIONABLE_STATUSES = ("cancelled", "awarded")


def _category_dimension(profile_categories, notice_categories):
    if not profile_categories: return None
    wanted = {category.lower() for category in profile_categories}
    matched = [(row["category"], row["confidence_score"]) for row in notice_categories if row["category"].lower() in wanted]
    weight = DIMENSION_WEIGHTS["category"]
    if not matched:
        return {"dimension": "category", "weight": weight, "score": 0.0, "matched": [],
                "detail": "No overlap with the company's configured categories."}
    score = max(confidence for _, confidence in matched)
    names = [name for name, _ in matched]
    return {"dimension": "category", "weight": weight, "score": score, "matched": names,
            "detail": f"Matches {', '.join(names)} (classifier confidence {score:.1f})."}


def _province_dimension(profile_provinces, notice_province):
    if not profile_provinces: return None
    wanted = {province.lower() for province in profile_provinces}
    weight = DIMENSION_WEIGHTS["province"]
    if notice_province and notice_province.lower() in wanted:
        return {"dimension": "province", "weight": weight, "score": 1.0, "matched": [notice_province],
                "detail": f"{notice_province} is one of the company's target provinces."}
    detail = "Notice's province is not among the company's target provinces." if notice_province else "Notice has no recorded province."
    return {"dimension": "province", "weight": weight, "score": 0.0, "matched": [], "detail": detail}


def _keyword_dimension(profile_keywords, title, authority):
    if not profile_keywords: return None
    haystack = f"{title or ''} {authority or ''}".lower()
    matched = [keyword for keyword in profile_keywords if keyword.lower() in haystack]
    weight = DIMENSION_WEIGHTS["keyword"]
    if not matched:
        return {"dimension": "keyword", "weight": weight, "score": 0.0, "matched": [],
                "detail": "None of the company's keywords appear in the title or authority."}
    return {"dimension": "keyword", "weight": weight, "score": 1.0, "matched": matched,
            "detail": f"Matched keyword(s): {', '.join(matched)}."}


def match_tender_to_company(notice, profile):
    """Score one notice against one company profile.

    `notice` is a dict with at least `title`, `authority`, `province`, and `categories` (a list of
    {"category", "confidence_score"} rows, as returned by queries.details()/matches_for_company()).
    `profile` is a company_profile record (storage.validate_company_profile()'s shape).

    Returns {"score": 0..1, "dimensions": [...]}, where `dimensions` lists only the dimensions the
    profile actually configured -- skipped ones are omitted, not zeroed, so an unset preference
    never drags the score down. The overall score is the weighted average over active dimensions
    only (weights renormalized), so a profile with just one dimension set (e.g. only categories)
    still produces a meaningful 0..1 score instead of one capped at that dimension's raw weight.
    """
    dimensions = [dimension for dimension in (
        _category_dimension(profile.get("categories", []), notice.get("categories", [])),
        _province_dimension(profile.get("provinces", []), notice.get("province")),
        _keyword_dimension(profile.get("keywords", []), notice.get("title", ""), notice.get("authority", "")),
    ) if dimension is not None]
    total_weight = sum(dimension["weight"] for dimension in dimensions)
    score = sum(dimension["weight"] * dimension["score"] for dimension in dimensions) / total_weight if total_weight else 0.0
    return {"score": round(score, 4), "dimensions": dimensions}
