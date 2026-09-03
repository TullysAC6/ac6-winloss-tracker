# Dependency lock maintenance

`requirements.txt` records the direct dependencies. `requirements.lock` is the
production installer input and contains every direct and transitive dependency,
exact versions, and SHA-256 hashes for the supported Windows x64 runtimes.

To update it, use a clean temporary directory and download wheels for both
supported runtimes:

```powershell
py -3.13 -m pip download --only-binary=:all: --platform win_amd64 --implementation cp --python-version 3.13 --dest lock-313 -r requirements.txt
py -3.14 -m pip download --only-binary=:all: --platform win_amd64 --implementation cp --python-version 3.14 --dest lock-314 -r requirements.txt
Get-FileHash lock-313\* -Algorithm SHA256
Get-FileHash lock-314\* -Algorithm SHA256
```

Pin every resolved package in `requirements.lock`. Include hashes for both ABI
specific wheels when they differ. Then verify in clean Windows Python 3.13 and
3.14 environments with:

```powershell
python -m pip install --only-binary=:all: --require-hashes -r requirements.lock
python -m pip check
python -m pip_audit -r requirements.lock
```

Do not generate the final lock from macOS-only wheels.
