"""Tests for the redaction pipeline."""

from __future__ import annotations

import time

import pytest

from lore.redact.pipeline import RedactionPipeline, _luhn_check, redact


class TestLuhn:
    def test_valid_visa(self) -> None:
        assert _luhn_check("4111111111111111") is True

    def test_valid_mastercard(self) -> None:
        assert _luhn_check("5500000000000004") is True

    def test_invalid(self) -> None:
        assert _luhn_check("1234567890123456") is False

    def test_valid_amex(self) -> None:
        assert _luhn_check("378282246310005") is True


class TestAPIKeys:
    def setup_method(self) -> None:
        self.p = RedactionPipeline()

    def test_openai_key(self) -> None:
        assert self.p.run("key: sk-abc123def456ghi789jkl012") == "key: [REDACTED:api_key]"

    def test_aws_key(self) -> None:
        assert self.p.run("key AKIAIOSFODNN7EXAMPLE") == "key [REDACTED:api_key]"

    def test_github_pat(self) -> None:
        text = "token ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"
        assert "[REDACTED:api_key]" in self.p.run(text)

    def test_slack_bot(self) -> None:
        text = "xoxb-123456789012-abcdefghij"
        assert self.p.run(text) == "[REDACTED:api_key]"

    def test_no_false_positive(self) -> None:
        text = "the skeleton key"
        assert self.p.run(text) == text


class TestEmails:
    def setup_method(self) -> None:
        self.p = RedactionPipeline()

    def test_basic_email(self) -> None:
        assert self.p.run("mail me at user@example.com ok") == "mail me at [REDACTED:email] ok"

    def test_plus_email(self) -> None:
        assert "[REDACTED:email]" in self.p.run("user+tag@example.co.uk")

    def test_no_false_positive(self) -> None:
        assert self.p.run("@mention in slack") == "@mention in slack"


class TestPhones:
    def setup_method(self) -> None:
        self.p = RedactionPipeline()

    def test_us_format(self) -> None:
        result = self.p.run("Call (555) 123-4567 now")
        assert "[REDACTED:phone]" in result

    def test_international(self) -> None:
        result = self.p.run("Call +1-555-123-4567")
        assert "[REDACTED:phone]" in result

    def test_uk(self) -> None:
        result = self.p.run("Ring +44 20 7946 0958")
        assert "[REDACTED:phone]" in result

    def test_no_false_positive_short(self) -> None:
        text = "version 1.2.3"
        assert self.p.run(text) == text


class TestIPAddresses:
    def setup_method(self) -> None:
        self.p = RedactionPipeline()

    def test_ipv4(self) -> None:
        assert self.p.run("server at 192.168.1.100") == "server at [REDACTED:ip_address]"

    def test_ipv4_boundary(self) -> None:
        assert self.p.run("ip 255.255.255.255") == "ip [REDACTED:ip_address]"

    def test_ipv4_no_false_positive(self) -> None:
        # 999.999.999.999 is not a valid IP
        text = "999.999.999.999"
        assert self.p.run(text) == text

    def test_ipv6_full(self) -> None:
        result = self.p.run("addr 2001:0db8:85a3:0000:0000:8a2e:0370:7334")
        assert "[REDACTED:ip_address]" in result


class TestCreditCards:
    def setup_method(self) -> None:
        self.p = RedactionPipeline()

    def test_visa_valid(self) -> None:
        assert self.p.run("card 4111111111111111") == "card [REDACTED:credit_card]"

    def test_visa_with_spaces(self) -> None:
        assert self.p.run("card 4111 1111 1111 1111") == "card [REDACTED:credit_card]"

    def test_visa_with_dashes(self) -> None:
        assert self.p.run("card 4111-1111-1111-1111") == "card [REDACTED:credit_card]"

    def test_invalid_luhn_not_redacted(self) -> None:
        # 1234567890123456 fails Luhn — should NOT be redacted
        assert self.p.run("num 1234567890123456") == "num 1234567890123456"

    def test_mastercard_valid(self) -> None:
        assert self.p.run("mc 5500000000000004") == "mc [REDACTED:credit_card]"


class TestCustomPatterns:
    def test_custom_pattern(self) -> None:
        p = RedactionPipeline(custom_patterns=[(r"ACCT-\d+", "account_id")])
        assert p.run("account ACCT-12345678") == "account [REDACTED:account_id]"

    def test_multiple_custom(self) -> None:
        p = RedactionPipeline(
            custom_patterns=[
                (r"ACCT-\d+", "account_id"),
                (r"SSN-\d{3}-\d{2}-\d{4}", "ssn"),
            ]
        )
        text = "user ACCT-123 has SSN-123-45-6789"
        result = p.run(text)
        assert "[REDACTED:account_id]" in result
        assert "[REDACTED:ssn]" in result


class TestMultipleRedactions:
    def test_multiple_types(self) -> None:
        p = RedactionPipeline()
        text = "Email user@test.com from 192.168.1.1 with key sk-abcdefghij1234567890"
        result = p.run(text)
        assert "[REDACTED:email]" in result
        assert "[REDACTED:ip_address]" in result
        assert "[REDACTED:api_key]" in result


class TestConvenienceFunction:
    def test_redact_fn(self) -> None:
        result = redact("email: user@example.com")
        assert result == "email: [REDACTED:email]"


class TestPerformance:
    def test_under_5ms(self) -> None:
        p = RedactionPipeline()
        text = (
            "Contact user@example.com or call +1-555-123-4567. "
            "Server at 192.168.1.1. Key: sk-abc123def456ghi789jkl012. "
            "Card: 4111111111111111"
        )
        # Warm up
        p.run(text)
        start = time.perf_counter()
        for _ in range(100):
            p.run(text)
        elapsed = (time.perf_counter() - start) / 100
        assert elapsed < 0.005, f"Redaction took {elapsed*1000:.2f}ms (> 5ms)"
