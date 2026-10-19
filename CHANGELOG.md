# Changelog

## 0.1.1

### Fixed
- `pyproject.toml` named a build backend that does not exist, so `pip install .` failed.
  It now uses `setuptools.build_meta`.
- `README.md` was a two-line stub; the full README had been committed as `README copy.md`.
- API tests failed because `ASGITransport` does not run lifespan events, leaving the
  collector unset. Tests now start the app lifespan.
- Added the CI workflow that the README badge already pointed to.

### Added
- `remaining_slo_headroom_seconds` — how much full-user, peak-traffic downtime is left
  before the target is missed.

## 0.1.0
- Initial release.
