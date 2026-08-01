"""
Professor name normalization and RMP result matching.

CUNY instructor strings are inconsistent — "Smith,J", "Smith,John A",
"Smith", "Staff", or multiple names smushed together from get_text(strip=True).
All matching is done against the local `professors` table; this module never
makes HTTP calls.
"""
import re
import unicodedata

_STAFF_RE = re.compile(r"^(staff|tba|to be announced|tbann?)$", re.IGNORECASE)
_COMMA_SPLIT = re.compile(r"\s*,\s*")


def _ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()


def normalize_instructor(raw: str) -> tuple[str, str, str]:
    """
    Parse a raw CUNY instructor string.

    Returns (normalized_key, last_name, first_initial) where:
    - normalized_key: lowercased "lastname f" suitable as a DB key
    - last_name: Title-cased last name
    - first_initial: single uppercase letter, or "" if unknown

    For multi-instructor smushed strings, uses the first recognisable name.
    For Staff/empty/TBA, returns ("", "", "").
    """
    if not raw:
        return "", "", ""

    # Strip HTML leftovers and collapse whitespace
    cleaned = re.sub(r"<[^>]+>", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned or _STAFF_RE.match(cleaned):
        return "", "", ""

    # Pick the first name token from a potentially smushed multi-instructor string.
    # Multi-instructor cells look like "Alice SmithBob Jones" after get_text(strip=True).
    # Heuristic: stop at first capital letter that follows a lowercase letter (new name start).
    first_name_str = re.split(r"(?<=[a-z])(?=[A-Z])", cleaned)[0].strip()

    # Handle "Last,First" or "Last, F" CUNY format
    if "," in first_name_str:
        parts = _COMMA_SPLIT.split(first_name_str, 1)
        last = parts[0].strip()
        first_part = parts[1].strip() if len(parts) > 1 else ""
        first_initial = first_part[0].upper() if first_part else ""
    else:
        # Space-separated: first token could be first or last name
        # CUNY usually gives "FirstName LastName" order
        tokens = first_name_str.split()
        if len(tokens) >= 2:
            last = tokens[-1]
            first_initial = tokens[0][0].upper() if tokens[0] else ""
        elif len(tokens) == 1:
            last = tokens[0]
            first_initial = ""
        else:
            return "", "", ""

    last = _ascii_fold(last).title()
    normalized_key = f"{last.lower()} {first_initial.lower()}".strip()

    return normalized_key, last, first_initial


def classify(raw: str) -> str:
    """Return 'staff' for blank/Staff/TBA strings, 'name' otherwise."""
    if not raw:
        return "staff"
    cleaned = re.sub(r"\s+", " ", raw).strip()
    if not cleaned or _STAFF_RE.match(cleaned):
        return "staff"
    return "name"


def match_rmp(last: str, first_initial: str, nodes: list[dict]) -> tuple[dict | None, str]:
    """
    Match a (last_name, first_initial) pair against a list of RMP result nodes.

    Each node should have keys: firstName, lastName, avgRating, numRatings,
    avgDifficulty, wouldTakeAgainPercent, legacyId, rmpUrl.

    Returns (best_node | None, status) where status is one of:
      'matched'   — exactly one plausible match
      'ambiguous' — multiple plausible matches (returns highest numRatings)
      'no_match'  — no node matched
    """
    if not nodes or not last:
        return None, "no_match"

    last_lower = last.lower()
    first_lower = first_initial.lower() if first_initial else ""

    candidates = [
        n for n in nodes
        if n.get("lastName", "").lower() == last_lower
        and (
            not first_lower
            or n.get("firstName", "").lower().startswith(first_lower)
        )
    ]

    if not candidates:
        return None, "no_match"
    if len(candidates) == 1:
        return candidates[0], "matched"

    # Multiple matches — pick highest numRatings and mark ambiguous
    best = max(candidates, key=lambda n: n.get("numRatings", 0))
    return best, "ambiguous"
