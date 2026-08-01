"""
Tests for cuny_scouter.professors — instructor name normalization and RMP matching.
"""
import pytest
from cuny_scouter.professors import normalize_instructor, classify, match_rmp


class TestNormalizeInstructor:
    def test_empty(self):
        assert normalize_instructor("") == ("", "", "")

    def test_none_equivalent(self):
        # None-like: empty string treated as staff
        key, last, initial = normalize_instructor("")
        assert key == "" and last == "" and initial == ""

    def test_staff(self):
        assert normalize_instructor("Staff") == ("", "", "")

    def test_staff_lowercase(self):
        assert normalize_instructor("staff") == ("", "", "")

    def test_tba(self):
        assert normalize_instructor("TBA") == ("", "", "")

    def test_firstname_lastname(self):
        key, last, initial = normalize_instructor("Pai Ma")
        assert last == "Ma"
        assert initial == "P"
        assert key == "ma p"

    def test_firstname_last_with_middle(self):
        key, last, initial = normalize_instructor("Jaime Muro Flomenbaum")
        assert last == "Flomenbaum"
        assert initial == "J"
        assert key == "flomenbaum j"

    def test_comma_format_last_first(self):
        key, last, initial = normalize_instructor("Smith,John")
        assert last == "Smith"
        assert initial == "J"
        assert key == "smith j"

    def test_comma_format_last_initial(self):
        key, last, initial = normalize_instructor("Smith,J")
        assert last == "Smith"
        assert initial == "J"
        assert key == "smith j"

    def test_comma_format_last_first_middle(self):
        key, last, initial = normalize_instructor("Smith,John A")
        assert last == "Smith"
        assert initial == "J"

    def test_single_name(self):
        key, last, initial = normalize_instructor("Muro")
        assert last == "Muro"
        assert initial == ""
        assert key == "muro"

    def test_accent_folded(self):
        # Accented characters should be ASCII-folded
        key, last, initial = normalize_instructor("José García")
        assert "garcia" in key

    def test_smushed_multi_instructor(self):
        # Multi-instructor cells: "Alice SmithBob Jones" — first name extracted
        key, last, initial = normalize_instructor("Alice SmithBob Jones")
        # Should parse the first name without crashing
        assert last != ""
        assert key != ""

    def test_returns_title_cased_last(self):
        _, last, _ = normalize_instructor("john smith")
        assert last == "Smith"


class TestClassify:
    def test_empty(self):
        assert classify("") == "staff"

    def test_staff(self):
        assert classify("Staff") == "staff"

    def test_tba(self):
        assert classify("TBA") == "staff"

    def test_real_name(self):
        assert classify("John Smith") == "name"

    def test_whitespace_only(self):
        assert classify("   ") == "staff"


class TestMatchRmp:
    def _node(self, first, last, num_ratings=10, avg=4.0):
        return {
            "firstName": first,
            "lastName": last,
            "avgRating": avg,
            "avgDifficulty": 2.5,
            "numRatings": num_ratings,
            "wouldTakeAgainPercent": 80.0,
            "legacyId": 12345,
            "rmpUrl": "https://www.ratemyprofessors.com/professor/12345",
        }

    def test_no_nodes(self):
        node, status = match_rmp("Smith", "J", [])
        assert node is None
        assert status == "no_match"

    def test_empty_last(self):
        nodes = [self._node("John", "Smith")]
        node, status = match_rmp("", "", nodes)
        assert node is None
        assert status == "no_match"

    def test_matched_exact(self):
        nodes = [self._node("John", "Smith"), self._node("Alice", "Jones")]
        node, status = match_rmp("Smith", "J", nodes)
        assert status == "matched"
        assert node["lastName"] == "Smith"

    def test_no_match_different_last(self):
        nodes = [self._node("John", "Smith")]
        node, status = match_rmp("Brown", "J", nodes)
        assert node is None
        assert status == "no_match"

    def test_no_match_wrong_initial(self):
        nodes = [self._node("John", "Smith")]
        node, status = match_rmp("Smith", "A", nodes)
        assert node is None
        assert status == "no_match"

    def test_matched_no_initial(self):
        # If no first initial, match on last name alone
        nodes = [self._node("John", "Smith")]
        node, status = match_rmp("Smith", "", nodes)
        assert status == "matched"

    def test_ambiguous_returns_highest_ratings(self):
        nodes = [
            self._node("John", "Smith", num_ratings=5),
            self._node("James", "Smith", num_ratings=90),
        ]
        node, status = match_rmp("Smith", "J", nodes)
        assert status == "ambiguous"
        assert node["numRatings"] == 90

    def test_case_insensitive(self):
        nodes = [self._node("john", "smith")]
        node, status = match_rmp("Smith", "J", nodes)
        assert status == "matched"
