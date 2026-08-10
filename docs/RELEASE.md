# Release Runbook — Order Samurai

Canonical version source: `pyproject.toml` (`version = "X.Y.Z"`). Every release keeps
`execution/cli.py __version__`, `bin/samurai SAMURAI_VERSION`, `package.json`,
`api/package.json`, `dashboard-ui/package.json`, and `CHANGELOG.md` in lockstep with it.
SemVer: MAJOR = breaking CLI/config change, MINOR = new feature, PATCH = fix.

## 1. Version bump
1. Update `pyproject.toml`, then the mirrors listed above.
2. Add a `CHANGELOG.md` entry with the date.
3. `grep -rn "<old version>"` across those files to catch stragglers.

## 2. Test gates (all must pass before packaging)
```bash
python3 -m pytest tests/ agentica_core/tests/ -q          # full pack suite
cd dashboard-ui && npm run build && cd ..                  # demo/dashboard bundle
bash -n bin/*.sh                                           # shell syntax
```

## 3. Build artifacts
```bash
bash bin/build_core_zip.sh    # -> dist/order-samurai-core.zip + .sha256
```
The script bases the file list on `git ls-files` and excludes internal docs
(`docs/productization/**`, `docs/INTERNAL_STRATEGY_MONETIZATION.md`, this file's
internal siblings), `__pycache__`/`*.pyc`, `*.ps1`, and `.env*`.
Gate: `unzip -l dist/order-samurai-core.zip | grep -icE 'productization|__pycache__|\.pyc|\.ps1|INTERNAL_STRATEGY'` must print `0`.

## 4. Ship to the site (order-samurai-landing repo)
1. Copy `dist/order-samurai-core.zip` to the landing repo root AND `demo/` (both are served).
2. Regenerate the sidecar **in the landing repo** (the installer verifies against it):
   `shasum -a 256 order-samurai-core.zip > order-samurai-core.zip.sha256`
3. If the dashboard changed: copy `dashboard-ui/dist/index.html` + `dist/assets/index-*.{js,css}`
   into `demo/`, `git rm` the superseded hashed assets, keep `demo/wid_payload.json`
   and `demo/validate_payload.py` (never overwrite the payload with the dist copy).
4. End-to-end installer check against the local tree:
   ```bash
   python3 -m http.server 8931 &   # from the landing repo root
   OS_BASE_URL=http://localhost:8931 HOME=$(mktemp -d) bash install.sh   # must exit 0
   ```
   Also verify the tamper path: corrupt a served copy of the zip → installer must abort non-zero.
5. `python3 demo/validate_payload.py demo/wid_payload.json` must pass.
6. Commit. **Human pushes** (push = deploy via Vercel).

## 5. Tag + GitHub release (human-run; never automated blind)
```bash
git tag -a vX.Y.Z -m "Order Samurai vX.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z dist/order-samurai-core.zip dist/order-samurai-core.zip.sha256 \
  --title "Order Samurai vX.Y.Z" --notes-file <notes>
```
Inspect the draft before publishing. Signing/attestation (Sigstore or Minisign) is not
yet set up — when an identity exists, sign the zip and attach the signature here.

## 6. Rollback
1. Landing repo: `git revert` the release commit (restores previous zip + matching sidecar
   + previous bundle) and push — Vercel redeploys the prior state.
2. Installer-side: users' previous install is preserved at `~/.samurai/core.bak-<timestamp>`
   by `install.sh`; restoring it is `rm -r ~/.samurai/core && mv ~/.samurai/core.bak-<ts> ~/.samurai/core`.
3. GitHub: `gh release delete vX.Y.Z` only if published minutes ago and broken; otherwise
   publish a patch release — never rewrite a tag others may have fetched.
