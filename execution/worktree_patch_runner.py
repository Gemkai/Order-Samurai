#!/usr/bin/env python3
"""Worktree Patch Runner for Order Samurai.

Executes candidate remediations inside an ephemeral Git worktree, verifies tests and
security contracts before creating any patch, and stages valid patches for review.
Prevents unconstrained "repo-blast" writes to the working tree.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import REPO_ROOT, STATE_DIR


def create_worktree(repo_root: Path, branch_name: str) -> Path:
    """Create an ephemeral worktree in a temporary directory."""
    worktree_dir = Path(tempfile.mkdtemp(prefix=f"samurai_wt_{branch_name.replace('/', '_')}_"))
    worktree_dir.rmdir()
    
    cmd = ["git", "worktree", "add", "-b", branch_name, str(worktree_dir), "HEAD"]
    res = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to create git worktree: {res.stderr.strip()}")
    return worktree_dir


def cleanup_worktree(repo_root: Path, worktree_dir: Path, branch_name: str) -> None:
    """Remove ephemeral worktree and delete its temporary branch."""
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_dir)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        pass
    if worktree_dir.exists():
        shutil.rmtree(worktree_dir, ignore_errors=True)
    try:
        subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        pass


def run_worktree_remediation(
    metric: str,
    remediation_cmd: list[str],
    validate_cmd: list[str] | None = None,
    timeout_s: int = 180,
    repo_root: Path = REPO_ROOT,
) -> dict:
    """Execute a remediation in a clean worktree, test it, and stage patch if clean."""
    t0 = time.time()
    tag = f"{metric.lower()}_{uuid.uuid4().hex[:8]}"
    branch_name = f"remediation/{tag}"
    
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    worktree_dir = None
    
    try:
        worktree_dir = create_worktree(repo_root, branch_name)
        
        # 1. Run remediation command inside worktree
        env = dict(os.environ)
        env["GOVERNANCE_ROOT"] = str(worktree_dir)
        
        subprocess.run(
            remediation_cmd,
            cwd=str(worktree_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        
        # 2. Check for git diff
        diff_res = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=str(worktree_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        patch_text = diff_res.stdout
        
        if not patch_text.strip():
            return {
                "success": False,
                "status": "no_change",
                "metric": metric,
                "detail": "Remediation command produced no file modifications",
                "duration_s": round(time.time() - t0, 2),
            }
            
        # 3. Run validation command if provided
        if validate_cmd:
            val_res = subprocess.run(
                validate_cmd,
                cwd=str(worktree_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
            if val_res.returncode != 0:
                return {
                    "success": False,
                    "status": "validation_failed",
                    "metric": metric,
                    "detail": f"Validation failed with exit {val_res.returncode}: {val_res.stderr[:300]}",
                    "duration_s": round(time.time() - t0, 2),
                }
                
        # 4. Stage patch
        patch_path = STATE_DIR / f"pending_remediation_{metric}_{int(time.time())}.patch"
        patch_path.write_text(patch_text, encoding="utf-8")
        
        return {
            "success": True,
            "status": "patch_staged",
            "metric": metric,
            "patch_file": str(patch_path),
            "lines_changed": len(patch_text.splitlines()),
            "detail": f"Patch successfully staged to {patch_path.name}",
            "duration_s": round(time.time() - t0, 2),
        }
        
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "status": "timeout",
            "metric": metric,
            "detail": f"Remediation timed out after {timeout_s}s",
            "duration_s": round(time.time() - t0, 2),
        }
    except Exception as exc:
        return {
            "success": False,
            "status": "error",
            "metric": metric,
            "detail": str(exc),
            "duration_s": round(time.time() - t0, 2),
        }
    finally:
        if worktree_dir:
            cleanup_worktree(repo_root, worktree_dir, branch_name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run remediation in an isolated worktree")
    parser.add_argument("--metric", required=True, help="Metric being remediated")
    parser.add_argument("--cmd", required=True, help="Remediation command to run (JSON list or string)")
    parser.add_argument("--validate-cmd", default=None, help="Validation command to run after remediation")
    parser.add_argument("--timeout", type=int, default=180, help="Execution timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    
    args = parser.parse_args()
    
    try:
        rem_cmd = json.loads(args.cmd) if args.cmd.startswith("[") else args.cmd.split()
    except Exception:
        rem_cmd = args.cmd.split()
        
    val_cmd = None
    if args.validate_cmd:
        try:
            val_cmd = json.loads(args.validate_cmd) if args.validate_cmd.startswith("[") else args.validate_cmd.split()
        except Exception:
            val_cmd = args.validate_cmd.split()
            
    res = run_worktree_remediation(
        metric=args.metric,
        remediation_cmd=rem_cmd,
        validate_cmd=val_cmd,
        timeout_s=args.timeout,
    )
    
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        status = res.get("status")
        detail = res.get("detail", "")
        print(f"[{status.upper()}] {args.metric}: {detail}")
        if res.get("patch_file"):
            print(f"  Patch: {res['patch_file']}")
            
    return 0 if res.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
