---
title: node --check on a Workflow DSL script (.claude/workflows/*) needs stripping, not raw checking
date: 2026-08-02
category: docs/solutions/best-practices/
module: .claude/workflows/* (governance-sweep, sensei-cycle-wf, and future Workflow scripts)
problem_type: false_syntax_error
component: Workflow DSL script files
severity: low
applies_when:
  - Editing a file under .claude/workflows/ and wanting a fast local syntax check before invoking Workflow
  - node --check reports "Illegal return statement" or "Unexpected token 'export'" on a file that Workflow itself runs fine
tags: [workflow-dsl, node-check, syntax-check, false-positive]
---

## Symptom

Running `node --check <file>.js` directly against a `.claude/workflows/*` Workflow DSL script
throws a syntax error even though the file is valid Workflow-DSL and runs correctly when passed to
the `Workflow` tool.

Two failure shapes, in order:
1. `SyntaxError: Illegal return statement` — the script body has a top-level `return` (Workflow scripts
   are executed as an async function body, not a top-level module).
2. Wrapping the body in a bare `(async () => { ... })()` IIFE fixes #1 but then throws
   `SyntaxError: Unexpected token 'export'` — because the file starts with the required
   `export const meta = {...}` declaration, which is only legal at true top-level (ES module) or
   inside a `<script type="module">`-equivalent context, not inside a wrapped function body.

## Root cause

Workflow DSL files are not plain Node scripts and not ES modules — they're a hybrid the `Workflow`
tool's own runner parses (top-level `export const meta` for metadata, then a top-level `return` is
valid at the end of the script body). Neither `node script.js` nor `node --check script.js` understands
this hybrid grammar, so a naive check always fails on a syntactically-valid file.

## Fix

To sanity-check syntax with `node --check` before invoking `Workflow`, strip the leading
`export const meta = {...}` declaration (comment it out or copy the file without it) AND wrap the
remaining body in an async IIFE:

```bash
# quick local check — not a full semantic validation, just balanced braces / valid JS
sed 's/^export const meta/const meta/' workflow-script.js > /tmp/check.js
node --check <(printf '(async () => {\n%s\n})();' "$(cat /tmp/check.js)")
```

A `SYNTAX OK` (or silent success) from that wrapped+stripped version is the real signal; a bare
`node --check` on the original file will report an error on virtually every valid Workflow script and
should not be trusted as evidence of a real bug.
