"""
Unit tests for pure functions — no external services or DB required.
"""
import hashlib
import hmac

import pytest

from app.embeddings import chunk_diff
from app.models import RiskLevel
from app.notifications import _score_bar
from app.scorer import _determine_risk_level


# ── chunk_diff ────────────────────────────────────────────────────────────────

class TestChunkDiff:
    def test_empty_string_returns_one_chunk(self):
        assert chunk_diff("") == [""]

    def test_short_diff_stays_as_single_chunk(self):
        diff = "one line of change"
        assert chunk_diff(diff, chunk_size=500) == [diff]

    def test_large_diff_splits_into_multiple_chunks(self):
        diff = "\n".join(["+" + "x" * 50] * 30)   # ~1 500 chars
        chunks = chunk_diff(diff, chunk_size=200)
        assert len(chunks) > 1

    def test_all_lines_are_preserved_across_chunks(self):
        lines = [f"line {i}" for i in range(50)]
        diff = "\n".join(lines)
        chunks = chunk_diff(diff, chunk_size=100)
        rejoined = "\n".join(chunks)
        for line in lines:
            assert line in rejoined

    def test_exact_boundary_does_not_drop_last_chunk(self):
        # chunk_size=10, each line is exactly 5 chars → two lines per chunk
        diff = "\n".join(["abcde"] * 6)
        chunks = chunk_diff(diff, chunk_size=10)
        assert len(chunks) >= 1
        assert all(c for c in chunks)   # no empty chunks


# ── _determine_risk_level ─────────────────────────────────────────────────────

class TestRiskLevel:
    @pytest.mark.parametrize("score,expected", [
        (0.00, RiskLevel.LOW),
        (0.30, RiskLevel.LOW),
        (0.39, RiskLevel.LOW),
        (0.40, RiskLevel.MEDIUM),
        (0.50, RiskLevel.MEDIUM),
        (0.59, RiskLevel.MEDIUM),
        (0.60, RiskLevel.HIGH),
        (0.70, RiskLevel.HIGH),
        (0.79, RiskLevel.HIGH),
        (0.80, RiskLevel.CRITICAL),
        (0.90, RiskLevel.CRITICAL),
        (1.00, RiskLevel.CRITICAL),
    ])
    def test_boundary_values(self, score: float, expected: RiskLevel):
        assert _determine_risk_level(score) == expected


# ── _score_bar ────────────────────────────────────────────────────────────────

class TestScoreBar:
    def test_zero_is_all_empty(self):
        assert _score_bar(0.0) == "░" * 10

    def test_full_is_all_filled(self):
        assert _score_bar(1.0) == "█" * 10

    def test_half_is_five_and_five(self):
        bar = _score_bar(0.5)
        assert bar.count("█") == 5
        assert bar.count("░") == 5

    def test_bar_is_always_ten_chars(self):
        for score in [0.0, 0.1, 0.33, 0.5, 0.77, 1.0]:
            bar = _score_bar(score)
            assert len(bar) == 10


# ── _verify_github_signature ──────────────────────────────────────────────────

class TestWebhookSignature:
    SECRET = "test-webhook-secret"   # matches GITHUB_WEBHOOK_SECRET in conftest

    def _sign(self, body: bytes) -> str:
        digest = hmac.new(
            self.SECRET.encode(), body, digestmod=hashlib.sha256
        ).hexdigest()
        return f"sha256={digest}"

    def test_valid_signature_passes(self):
        from app.main import _verify_github_signature
        body = b'{"ref":"refs/heads/master"}'
        assert _verify_github_signature(body, self._sign(body)) is True

    def test_wrong_signature_fails(self):
        from app.main import _verify_github_signature
        body = b'{"ref":"refs/heads/master"}'
        assert _verify_github_signature(body, "sha256=deadbeef") is False

    def test_missing_header_fails(self):
        from app.main import _verify_github_signature
        assert _verify_github_signature(b"test", None) is False

    def test_missing_sha256_prefix_fails(self):
        from app.main import _verify_github_signature
        assert _verify_github_signature(b"test", "md5=abc123") is False

    def test_tampered_body_fails(self):
        from app.main import _verify_github_signature
        body = b'{"ref":"refs/heads/master"}'
        sig = self._sign(body)
        tampered = body + b"extra"
        assert _verify_github_signature(tampered, sig) is False
