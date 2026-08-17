"""Shared result plumbing for the `verify_*` / `score_*` family (M7.2).

Every verifier in `execution/` returns the same row shape — status, a label, a
detail string — and every one of them counts those rows the same way to decide
its exit code. That plumbing was copy-pasted 26 times: 26 `_make_result`, 26
`summarize`. Measured before extracting: all 26 `summarize` bodies are
behaviourally identical (two formatting variants), and 25 of 26 `_make_result`
bodies are too.

What this module deliberately does NOT do: merge the checks themselves. The
verifiers hold genuinely different policy semantics — what counts as a FAIL for
path authority is not what counts for pack integrity — and pushing those behind
one abstraction would trade a little repetition for a lot of coupling. This is
the plumbing only.

The one real variation is the label key: `verify_claude_root_hygiene` emits
`name` where the other 25 emit `label`, and doctor's family registry reads it
accordingly. `make_result` takes the key rather than pretending the difference
does not exist.
"""
from __future__ import annotations

#: The three statuses a verifier row may carry, in report order.
STATUSES = ("OK", "WARN", "FAIL")


def make_result(status: str, label: str, detail: str, *,
                label_key: str = "label") -> dict[str, str]:
    """One verifier result row.

    `label_key` exists for the single verifier that publishes `name`; changing
    that spelling would break doctor's rendering and its consumers' assertions,
    so it is a parameter rather than something to normalise away.
    """
    return {"status": status, label_key: label, "detail": detail}


def summarize(results: list[dict[str, str]]) -> tuple[dict[str, int], int]:
    """(counts, exit_code). Exit is 1 iff any row FAILed.

    Unknown statuses are counted under their own key rather than dropped: a
    typo'd status silently vanishing from the totals is how a check stops
    reporting without anyone noticing.
    """
    counts = {status: 0 for status in STATUSES}
    for result in results:
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1
    return counts, 1 if counts["FAIL"] else 0


def render(results: list[dict[str, str]], *, label_key: str = "label") -> list[str]:
    """The `[STATUS] label: detail` lines every verifier's main() prints."""
    return [f"[{r['status']}] {r[label_key]}: {r['detail']}" for r in results]
