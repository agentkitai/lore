"""Exhaustive tests for RuleBasedClassifier — every pattern category, fallback defaults, edge cases."""

from __future__ import annotations

import pytest

from lore.classify.rules import _DEFAULT_CONFIDENCE, _MATCHED_CONFIDENCE, RuleBasedClassifier
from lore.classify.taxonomies import DOMAIN_LABELS, EMOTION_LABELS, INTENT_LABELS


@pytest.fixture
def clf():
    return RuleBasedClassifier()


# ── Intent tests ────────────────────────────────────────────────────


class TestIntentClassification:
    def test_question_mark(self, clf):
        result = clf.classify("How do I deploy to staging?")
        assert result.intent == "question"
        assert result.confidence["intent"] == _MATCHED_CONFIDENCE

    def test_question_who(self, clf):
        result = clf.classify("who owns this service")
        assert result.intent == "question"

    def test_question_what(self, clf):
        result = clf.classify("what is the deployment process")
        assert result.intent == "question"

    def test_question_why(self, clf):
        result = clf.classify("why does this fail")
        assert result.intent == "question"

    def test_question_can(self, clf):
        result = clf.classify("can we use postgres instead")
        assert result.intent == "question"

    def test_question_should(self, clf):
        result = clf.classify("should we migrate to the new api")
        assert result.intent == "question"

    def test_instruction_always(self, clf):
        result = clf.classify("Always use bun instead of npm")
        assert result.intent == "instruction"
        assert result.confidence["intent"] == _MATCHED_CONFIDENCE

    def test_instruction_never(self, clf):
        result = clf.classify("Never commit directly to main")
        assert result.intent == "instruction"

    def test_instruction_run(self, clf):
        result = clf.classify("Run the tests before merging")
        assert result.intent == "instruction"

    def test_instruction_use(self, clf):
        result = clf.classify("Use environment variables for config")
        assert result.intent == "instruction"

    def test_instruction_ensure(self, clf):
        result = clf.classify("Ensure all tests pass before deploy")
        assert result.intent == "instruction"

    def test_instruction_dont(self, clf):
        result = clf.classify("Don't push to main without review")
        assert result.intent == "instruction"

    def test_preference_prefer(self, clf):
        result = clf.classify("I prefer vim over emacs")
        assert result.intent == "preference"
        assert result.confidence["intent"] == _MATCHED_CONFIDENCE

    def test_preference_always_use(self, clf):
        result = clf.classify("I always use dark mode for coding")
        assert result.intent == "preference"

    def test_preference_i_like(self, clf):
        result = clf.classify("I like using typescript for everything")
        assert result.intent == "preference"

    def test_decision_decided(self, clf):
        result = clf.classify("We decided to use Postgres over MySQL")
        assert result.intent == "decision"
        assert result.confidence["intent"] == _MATCHED_CONFIDENCE

    def test_decision_settled_on(self, clf):
        result = clf.classify("We settled on the microservice approach")
        assert result.intent == "decision"

    def test_decision_going_with(self, clf):
        result = clf.classify("Going with React for the frontend")
        assert result.intent == "decision"

    def test_observation_noticed(self, clf):
        result = clf.classify("I noticed the build is slower today")
        assert result.intent == "observation"
        assert result.confidence["intent"] == _MATCHED_CONFIDENCE

    def test_observation_seems(self, clf):
        result = clf.classify("It seems the API latency increased")
        assert result.intent == "observation"

    def test_observation_today(self, clf):
        result = clf.classify("The deploy took 12 minutes today")
        assert result.intent == "observation"

    def test_statement_fallback(self, clf):
        result = clf.classify("The build is broken")
        assert result.intent == "statement"
        assert result.confidence["intent"] == _DEFAULT_CONFIDENCE


# ── Domain tests ────────────────────────────────────────────────────


class TestDomainClassification:
    def test_technical_code(self, clf):
        result = clf.classify("The code has a bug in the parser")
        assert result.domain == "technical"
        assert result.confidence["domain"] == _MATCHED_CONFIDENCE

    def test_technical_deploy(self, clf):
        result = clf.classify("Deploy the service to production")
        assert result.domain == "technical"

    def test_technical_docker(self, clf):
        result = clf.classify("The docker container keeps crashing")
        assert result.domain == "technical"

    def test_technical_npm(self, clf):
        result = clf.classify("Install the package with npm")
        assert result.domain == "technical"

    def test_technical_bun(self, clf):
        result = clf.classify("Use bun for faster installs")
        assert result.domain == "technical"

    def test_business_revenue(self, clf):
        result = clf.classify("Revenue grew 20% this quarter")
        assert result.domain == "business"
        assert result.confidence["domain"] == _MATCHED_CONFIDENCE

    def test_business_stakeholder(self, clf):
        result = clf.classify("The stakeholder wants a demo")
        assert result.domain == "business"

    def test_business_okr(self, clf):
        result = clf.classify("Our OKR targets are ambitious")
        assert result.domain == "business"

    def test_creative_design(self, clf):
        result = clf.classify("The design needs more contrast")
        assert result.domain == "creative"
        assert result.confidence["domain"] == _MATCHED_CONFIDENCE

    def test_creative_ui(self, clf):
        result = clf.classify("Update the UI components")
        assert result.domain == "creative"

    def test_creative_wireframe(self, clf):
        result = clf.classify("Create a wireframe for the dashboard")
        assert result.domain == "creative"

    def test_administrative_meeting(self, clf):
        result = clf.classify("Schedule the team meeting for Friday")
        assert result.domain == "administrative"
        assert result.confidence["domain"] == _MATCHED_CONFIDENCE

    def test_administrative_deadline(self, clf):
        result = clf.classify("The deadline is next week")
        assert result.domain == "administrative"

    def test_administrative_sprint(self, clf):
        result = clf.classify("Sprint planning is tomorrow")
        assert result.domain == "administrative"

    def test_personal_fallback(self, clf):
        result = clf.classify("I went for a walk this morning")
        assert result.domain == "personal"
        assert result.confidence["domain"] == _DEFAULT_CONFIDENCE


# ── Emotion tests ───────────────────────────────────────────────────


class TestEmotionClassification:
    def test_frustrated_broken(self, clf):
        result = clf.classify("This is broken again")
        assert result.emotion == "frustrated"
        assert result.confidence["emotion"] == _MATCHED_CONFIDENCE

    def test_frustrated_keeps_breaking(self, clf):
        result = clf.classify("This keeps breaking every time")
        assert result.emotion == "frustrated"

    def test_frustrated_hate(self, clf):
        result = clf.classify("I hate this flaky test")
        assert result.emotion == "frustrated"

    def test_frustrated_annoying(self, clf):
        result = clf.classify("This is really annoying behavior")
        assert result.emotion == "frustrated"

    def test_excited_amazing(self, clf):
        result = clf.classify("This new feature is amazing")
        assert result.emotion == "excited"
        assert result.confidence["emotion"] == _MATCHED_CONFIDENCE

    def test_excited_finally(self, clf):
        result = clf.classify("It finally works!")
        assert result.emotion == "excited"

    def test_excited_awesome(self, clf):
        result = clf.classify("That's awesome work")
        assert result.emotion == "excited"

    def test_curious_wonder(self, clf):
        result = clf.classify("I wonder if we could use a different approach")
        assert result.emotion == "curious"
        assert result.confidence["emotion"] == _MATCHED_CONFIDENCE

    def test_curious_interesting(self, clf):
        result = clf.classify("That's an interesting pattern")
        assert result.emotion == "curious"

    def test_curious_what_if(self, clf):
        result = clf.classify("What if we tried a different algorithm")
        assert result.emotion == "curious"

    def test_confident_definitely(self, clf):
        result = clf.classify("This is definitely the right approach")
        assert result.emotion == "confident"
        assert result.confidence["emotion"] == _MATCHED_CONFIDENCE

    def test_confident_absolutely(self, clf):
        result = clf.classify("I'm absolutely sure about this")
        assert result.emotion == "confident"

    def test_confident_clearly(self, clf):
        result = clf.classify("This is clearly the best option")
        assert result.emotion == "confident"

    def test_uncertain_maybe(self, clf):
        result = clf.classify("Maybe we should reconsider")
        assert result.emotion == "uncertain"
        assert result.confidence["emotion"] == _MATCHED_CONFIDENCE

    def test_uncertain_maybe_unsure(self, clf):
        result = clf.classify("Maybe this approach is wrong, I'm unsure")
        assert result.emotion == "uncertain"

    def test_uncertain_probably(self, clf):
        result = clf.classify("This will probably need refactoring")
        assert result.emotion == "uncertain"

    def test_neutral_fallback(self, clf):
        result = clf.classify("The function returns a list")
        assert result.emotion == "neutral"
        assert result.confidence["emotion"] == _DEFAULT_CONFIDENCE


# ── Edge cases ──────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_string(self, clf):
        result = clf.classify("")
        assert result.intent == "statement"
        assert result.domain == "personal"
        assert result.emotion == "neutral"

    def test_whitespace_only(self, clf):
        result = clf.classify("   \n\t  ")
        assert result.intent == "statement"
        assert result.domain == "personal"
        assert result.emotion == "neutral"

    def test_unicode_text(self, clf):
        result = clf.classify("这个代码有bug吗?")
        assert result.intent == "question"  # ASCII "?" at end
        assert result.intent in INTENT_LABELS

    def test_very_long_text(self, clf):
        long_text = "The deploy " * 1000
        result = clf.classify(long_text)
        assert result.domain == "technical"

    def test_all_axes_have_valid_labels(self, clf):
        result = clf.classify("anything")
        assert result.intent in INTENT_LABELS
        assert result.domain in DOMAIN_LABELS
        assert result.emotion in EMOTION_LABELS

    def test_all_confidence_values_are_floats(self, clf):
        result = clf.classify("test text")
        for v in result.confidence.values():
            assert isinstance(v, float)
            assert 0.0 <= v <= 1.0

    def test_multiple_axes_match(self, clf):
        result = clf.classify("How do I deploy this broken code?")
        assert result.intent == "question"
        assert result.domain == "technical"
        assert result.emotion == "frustrated"

    def test_never_returns_none(self, clf):
        for text in ["", "x", "hello world", "?", "!!!"]:
            result = clf.classify(text)
            assert result is not None
            assert result.intent is not None
            assert result.domain is not None
            assert result.emotion is not None

    def test_per_axis_methods(self, clf):
        assert clf._classify_intent("How?") == "question"
        assert clf._classify_domain("deploy the code") == "technical"
        assert clf._classify_emotion("this is amazing") == "excited"
