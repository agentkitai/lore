# Publishing Guide

Instructions for publishing Lore SDK packages to PyPI and npm.

## Python SDK (PyPI)

### Prerequisites

```bash
pip install build twine
```

### Build

```bash
# Clean previous builds
rm -rf dist/

# Build sdist and wheel
python3 -m build
```

### Verify

```bash
# Check the dist output
ls -la dist/

# Verify metadata
twine check dist/*

# Optional: test install from the wheel
pip install dist/lore_sdk-*.whl --force-reinstall
python3 -c "from lore import Lore; print('OK')"
```

### Publish to TestPyPI (dry run)

```bash
twine upload --repository testpypi dist/*

# Test install from TestPyPI
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ lore-sdk
```

### Publish to PyPI

```bash
twine upload dist/*
```

You will be prompted for credentials. Use an API token:
- Username: `__token__`
- Password: `pypi-AgEI...` (your PyPI API token)

Or configure `~/.pypirc`:

```ini
[pypi]
username = __token__
password = pypi-AgEIpY...
```

### Verify published package

```bash
pip install lore-sdk --upgrade
python3 -c "from lore import Lore; print(Lore)"
```

---

## TypeScript SDK (npm)

### Prerequisites

```bash
cd ts/
npm install
```

### Build

```bash
npm run build
```

### Verify

```bash
# Check the dist output
ls -la dist/

# Dry run to see what would be published
npm pack --dry-run
```

### Publish to npm

```bash
# Login (first time)
npm login

# Publish
npm publish

# Or publish with public access (for scoped packages)
npm publish --access public
```

### Verify published package

```bash
npm info lore-sdk
```

---

## Release Checklist

1. [ ] All tests passing (`pytest` / `npm test`)
2. [ ] Version bumped in `pyproject.toml` and `ts/package.json`
3. [ ] Version bumped in `src/lore/__init__.py`
4. [ ] CHANGELOG.md updated
5. [ ] Git tag created (`git tag v0.x.x`)
6. [ ] Python package builds cleanly (`python3 -m build`)
7. [ ] TypeScript package builds cleanly (`cd ts && npm run build`)
8. [ ] Published to PyPI (`twine upload dist/*`)
9. [ ] Published to npm (`cd ts && npm publish`)
10. [ ] Git tag pushed (`git push origin v0.x.x`)
11. [ ] GitHub release created with changelog notes
