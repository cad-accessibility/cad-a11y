# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Upgraded runtime from Python 3.9 (EOL) to Python 3.12 in `environment.yml`
- Applied pending Dependabot dependency bumps now unblocked by the Python upgrade:
  `flask-cors >=5.0.1`, `numpy >=2.5.0`, `requests >=2.34.2`, `bleak >=3.0.2`,
  `ruff >=0.14.14`, `mamba-org/setup-micromamba v3`, `actions/github-script v9`

### Fixed
- Dockerfile pip install no longer silently masks failures: `|| true` now only
  covers optional packages (polyscope), so a broken primary install
  causes the build to fail visibly instead of starting a container without Flask

### Added
- OSS contribution infrastructure: branch protection, PR templates, issue templates, CI pipeline
- GitHub to GitLab deployment mirror via GitHub Actions
- Deployment documentation (`docs/DEPLOYMENT.md`)
- Dependabot for automated dependency updates
- Stale issue and PR automation
- Conventional Commits enforcement on PR titles
- Integration test scaffolding using the Flask test client
- ML-based slice importance scoring: `app/slice_scorer.py` extracts 10 geometric
  features per cross-section (area change, topology, convexity, centroid drift, etc.)
  and scores slices using a trained RandomForest model
- CLI scoring tool: `app/score_single_model.py` scores and visualizes the top-k
  most structurally significant slices for any STL/OBJ file, using KMeans clustering
  to ensure diverse slice selection across distinct structural regions
- Pretrained slice importance model: `app/slice_scorer_abc_only_v5.pkl` trained on
  383 ABC mechanical CAD models (1.8MB)
