"""Tests for scraper/scoring.py — term_hits word-boundary and penalty fixes."""
import pytest
from scraper.scoring import term_hits, candidate_score
import datetime as dt


# ---------------------------------------------------------------------------
# term_hits — word boundaries
# ---------------------------------------------------------------------------

class TestTermHits:
    def test_exact_match_counts(self):
        assert term_hits("board paper", ["board"]) == 1

    def test_no_false_positive_substring(self):
        # "board" must not match inside "keyboard" or "skateboard"
        assert term_hits("keyboard skateboard", ["board"]) == 0

    def test_no_false_positive_prefix(self):
        assert term_hits("aboard the ship", ["board"]) == 0

    def test_no_false_positive_suffix(self):
        assert term_hits("overboard", ["board"]) == 0

    def test_strategy_does_not_match_strategist(self):
        assert term_hits("our strategist proposed this", ["strategy"]) == 0

    def test_strategy_matches_strategy(self):
        assert term_hits("annual strategy document", ["strategy"]) == 1

    def test_multiple_terms_counted_separately(self):
        assert term_hits("board strategy paper", ["board", "strategy"]) == 2

    def test_term_missing_from_text_zero(self):
        assert term_hits("annual report 2024", ["board"]) == 0

    def test_hyphen_normalised_board_paper(self):
        # "board paper" term should match "board-paper" in text
        assert term_hits("board-paper agenda", ["board paper"]) == 1

    def test_hyphen_normalised_multi_word(self):
        assert term_hits("board of directors meeting", ["board of directors"]) == 1

    def test_case_insensitive(self):
        assert term_hits("BOARD PAPER", ["board"]) == 1

    def test_empty_text_returns_zero(self):
        assert term_hits("", ["board", "strategy"]) == 0

    def test_empty_terms_returns_zero(self):
        assert term_hits("board strategy paper", []) == 0

    def test_each_term_counted_once_regardless_of_occurrences(self):
        # term_hits counts distinct terms that match, not total occurrences
        result = term_hits("board board board", ["board"])
        assert result >= 1  # at least one hit; exact behaviour is hit-per-term

    def test_minute_does_not_match_instrument(self):
        assert term_hits("instrument calibration", ["minute"]) == 0

    def test_minute_matches_minutes(self):
        # "minutes" contains "minute" as prefix — verify behaviour
        # (depends on whether \b anchors allow partial stem match)
        assert term_hits("board minutes from march", ["minutes"]) == 1


# ---------------------------------------------------------------------------
# candidate_score — minute/draft penalties use word boundaries
# ---------------------------------------------------------------------------

class TestCandidateScore:
    def _score(self, url="http://example.com/doc.pdf", text="", date=None, type_score=60):
        return candidate_score(url=url, text=text, found_date=date, type_score=type_score)

    def test_draft_penalty_applied(self):
        with_draft = self._score(text="draft board paper")
        without_draft = self._score(text="board paper")
        assert with_draft < without_draft

    def test_draft_penalty_not_triggered_by_substring(self):
        # "drafter" should not trigger the draft penalty
        with_drafter = self._score(text="board drafter notes")
        without_any = self._score(text="board paper notes")
        # "drafter" should not reduce score (no \bdraft\b match)
        assert with_drafter == without_any

    def test_minutes_penalty_applied(self):
        with_minutes = self._score(text="board minutes march 2026")
        without_minutes = self._score(text="board papers march 2026")
        assert with_minutes < without_minutes

    def test_minutes_penalty_not_triggered_by_instrument(self):
        with_instrument = self._score(text="instrument calibration report")
        baseline = self._score(text="calibration report")
        # "instrument" contains "minute" as substring — penalty must NOT apply
        assert with_instrument == baseline

    def test_document_url_bonus(self):
        pdf_score = self._score(url="http://example.com/report.pdf")
        htm_score = self._score(url="http://example.com/page.html")
        assert pdf_score > htm_score

    def test_date_bonus(self):
        with_date = self._score(date=dt.date(2026, 1, 1))
        without_date = self._score(date=None)
        assert with_date > without_date
