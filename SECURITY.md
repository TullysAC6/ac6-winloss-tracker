# Distribution security

Stable installation and updates use the one-line command in `README.txt`.
Downloaded text is never passed to `Invoke-Expression`.

The command downloads `bootstrap.ps1` from the immutable Stable tag and checks
its documented SHA-256 before running it with Windows PowerShell 5.1 `-File`.
The bootstrap then selects GitHub's latest non-draft, non-prerelease semantic
version Release. It requires both GitHub's `sha256:` asset digest and the
matching checksum asset before it executes `install.ps1` or `uninstall.ps1`.
Any HTTP, empty-file, metadata, digest, checksum, or PowerShell syntax failure
is fail-closed and temporary files are removed.

Before publishing a Stable Release, run:

```powershell
./scripts/prepare-release-assets.ps1 -OutputDirectory ./release-assets
```

Upload all four generated files as assets:

- `install.ps1`
- `install.ps1.sha256`
- `uninstall.ps1`
- `uninstall.ps1.sha256`

Confirm that the Release API exposes a `sha256:` digest for each asset before
publishing the one-line command. Enable GitHub Immutable Releases for Stable
releases when that repository setting is available.

Production dependencies are installed from `requirements.lock` using both
`--require-hashes` and `--only-binary=:all:`. See
`docs/dependency-lock.md` for the Windows x64 Python 3.13/3.14 update process.
