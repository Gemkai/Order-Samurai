# Contributing to Order Samurai

Thank you for your interest in contributing to **Order Samurai**! Order Samurai is the local-first governance and security layer for autonomous coding agent fleets (Claude Code, etc.).

## Code of Conduct

We follow the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). Please read and adhere to it in all interactions.

## Security Policy & Vulnerabilities

Security is central to Order Samurai. If you discover a security vulnerability, please refer to our [SECURITY.md](SECURITY.md) for responsible disclosure instructions. Do NOT open public issues for sensitive security vulnerabilities.

## Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/order-samurai/order-samurai.git
   cd order-samurai
   ```

2. **Python Requirements:**
   Python 3.10+ is required. Set up a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r pyproject.toml # or pip install pytest
   ```

3. **Running Tests:**
   All tests must pass before submitting a Pull Request.
   ```bash
   python3 -m pytest tests/ -v
   ```

4. **Running Verification Suite:**
   ```bash
   ./verify.sh
   ```

## Development Principles

- **Local-First & Privacy First**: No customer prompts, code, or private keys leave local execution.
- **Fail-Closed Gate Posture**: Security gates must fail closed loud, never open silently.
- **Honesty Invariant**: Measured metrics must be clearly distinguished from benchmark placeholders (`calibrated: false`).

## Submitting Pull Requests

1. Fork the repo and create your branch from `main`.
2. Ensure all 390+ unit tests pass.
3. Write test coverage for new hooks, reducers, or verifiers.
4. Open a PR with a clear description of the problem and solution.
