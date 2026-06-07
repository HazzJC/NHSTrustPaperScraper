"""Tests for scraper/extraction.py — iter_links context extraction and _nearest_heading."""
import datetime as dt
import pytest
from scraper.extraction import iter_links, _nearest_heading, extract_date
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _soup_anchor(html: str, href: str = "#"):
    """Return a BeautifulSoup anchor element from an HTML fragment."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.find("a", href=href) or soup.find("a")


# ---------------------------------------------------------------------------
# _nearest_heading
# ---------------------------------------------------------------------------

class TestNearestHeading:
    def test_h3_preceding_sibling(self):
        html = """
        <div>
          <h3>Board Meeting — June 2026</h3>
          <a href="/doc.pdf">Download PDF</a>
        </div>
        """
        anchor = _soup_anchor(html)
        assert "Board Meeting" in _nearest_heading(anchor)

    def test_h2_at_parent_level(self):
        html = """
        <section>
          <h2>Annual Reports</h2>
          <ul>
            <li><a href="/doc.pdf">Download</a></li>
          </ul>
        </section>
        """
        anchor = _soup_anchor(html)
        assert "Annual Reports" in _nearest_heading(anchor)

    def test_strong_tag_treated_as_heading(self):
        html = """
        <div>
          <strong>Board Papers 2025-26</strong>
          <a href="/papers.pdf">View</a>
        </div>
        """
        anchor = _soup_anchor(html)
        assert "Board Papers" in _nearest_heading(anchor)

    def test_no_heading_returns_empty_string(self):
        html = """<div><p>Some text</p><a href="/doc.pdf">Link</a></div>"""
        anchor = _soup_anchor(html)
        assert _nearest_heading(anchor) == ""

    def test_heading_text_capped_at_120_chars(self):
        long_title = "A" * 200
        html = f"<div><h3>{long_title}</h3><a href='/doc.pdf'>Link</a></div>"
        anchor = _soup_anchor(html)
        result = _nearest_heading(anchor)
        assert len(result) <= 120

    def test_heading_inside_preceding_sibling_container(self):
        # heading nested inside a sibling div
        html = """
        <div>
          <div><h4>Committee Reports</h4></div>
          <a href="/doc.pdf">Download</a>
        </div>
        """
        anchor = _soup_anchor(html)
        assert "Committee Reports" in _nearest_heading(anchor)

    def test_nearest_heading_ignores_following_headings(self):
        # Heading that comes AFTER the anchor should not be returned
        html = """
        <div>
          <a href="/doc.pdf">Download</a>
          <h3>Later Section</h3>
        </div>
        """
        anchor = _soup_anchor(html)
        assert _nearest_heading(anchor) == ""

    def test_two_parent_levels_searched(self):
        # h2 is a sibling of <ul> — 2 parent levels above the anchor (anchor → li → ul)
        html = """
        <section>
          <h2>Deep Section</h2>
          <ul>
            <li><a href="/doc.pdf">Link</a></li>
          </ul>
        </section>
        """
        anchor = _soup_anchor(html)
        assert "Deep Section" in _nearest_heading(anchor)


# ---------------------------------------------------------------------------
# iter_links — 3-tuple output and context inclusion
# ---------------------------------------------------------------------------

class TestIterLinks:
    BASE_URL = "https://example.nhs.uk/"

    def _links(self, html):
        return list(iter_links(html, self.BASE_URL))

    def test_yields_three_tuple(self):
        html = '<a href="/doc.pdf">Download</a>'
        result = self._links(html)
        assert len(result) == 1
        url, link_text, context_text = result[0]
        assert isinstance(url, str)
        assert isinstance(link_text, str)
        assert isinstance(context_text, str)

    def test_link_text_extracted(self):
        html = '<a href="/doc.pdf">Board Papers</a>'
        url, link_text, _ = self._links(html)[0]
        assert link_text == "Board Papers"

    def test_url_resolved_to_absolute(self):
        html = '<a href="/doc.pdf">Link</a>'
        url, _, _ = self._links(html)[0]
        assert url == "https://example.nhs.uk/doc.pdf"

    def test_context_includes_container_text(self):
        html = """
        <li>Board Meeting June 2026 <a href="/doc.pdf">Download PDF</a></li>
        """
        url, link_text, context_text = self._links(html)[0]
        assert "Board Meeting" in context_text

    def test_context_includes_heading_text(self):
        html = """
        <div>
          <h3>Annual Strategy</h3>
          <a href="/strategy.pdf">Download</a>
        </div>
        """
        url, link_text, context_text = self._links(html)[0]
        assert "Annual Strategy" in context_text

    def test_skips_mailto_links(self):
        html = '<a href="mailto:info@nhs.uk">Contact</a>'
        assert self._links(html) == []

    def test_skips_tel_links(self):
        html = '<a href="tel:01234567890">Call</a>'
        assert self._links(html) == []

    def test_skips_javascript_links(self):
        html = '<a href="javascript:void(0)">Click</a>'
        assert self._links(html) == []

    def test_strips_fragment(self):
        html = '<a href="/page#section">Page</a>'
        url, _, _ = self._links(html)[0]
        assert "#" not in url

    def test_multiple_links_returned(self):
        html = """
        <div>
          <a href="/a.pdf">A</a>
          <a href="/b.pdf">B</a>
        </div>
        """
        results = self._links(html)
        assert len(results) == 2

    def test_empty_href_skipped(self):
        html = '<a href="">Empty</a>'
        assert self._links(html) == []

    def test_context_text_capped_reasonably(self):
        long_text = "word " * 100
        html = f'<li>{long_text}<a href="/doc.pdf">Download</a></li>'
        _, _, context_text = self._links(html)[0]
        # container_text is capped at 200 chars in the implementation
        assert len(context_text) <= 400  # 200 container + space + 120 heading


# ---------------------------------------------------------------------------
# extract_date — year plausibility guard
# ---------------------------------------------------------------------------

class TestExtractDateYearGuard:
    def test_valid_recent_date_accepted(self):
        date, source = extract_date("board-papers-2026-03-15")
        assert date == dt.date(2026, 3, 15)

    def test_far_future_year_rejected(self):
        # 2081 is syntactically valid (20xx) but implausible — should be discarded
        date, source = extract_date("board-papers-2081-08-20")
        assert date is None

    def test_far_future_compact_rejected(self):
        date, source = extract_date("20810820 agenda")
        assert date is None

    def test_far_future_month_name_rejected(self):
        date, source = extract_date("August 2081 board meeting")
        assert date is None

    def test_year_2000_accepted(self):
        date, source = extract_date("January 2000 report")
        assert date is not None
        assert date.year == 2000

    def test_year_1999_rejected(self):
        # Pre-2000 years outside the plausible window
        date, source = extract_date("15/06/1999")
        assert date is None

    def test_next_year_accepted(self):
        # Up to 1 year in the future is allowed (planned meetings)
        next_year = dt.date.today().year + 1
        date, source = extract_date(f"January {next_year} board meeting")
        assert date is not None
        assert date.year == next_year

    def test_two_years_future_rejected(self):
        too_far = dt.date.today().year + 2
        date, source = extract_date(f"January {too_far} board meeting")
        assert date is None

    def test_valid_dmy_date_accepted(self):
        date, source = extract_date("20/08/2024")
        assert date == dt.date(2024, 8, 20)

    def test_no_date_in_text_returns_none(self):
        date, source = extract_date("no date here at all")
        assert date is None
        assert source == "not_found"
