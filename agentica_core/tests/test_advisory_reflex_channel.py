"""Producer half of the 2026-07-29 sensei-stall regression.

The consumer half lives in api/src/sensei-orchestrator-silence.test.ts. This file
guards the split at its source: a non-auto-remediable metric must be ROUTED to the
advisory channel, never dropped.

History: SENSEI-3/4 correctly stopped these metrics from producing a dashboard card
with a run button that could not move the metric — but it implemented that by
`continue`-ing in `_metric_reflexes`, and `payload["reflexes"]` was also sensei's sole
input. Sensei is an adversarial INVESTIGATION loop that ends in a verdict, not a fix,
so metrics nothing can auto-fix are exactly its highest-value targets. When the flagged
population narrowed to only those, the 6-hourly cycle went to 0 eligible and wrote
nothing for 8 days while exiting 0.
"""
import json

from agentica_core import insights, reflexes

_NOWHERE = reflexes.Path("does-not-exist")


def _pillars(**envs):
    """Empty envelope tree with the given (pillar, group, metric) -> env grafted in."""
    out = {"bow": {}, "sword": {}, "brush": {}, "arts": {}}
    for pk, (group, mk, env) in envs.items():
        out[pk] = {group: {mk: env}}
    return out


def _scores(**flags_by_pillar):
    return {pk: {"flags": flags_by_pillar.get(pk, [])}
            for pk in ("bow", "sword", "brush", "arts")}


def _build(pillars, category_scores):
    return reflexes.build_reflexes(pillars, category_scores, {},
                                   nudges_path=_NOWHERE, state_path=_NOWHERE)


def test_config_premise_still_holds():
    """The two metrics this suite is built on are still declared non-remediable.

    If someone flips either to auto_remediable=True the assertions below would pass
    for the wrong reason, so the premise is asserted rather than assumed.
    """
    for mk in ("Mechanism_Orphans", "Retrieval_Relevance"):
        assert insights.METRIC_CONFIG.get(mk, {}).get("auto_remediable") is False


def test_live_stall_input_produces_advisory_reflexes_not_silence():
    """The exact 2026-07-29 payload condition: every flag is non-remediable.

    Pre-fix this produced ZERO metric reflexes on any channel, which is what starved
    sensei. Post-fix both breaches leave on the advisory channel, tier intact.
    """
    pillars = _pillars(
        bow=("Autonomic", "Mechanism_Orphans",
             {"val": "4", "is_simulated": False, "history": [1.0, 4.0]}),
        arts=("Retrieval", "Retrieval_Relevance",
              {"val": "53.3", "is_simulated": False, "history": []}),
    )
    category_scores = _scores(
        bow=[{"name": "Mechanism_Orphans", "val": "4", "grade": "F"}],
        arts=[{"name": "Retrieval_Relevance", "val": "53.3", "grade": "D"}],
    )

    dispatch, advisory = _build(pillars, category_scores)

    assert [r["id"] for r in dispatch if r["source"] == "metric"] == []
    assert {r["id"]: r["tier"] for r in advisory} == {
        "metric:bow:Mechanism_Orphans": "CRITICAL",
        "metric:arts:Retrieval_Relevance": "HIGH",
    }


def test_advisory_entries_stay_marked_non_remediable():
    """The advisory channel must not become a back door to a run button.

    Every entry still carries auto_remediable=False and kind=advisory, which is what
    the dashboard and ReflexEngine gate on if either ever reads this key.
    """
    pillars = _pillars(bow=("Autonomic", "Mechanism_Orphans",
                            {"val": "4", "is_simulated": False, "history": []}))
    category_scores = _scores(bow=[{"name": "Mechanism_Orphans", "val": "4", "grade": "F"}])

    _dispatch, advisory = _build(pillars, category_scores)

    assert len(advisory) == 1
    assert advisory[0]["auto_remediable"] is False
    assert advisory[0]["kind"] == "advisory"


def test_remediable_metric_stays_on_the_dispatch_channel():
    """Guards the other direction: routing must not drain the dispatch channel."""
    pillars = _pillars(sword=("Vulnerability", "Open_CVEs",
                              {"val": "6", "is_simulated": False, "history": [],
                               "mitigation_command": "/codebase-cleanup-deps-audit"}))
    category_scores = _scores(sword=[{"name": "Open_CVEs", "val": "6", "grade": "F"}])

    dispatch, advisory = _build(pillars, category_scores)

    assert [r["id"] for r in dispatch if r["source"] == "metric"] == ["metric:sword:Open_CVEs"]
    assert advisory == []


def test_no_metric_appears_on_both_channels():
    """One metric, one channel. A duplicate would let a suppressed card back onto the
    dashboard AND double-count the same breach into the scout batch."""
    pillars = _pillars(
        bow=("Autonomic", "Mechanism_Orphans",
             {"val": "4", "is_simulated": False, "history": [1.0, 1.0, 1.0, 1.0, 4.0]}),
        sword=("Vulnerability", "Open_CVEs",
               {"val": "6", "is_simulated": False, "history": []}),
    )
    category_scores = _scores(
        bow=[{"name": "Mechanism_Orphans", "val": "4", "grade": "F"}],
        sword=[{"name": "Open_CVEs", "val": "6", "grade": "F"}],
    )

    dispatch, advisory = _build(pillars, category_scores)

    overlap = {r["id"] for r in dispatch} & {r["id"] for r in advisory}
    assert overlap == set()


def test_wid_payload_schema_declares_the_advisory_channel():
    """The Python->TS seam is explicit, not merely permitted by additionalProperties.

    Both ends validate this same file; an undeclared key would make the contract
    depend on a permissive default rather than a stated agreement.
    """
    schema_path = (reflexes.Path(__file__).parents[2] / "schema" / "wid_payload.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert "advisory_reflexes" in schema["properties"]
    assert schema["properties"]["advisory_reflexes"]["items"] == {"$ref": "#/definitions/reflex"}
    # Optional on purpose — a payload written before this key existed is still valid.
    assert "advisory_reflexes" not in schema["required"]
