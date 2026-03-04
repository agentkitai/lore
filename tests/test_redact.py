"""Tests for the redaction pipeline."""

from __future__ import annotations

import time

from lore.redact.patterns import shannon_entropy
from lore.redact.pipeline import Finding, RedactionPipeline, ScanResult, _luhn_check, redact


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


# ====================================================================
# F2-S1: New L1 patterns
# ====================================================================


class TestJWTTokens:
    def setup_method(self) -> None:
        self.p = RedactionPipeline()

    def test_jwt_detected(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        result = self.p.run(f"token: {jwt}")
        assert "[REDACTED:jwt_token]" in result

    def test_jwt_short_segments_not_matched(self) -> None:
        # Too short to be a real JWT
        text = "eyJh.eyJz.abc"
        assert self.p.run(text) == text

    def test_jwt_in_header(self) -> None:
        jwt = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJodHRwczovL2V4YW1wbGUuY29tIn0.signature1234567890abcdef"
        result = self.p.run(f"Authorization: Bearer {jwt}")
        assert "[REDACTED:jwt_token]" in result
        assert "eyJ" not in result


class TestPrivateKeys:
    def setup_method(self) -> None:
        self.p = RedactionPipeline()

    def test_rsa_private_key(self) -> None:
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIB..."
        assert "[REDACTED:private_key]" in self.p.run(text)

    def test_ec_private_key(self) -> None:
        text = "-----BEGIN EC PRIVATE KEY-----\nMHQCAQ..."
        assert "[REDACTED:private_key]" in self.p.run(text)

    def test_generic_private_key(self) -> None:
        text = "-----BEGIN PRIVATE KEY-----\nMIIEvQIB..."
        assert "[REDACTED:private_key]" in self.p.run(text)

    def test_public_key_not_matched(self) -> None:
        text = "-----BEGIN PUBLIC KEY-----\nMIIBIjAN..."
        assert self.p.run(text) == text

    def test_openssh_private_key(self) -> None:
        text = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC..."
        assert "[REDACTED:private_key]" in self.p.run(text)


class TestShannonEntropy:
    def test_low_entropy_string(self) -> None:
        assert shannon_entropy("aaaaaaaaaa") < 1.0

    def test_high_entropy_hex(self) -> None:
        # Random-looking hex string
        assert shannon_entropy("a1b2c3d4e5f6a7b8c9d0") > 3.0

    def test_empty(self) -> None:
        assert shannon_entropy("") == 0.0

    def test_max_entropy_binary(self) -> None:
        # Perfectly balanced binary string
        ent = shannon_entropy("01" * 50)
        assert abs(ent - 1.0) < 0.01


class TestHighEntropyStrings:
    def setup_method(self) -> None:
        self.p = RedactionPipeline()

    def test_high_entropy_base64_detected(self) -> None:
        # Base64-like string with mixed case and digits — high entropy (>4.5)
        secret = "Zk9mXpL2vR8nQw4jY6tU0hC3bA7dE5fG"
        result = self.p.run(f"key={secret}")
        assert "[REDACTED:" in result

    def test_low_entropy_hex_not_detected(self) -> None:
        # Repetitive hex — low entropy
        text = "value=00000000000000000000"
        assert self.p.run(text) == text

    def test_short_hex_not_detected(self) -> None:
        # Below 20-char threshold
        text = "hash=a1b2c3d4e5f6"
        assert self.p.run(text) == text


# ====================================================================
# F2-S2: ScanResult and block action
# ====================================================================


class TestScanResult:
    def test_scan_returns_findings(self) -> None:
        p = RedactionPipeline()
        result = p.scan("key: sk-abc123def456ghi789jkl012")
        assert len(result.findings) > 0
        assert result.action == "block"  # API keys trigger block

    def test_scan_pii_masks(self) -> None:
        p = RedactionPipeline()
        result = p.scan("email: user@example.com")
        assert result.action == "mask"
        assert result.masked_text() == "email: [REDACTED:email]"

    def test_scan_no_findings(self) -> None:
        p = RedactionPipeline()
        result = p.scan("just normal text")
        assert result.action == "pass"
        assert result.findings == []
        assert result.masked_text() == "just normal text"

    def test_scan_block_types(self) -> None:
        p = RedactionPipeline()
        result = p.scan("token: sk-abc123def456ghi789jkl012")
        assert "api_key" in result.blocked_types

    def test_jwt_triggers_block(self) -> None:
        p = RedactionPipeline()
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        result = p.scan(f"auth: {jwt}")
        assert result.action == "block"

    def test_private_key_triggers_block(self) -> None:
        p = RedactionPipeline()
        result = p.scan("-----BEGIN RSA PRIVATE KEY-----")
        assert result.action == "block"


class TestActionOverrides:
    def test_override_api_key_to_mask(self) -> None:
        p = RedactionPipeline(security_action_overrides={"api_key": "mask"})
        result = p.scan("key: sk-abc123def456ghi789jkl012")
        assert result.action == "mask"  # not block

    def test_override_email_to_block(self) -> None:
        p = RedactionPipeline(security_action_overrides={"email": "block"})
        result = p.scan("user@example.com")
        assert result.action == "block"


class TestSecurityScanLevels:
    def test_default_l1_only(self) -> None:
        p = RedactionPipeline()
        assert p._levels == {1}

    def test_explicit_levels(self) -> None:
        p = RedactionPipeline(security_scan_levels=[1, 2])
        assert p._levels == {1, 2}

    def test_l2_graceful_degradation(self) -> None:
        """L2 should not crash even if detect-secrets is not installed."""
        p = RedactionPipeline(security_scan_levels=[1, 2])
        result = p.scan("some text with potential secrets")
        # Should complete without error regardless of detect-secrets availability
        assert result is not None

    def test_l3_graceful_degradation(self) -> None:
        """L3 should not crash even if spacy is not installed."""
        p = RedactionPipeline(security_scan_levels=[1, 3])
        result = p.scan("John Smith went to New York")
        # Should complete without error regardless of spacy availability
        assert result is not None


class TestFindingDataclass:
    def test_finding_fields(self) -> None:
        f = Finding(type="email", value="test@test.com", start=0, end=13, action="mask")
        assert f.type == "email"
        assert f.action == "mask"

    def test_scan_result_masked_text_multiple(self) -> None:
        result = ScanResult(
            text="hello world test",
            findings=[
                Finding(type="a", value="hello", start=0, end=5, action="mask"),
                Finding(type="b", value="test", start=12, end=16, action="mask"),
            ],
        )
        assert result.masked_text() == "[REDACTED:a] world [REDACTED:b]"
