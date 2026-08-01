"""
Rate My Professors GraphQL client.

Only called from the worker process — never from the request path.
Ports the query shape from cunyfirstPlus/background.js.
"""
import base64
import logging

import httpx

log = logging.getLogger(__name__)

_RMP_URL = "https://www.ratemyprofessors.com/graphql"
_AUTH = "Basic dGVzdDp0ZXN0"  # "test:test" — public RMP API token

_QUERY = """
query SearchTeachers($text: String!, $schoolID: ID!) {
  newSearch {
    teachers(query: {text: $text, schoolID: $schoolID}) {
      edges {
        node {
          id
          firstName
          lastName
          avgRating
          avgDifficulty
          numRatings
          wouldTakeAgainPercent
          department
        }
      }
    }
  }
}
"""


def _encode_school_id(school_id: int) -> str:
    return base64.b64encode(f"School-{school_id}".encode()).decode()


def _decode_legacy_id(node_id: str) -> int | None:
    """Decode base64 'Teacher-<n>' to the numeric legacy id."""
    try:
        decoded = base64.b64decode(node_id).decode()
        return int(decoded.split("-", 1)[1])
    except Exception:
        return None


_HEADERS = {
    "Authorization": _AUTH,
    "Content-Type": "application/json",
    # RMP blocks requests that don't look like they come from a browser.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.ratemyprofessors.com",
    "Referer": "https://www.ratemyprofessors.com/",
}


def search_teacher(name: str, school_id: int, timeout: float = 10.0) -> list[dict]:
    """
    Search RMP for a teacher by name at the given school.
    Returns a list of node dicts with legacyId and rmpUrl added.
    Returns [] on any error (network, parse, rate-limit).
    """
    try:
        resp = httpx.post(
            _RMP_URL,
            headers=_HEADERS,
            json={
                "query": _QUERY,
                "variables": {
                    "text": name,
                    "schoolID": _encode_school_id(school_id),
                },
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        edges = (
            data.get("data", {})
            .get("newSearch", {})
            .get("teachers", {})
            .get("edges", [])
        )
        nodes = []
        for edge in edges:
            node = dict(edge.get("node", {}))
            legacy_id = _decode_legacy_id(node.get("id", ""))
            node["legacyId"] = legacy_id
            node["rmpUrl"] = (
                f"https://www.ratemyprofessors.com/professor/{legacy_id}"
                if legacy_id
                else None
            )
            nodes.append(node)
        return nodes
    except Exception as exc:
        log.warning("RMP search failed for %r: %s", name, exc)
        return []
