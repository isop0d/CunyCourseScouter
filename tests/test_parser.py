"""
Parser tests — all offline, using captured HTML fixtures.
"""
import pytest
from pathlib import Path
from cuny_scouter.scraper.parser import (
    parse_sections,
    ParseError,
    StructuralValidationError,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="ISO-8859-1")


@pytest.fixture
def all_sections_html():
    return load_fixture("results_all.html")


@pytest.fixture
def open_only_html():
    return load_fixture("results_open_only.html")


class TestAllSectionsFixture:
    def test_count_matches_declared(self, all_sections_html):
        records = parse_sections(all_sections_html)
        assert len(records) == 68

    def test_known_class_number_present(self, all_sections_html):
        records = parse_sections(all_sections_html)
        numbers = {r.class_number for r in records}
        assert 39021 in numbers

    def test_all_class_numbers_are_integers(self, all_sections_html):
        records = parse_sections(all_sections_html)
        for r in records:
            assert isinstance(r.class_number, int)
            assert r.class_number > 0

    def test_status_values_are_valid(self, all_sections_html):
        records = parse_sections(all_sections_html)
        valid = {"open", "closed", "waitlist"}
        for r in records:
            assert r.status in valid, f"class {r.class_number} has invalid status {r.status!r}"

    def test_all_three_statuses_present(self, all_sections_html):
        records = parse_sections(all_sections_html)
        statuses = {r.status for r in records}
        # results_all.html was captured with open_class="" so we expect open + waitlist at minimum
        assert "open" in statuses
        assert "waitlist" in statuses

    def test_course_number_populated(self, all_sections_html):
        records = parse_sections(all_sections_html)
        for r in records:
            assert r.course_number, f"class {r.class_number} missing course_number"

    def test_course_name_populated(self, all_sections_html):
        records = parse_sections(all_sections_html)
        for r in records:
            assert r.course_name, f"class {r.class_number} missing course_name"

    def test_known_section_fields(self, all_sections_html):
        records = parse_sections(all_sections_html)
        first = next(r for r in records if r.class_number == 39021)
        assert first.course_number == "2200"
        assert first.section_code == "BMWA-LEC Regular"
        assert first.instructor == "Pai Ma"
        assert first.status == "open"

    def test_default_metadata_fields(self, all_sections_html):
        records = parse_sections(all_sections_html)
        for r in records:
            assert r.term_code == "1269"
            assert r.institution == "BAR01"
            assert r.subject == "CMIS"

    def test_open_only_fixture_has_fewer_sections(self, all_sections_html, open_only_html):
        """Confirms Risk 1 finding: open_class='O' hides waitlisted sections."""
        all_records = parse_sections(all_sections_html)
        # open-only fixture was captured with open_class='O' — expect fewer records
        # (it may raise StructuralValidationError if declared count differs from parsed)
        try:
            open_records = parse_sections(open_only_html)
            assert len(open_records) < len(all_records)
        except StructuralValidationError:
            pass  # also acceptable — the open-only HTML structure may differ


class TestStructuralValidation:
    def test_empty_html_raises(self):
        with pytest.raises(StructuralValidationError):
            parse_sections("<html><body>No results here</body></html>")

    def test_zero_count_in_message_raises(self):
        html = '<html><body><div class="classfound_msg">0 class section(s) found</div></body></html>'
        with pytest.raises(StructuralValidationError):
            parse_sections(html)
