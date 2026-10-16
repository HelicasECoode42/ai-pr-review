"""Claims in a PR description, checked against the diff."""

from __future__ import annotations

from src.models import ChangedFile
from src.reviewer.claim_check import check_claims, extract_claims, unimplemented

JADX_BODY = """## Summary

- Use a shared single-thread executor to serialise pull and push operations
- Replace the four direct thread spawns with executor submits
"""

JADX_DIFF = """+    thread {
+        repository.pull()
+    }
+    thread {
+        repository.push()
+    }
"""

REAL_FIX_BODY = """Allowlist regex flags to the gimsuy characters only in validatePattern()

Reject patterns longer than 1000 characters before they reach the engine.
"""

REAL_FIX_DIFF = """+        if pattern.length > 1000
+            reject(pattern)
+        allowlist = "gimsuy"
+"""


def files(patch: str) -> list[ChangedFile]:
    return [ChangedFile(filename="src/engine.py", patch=patch)]


def test_a_claim_that_the_diff_does_not_implement_is_reported():
    # The description promises a shared executor; the diff spawns bare threads.
    reported = unimplemented(JADX_BODY, files(JADX_DIFF))
    assert reported, "an unimplemented mechanism must be reported"
    assert "executor" in " ".join(reported[0].terms)


def test_a_claim_the_diff_does_implement_is_not_reported():
    assert unimplemented(REAL_FIX_BODY, files(REAL_FIX_DIFF)) == []


def test_headings_links_and_prose_are_not_claims():
    body = "## Summary\n\nFixes #12\n\ncc @someone\n"
    assert extract_claims(body) == []


def test_an_empty_description_yields_nothing():
    assert extract_claims(None) == [] and check_claims("", []) == []


def test_every_claim_reports_the_terms_it_looked_for():
    claims = check_claims(REAL_FIX_BODY, files(REAL_FIX_DIFF))
    assert claims
    assert all(claim.terms for claim in claims)
    assert any(claim.supported for claim in claims)


def test_a_replacement_claim_is_contradicted_when_the_old_mechanism_is_still_added():
    """The real jadx gold finding: "replaces kotlin.concurrent.thread" while the
    diff adds kotlin.concurrent.thread calls. Presence of the old name in the added
    lines is evidence against the claim, not for it."""
    body = ("Shared single-thread executor** - replaces ad-hoc "
            "`kotlin.concurrent.thread` calls\n")
    diff = "+    kotlin.concurrent.thread { repository.pull() }\n"
    claims = check_claims(body, files(diff))
    assert len(claims) == 1
    assert claims[0].status == "contradicted", claims[0].evidence
    assert claims[0].replaced_terms
