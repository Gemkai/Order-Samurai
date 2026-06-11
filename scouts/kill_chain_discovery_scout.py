#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kill_chain_discovery_scout.py — Weekly discovery scout
Clusters unmatched events, proposes new chains via gemma-4-e4b.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve()
REPO_ROOT = _HERE.parents[1]
UNMATCHED_LOG = REPO_ROOT / "state" / "kill_chain_unmatched.jsonl"
PROPOSED_CHAINS_FILE = REPO_ROOT / "state" / "proposed_kill_chains.json"
EVENTS_LOG = REPO_ROOT / "state" / "kill_chain_events.jsonl"
TAXONOMY_FILE = REPO_ROOT / "state" / "kill_chain_taxonomy.json"
AUTONOMIC_EVENTS_LOG = REPO_ROOT / "state" / "autonomic_events.jsonl"

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
MODEL = "google/gemma-4-e4b"
TIMEOUT_S = 30

def tokenize(s: str) -> set[str]:
    return set(re.findall(r"\w+", s.lower()))

def token_similarity(s1: str, s2: str) -> float:
    t1 = tokenize(s1)
    t2 = tokenize(s2)
    if not t1 or not t2:
        return 0.0
    return len(t1.intersection(t2)) / len(t1.union(t2))

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        records.append(obj)
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return records

def atomic_json_write(path: Path, data: dict):
    tmp = str(path) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        # Windows-safe replace
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass
        Path(tmp).replace(path)
    except Exception:
        if Path(tmp).exists():
            try:
                Path(tmp).unlink()
            except Exception:
                pass

def atomic_jsonl_append(file_path: Path, entry: dict):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip():
                        lines.append(line)
        except Exception:
            pass
    lines.append(json.dumps(entry) + "\n")
    tmp_path = file_path.with_suffix(".jsonl.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                pass
        tmp_path.replace(file_path)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

def propose_chain_via_lm(taxonomy: dict, cluster_events: list[dict]) -> dict | None:
    examples = "\n".join(f"- Type: {e.get('event_type')}, Detail: {e.get('detail')}" for e in cluster_events[:5])
    system_prompt = (
        "You are a cybersecurity expert analyzing unmatched security alert clusters. "
        "Your goal is to propose a new MITRE ATT&CK security kill chain for the observed cluster. "
        "Respond ONLY with a JSON object. No other text.\n"
        "Expected JSON format:\n"
        "{\n"
        '  "name": "<descriptive chain name>",\n'
        '  "phases": ["<Phase1>", "<Phase2>"],\n'
        '  "detection_points": ["<DetectionPoint1>"],\n'
        '  "cia_targets": ["Confidentiality", "Integrity"],\n'
        '  "mitre_techniques": ["Txxxx"],\n'
        '  "confidence": <float between 0.0 and 1.0>\n'
        "}"
    )
    user_prompt = (
        f"Existing Taxonomy:\n{json.dumps(taxonomy.get('chains', [])[:5], indent=2)}\n\n"
        f"Unmatched alert cluster examples:\n{examples}\n\n"
        "Propose a new chain definition that encapsulates this pattern."
    )
    
    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 1000
    }).encode("utf-8")
    
    req = urllib.request.Request(
        LM_STUDIO_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                lines = content.splitlines()
                content = "\n".join(line for line in lines if not line.startswith("```")).strip()
            start = content.find("{")
            end = content.rfind("}") + 1
            if start == -1 or end == 0:
                return None
            return json.loads(content[start:end])
    except Exception:
        return None

def cluster_unmatched(events: list[dict]) -> list[list[dict]]:
    # Simple similarity clustering
    clusters: list[list[dict]] = []
    for e in events:
        detail = e.get("detail", "")
        event_type = e.get("event_type", "")
        
        placed = False
        for c in clusters:
            # Check similarity with first element of cluster
            ref = c[0]
            if ref.get("event_type") == event_type:
                sim = token_similarity(ref.get("detail", ""), detail)
                if sim >= 0.5:
                    c.append(e)
                    placed = True
                    break
        if not placed:
            clusters.append([e])
    return clusters

def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print proposals without writing")
    args = parser.parse_args()

    print(f"Running kill chain discovery scout... (dry-run: {args.dry_run})")
    
    # 1. Read unmatched events
    events = read_jsonl(UNMATCHED_LOG)
    
    # Filter to events in last 30 days
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    recent_events = []
    for e in events:
        ts = e.get("ts")
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= cutoff:
                    recent_events.append(e)
            except Exception:
                pass
                
    print(f"Found {len(events)} total unmatched events, {len(recent_events)} within the last 30 days.")
    
    if len(recent_events) < 5:
        print("Insufficient signal: fewer than 5 recent unmatched events.")
        return 0

    # 2. Cluster
    clusters = cluster_unmatched(recent_events)
    significant_clusters = [c for c in clusters if len(c) >= 3]
    print(f"Clustered into {len(clusters)} total groups, {len(significant_clusters)} have >=3 events.")
    
    if not significant_clusters:
        print("No significant clusters (>=3 events) found.")
        return 0

    # 3. Load taxonomy & existing proposals
    taxonomy = {}
    if TAXONOMY_FILE.exists():
        try:
            taxonomy = json.loads(TAXONOMY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
            
    proposed_data = {"proposals": [], "last_run": None, "approved_count": 0}
    if PROPOSED_CHAINS_FILE.exists():
        try:
            proposed_data = json.loads(PROPOSED_CHAINS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 4. Run LLM completions
    proposals_added = 0
    for idx, cluster in enumerate(significant_clusters, 1):
        print(f"Analyzing Cluster {idx} ({len(cluster)} events)...")
        prop = propose_chain_via_lm(taxonomy, cluster)
        if prop:
            confidence = prop.get("confidence", 0.0)
            print(f"  Proposed: {prop.get('name')} (confidence: {confidence})")
            if confidence >= 0.7:
                proposal_entry = {
                    "id": len(proposed_data.get("proposals", [])) + 1,
                    "name": prop.get("name"),
                    "phases": prop.get("phases", []),
                    "detection_points": prop.get("detection_points", []),
                    "cia_targets": prop.get("cia_targets", []),
                    "mitre_techniques": prop.get("mitre_techniques", []),
                    "status": "proposed",
                    "confidence": confidence,
                    "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "events_count": len(cluster)
                }
                if args.dry_run:
                    print("  [DRY RUN] Would write proposal:")
                    print(json.dumps(proposal_entry, indent=2))
                else:
                    proposed_data["proposals"].append(proposal_entry)
                    proposals_added += 1
            else:
                print(f"  [SKIP] Confidence {confidence} below threshold 0.7.")
        else:
            print("  [WARN] LM Studio offline or returned invalid response.")

    # 5. Write state files
    if not args.dry_run and proposals_added > 0:
        proposed_data["last_run"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        atomic_json_write(PROPOSED_CHAINS_FILE, proposed_data)
        
        # Emit autonomic event
        auto_entry = {
            "event": "kill_chain_proposal",
            "pillar": "sword",
            "detail": f"Proposed {proposals_added} new kill chains for review.",
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        }
        atomic_jsonl_append(AUTONOMIC_EVENTS_LOG, auto_entry)
        print(f"Successfully wrote {proposals_added} new proposal(s) and emitted autonomic event.")

    return 0

if __name__ == "__main__":
    sys.exit(run())
