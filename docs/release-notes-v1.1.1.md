# AC6 Win/Loss Tracker v1.1.1

This is a distribution-integrity patch release. It supersedes v1.1.0.

**Tracker behaviour is unchanged from v1.1.0.** No runtime, gameplay, detection, result, history, configuration or dependency change is included.

## Why this release exists

The install command published in the v1.1.0 tag carried a bootstrap SHA-256 that had been computed from Windows CRLF working-tree bytes instead of the Git blob bytes that `raw.githubusercontent.com` actually serves. The command therefore stopped safely, before execution, with `bootstrap SHA-256 mismatch`.

A published tag and Release are immutable, so the fix ships as a new immutable version rather than as a correction to v1.1.0. The `v1.1.0` tag and Release are left exactly as published.

## Changes

- Corrected the README install and uninstall commands to the real published bootstrap SHA-256.
- Pointed both commands at the immutable `refs/tags/v1.1.1` bootstrap.
- Version metadata moved to 1.1.1: `VERSION`, installer `SourceTag`, installer and bootstrap User-Agent.
- Added a regression test that derives the expected bootstrap hash from the committed Git blob instead of trusting a literal, and that fails if the README value matches the working-tree bytes instead. This is the defect that blocked v1.1.0.

## Installation

Use the one-liner in the README at the `v1.1.1` tag. Updating from v1.1.0 uses the same command.

Python install strategy is unchanged: Python 3.14 preferred with a 3.13 fallback, packages installed with `pip --user` against `requirements.lock`. Dedicated venv isolation remains a future version.

Supported mode remains **RANK MATCH: SINGLE only**. CUSTOM MATCH and RANK MATCH: TEAM are not supported by this release.
