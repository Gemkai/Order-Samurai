"""Stable execution fields a human decision authorizes; timestamps are not authority."""
import hashlib
import json

FIELDS = ('id', 'command', 'skill', 'pillar', 'source', 'metric_id', 'backlog_id',
          'blast_radius', 'reversible', 'tier_assigned', 'context')


def proposal(item):
    result = {key: item.get(key) for key in FIELDS}
    # Preserve hashes of historical proposals that predate category classification.
    if "decision_category" in item:
        result["decision_category"] = item["decision_category"]
    return result


def proposal_hash(item):
    return hashlib.sha256(json.dumps(proposal(item), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
