"""Eval for the deterministic skill-consolidator mechanism (bin/skill_consolidator.py).

This IS the eval the LLM /skill-consolidator skill never had: pinned small vectors
(no ChromaDB, no live embedder) map a corpus to the expected near-duplicate
clusters and per-group classification, pinning the safety-critical distinction the
skill exists to protect — clone-family (MERGE candidate) vs language-family and
distinct-function (KEEP). Same corpus + threshold -> identical result (idempotent).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin.skill_consolidator import (  # type: ignore[import-not-found]
    analyze,
    classify,
    cluster,
    cosine,
    has_template_descriptions,
    is_language_family,
)


# ---------------------------------------------------------------------------
# Pinned vector fixtures (cosine is controlled by construction)
# ---------------------------------------------------------------------------

# Three tight vectors around the x-axis: pairwise cosine >= 0.92.
TIGHT_X = [[1.0, 0.0, 0.0], [0.96, 0.28, 0.0], [0.96, 0.0, 0.28]]
# Three tight vectors around the y-axis, near-orthogonal to TIGHT_X.
TIGHT_Y = [[0.0, 1.0, 0.0], [0.28, 0.96, 0.0], [0.0, 0.96, 0.28]]
# A lone vector on the z-axis, far from both clusters.
LONE_Z = [0.0, 0.0, 1.0]

# Shared boilerplate opening (first 6 words identical) -> template signal True.
CLONE_DESCS = [
    "Wrap the provider API via Composio for Stripe payments automation",
    "Wrap the provider API via Composio for Asana task automation",
    "Wrap the provider API via Composio for Slack message automation",
]
# Distinct openings -> template signal False even at high cohesion.
DISTINCT_DESCS = [
    "Crawl entire websites recursively following internal links deeply",
    "Scrape one page into clean structured markdown output",
    "Search the open web returning ranked relevant results",
]


# ---------------------------------------------------------------------------
# cosine
# ---------------------------------------------------------------------------

class CosineTests(unittest.TestCase):

    def test_identical_vectors_score_one(self) -> None:
        self.assertAlmostEqual(cosine([1.0, 2.0], [1.0, 2.0]), 1.0)

    def test_orthogonal_vectors_score_zero(self) -> None:
        self.assertAlmostEqual(cosine([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_zero_vector_scores_zero_without_dividing_by_zero(self) -> None:
        self.assertEqual(cosine([0.0, 0.0], [1.0, 1.0]), 0.0)


# ---------------------------------------------------------------------------
# cluster
# ---------------------------------------------------------------------------

class ClusterTests(unittest.TestCase):

    def test_groups_near_duplicates_above_threshold(self) -> None:
        groups = cluster(TIGHT_X, threshold=0.85)
        self.assertEqual(groups, [[0, 1, 2]])

    def test_separates_two_orthogonal_clusters(self) -> None:
        groups = cluster(TIGHT_X + TIGHT_Y, threshold=0.85)
        self.assertEqual(groups, [[0, 1, 2], [3, 4, 5]])

    def test_excludes_singletons(self) -> None:
        groups = cluster(TIGHT_X + [LONE_Z], threshold=0.85)
        self.assertEqual(groups, [[0, 1, 2]])  # index 3 (lone z) dropped

    def test_higher_threshold_dissolves_a_loose_group(self) -> None:
        # TIGHT_X edges are ~0.96; at 0.97 none hold, so every member is a singleton.
        groups = cluster(TIGHT_X, threshold=0.97)
        self.assertEqual(groups, [])


# ---------------------------------------------------------------------------
# String heuristics
# ---------------------------------------------------------------------------

class TemplateHeuristicTests(unittest.TestCase):

    def test_shared_opening_is_a_template(self) -> None:
        self.assertTrue(has_template_descriptions(CLONE_DESCS))

    def test_distinct_openings_are_not_a_template(self) -> None:
        self.assertFalse(has_template_descriptions(DISTINCT_DESCS))

    def test_strips_name_prefix_before_comparing(self) -> None:
        descs = ["stripe-auto: Run the shared workflow once per call",
                 "asana-auto: Run the shared workflow once per call"]
        self.assertTrue(has_template_descriptions(descs))


class LanguageFamilyHeuristicTests(unittest.TestCase):

    def test_cross_language_suffixes_collapse_to_fewer_stems(self) -> None:
        self.assertTrue(is_language_family(["json-parser-py", "json-parser-java", "json-parser-ts"]))

    def test_distinct_names_are_not_a_language_family(self) -> None:
        self.assertFalse(is_language_family(["stripe-auto", "asana-auto", "slack-auto"]))


# ---------------------------------------------------------------------------
# classify — the safety-critical distinction
# ---------------------------------------------------------------------------

class ClassifyTests(unittest.TestCase):

    def test_template_plus_cohesion_is_a_clone_family(self) -> None:
        names = ["stripe-automation", "asana-automation", "slack-automation"]
        verdict = classify([0, 1, 2], names, CLONE_DESCS, TIGHT_X)
        self.assertEqual(verdict["kind"], "clone_family")

    def test_language_suffix_wins_even_with_template_descriptions(self) -> None:
        names = ["parser-py", "parser-java", "parser-ts"]
        verdict = classify([0, 1, 2], names, CLONE_DESCS, TIGHT_X)
        self.assertEqual(verdict["kind"], "language_family")

    def test_high_similarity_without_template_is_distinct_function(self) -> None:
        names = ["firecrawl-crawl", "firecrawl-scrape", "firecrawl-search"]
        verdict = classify([0, 1, 2], names, DISTINCT_DESCS, TIGHT_X)
        self.assertEqual(verdict["kind"], "distinct_function")

    def test_template_without_cohesion_is_not_a_clone_family(self) -> None:
        # Shared opening but low mutual cosine -> below CLONE_COHESION -> not clone.
        loose = [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]
        verdict = classify([0, 1, 2], ["a-auto", "b-auto", "c-auto"], CLONE_DESCS, loose)
        self.assertNotEqual(verdict["kind"], "clone_family")

    def test_verdict_reports_sorted_member_names(self) -> None:
        names = ["slack-automation", "asana-automation", "stripe-automation"]
        verdict = classify([0, 1, 2], names, CLONE_DESCS, TIGHT_X)
        self.assertEqual(verdict["members"], ["asana-automation", "slack-automation", "stripe-automation"])


# ---------------------------------------------------------------------------
# analyze — end-to-end corpus
# ---------------------------------------------------------------------------

class AnalyzeTests(unittest.TestCase):

    def _corpus(self) -> tuple[list[str], list[str], list[list[float]]]:
        names = ["stripe-automation", "asana-automation", "slack-automation",
                 "firecrawl-crawl", "firecrawl-scrape", "firecrawl-search", "lone-skill"]
        descs = CLONE_DESCS + DISTINCT_DESCS + ["A unique standalone capability with its own job"]
        vectors = TIGHT_X + TIGHT_Y + [LONE_Z]
        return names, descs, vectors

    def test_detects_clone_and_distinct_groups_and_drops_singleton(self) -> None:
        names, descs, vectors = self._corpus()
        report = analyze(names, descs, vectors, threshold=0.85)
        kinds = [g["kind"] for g in report["groups"]]
        self.assertEqual(kinds, ["clone_family", "distinct_function"])

    def test_only_clone_family_is_a_merge_candidate(self) -> None:
        names, descs, vectors = self._corpus()
        report = analyze(names, descs, vectors, threshold=0.85)
        clone = [g for g in report["groups"] if g["kind"] == "clone_family"]
        self.assertEqual(clone[0]["members"],
                         ["asana-automation", "slack-automation", "stripe-automation"])


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class IdempotencyTests(unittest.TestCase):

    def test_same_corpus_yields_identical_analysis(self) -> None:
        names = ["stripe-automation", "asana-automation", "slack-automation"]
        first = analyze(names, CLONE_DESCS, TIGHT_X, threshold=0.85)
        second = analyze(names, CLONE_DESCS, TIGHT_X, threshold=0.85)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
