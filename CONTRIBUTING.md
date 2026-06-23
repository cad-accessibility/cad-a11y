# Contributing

Thanks for your interest in improving CAD A11y.

## Before you start

- Open an issue for large changes so we can discuss options and scope before you invest time in a PR.
- Keep pull requests focused and small when possible.
- Always consider the accessibility impact of new features — this project serves BLV (blind and low-vision) users.

## Development setup

1. Install Conda and create the environment:
   ```bash
   conda env create -f environment.yml
   conda activate cad-a11y
   ```
2. Run the app locally:
   ```bash
   python app.py
   ```

## Branching

Create a short-lived branch from `master` named with a Conventional Commit prefix:

```
feat/your-feature-name
fix/the-bug-you-are-fixing
docs/what-you-are-documenting
a11y/what-you-are-improving
```

Open a pull request targeting `master` when your change is ready for review.

## PR title format (Conventional Commits)

The PR title becomes the commit message when squash-merged. Use this format:

```
<type>: short description in present tense
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `build`, `perf`, `a11y`

Examples drawn from this project:

```
feat: add WitMotion IMU support for physical model rotation control
fix: prevent stale render responses from overwriting newer state
a11y: reduce redundant screen reader announcements during pan operations
fix: correct depth-slice announcements for concave geometries
feat: add orthographic projection toggle via keyboard shortcut
fix: resolve upload session leak when browser tab closes unexpectedly
a11y: improve Monarch braille display payload timing under fast gestures
refactor: isolate viewer render state per session to prevent cross-tab bleed
test: add Flask integration tests for the STL conversion endpoint
docs: document Trinkey slider calibration procedure for new hardware setups
```

Individual commit messages within your branch do not need to follow this format — only the PR title matters,
because we squash-merge.

## Pull request guidelines

- Link every PR to an issue using `Closes #NNN` in the PR description — GitHub will close the issue automatically on merge.
- Use GitHub's **Draft PR** feature if your change is not yet ready for review.
- Add or update tests when behavior changes.
- Update documentation for user-facing changes.
- Describe the accessibility impact, even if it is "No accessibility impact."
- Do not include generated artifacts, large binaries, or secrets.

## Images and alt text in issues and PRs

Every screenshot or image you add to an issue or pull request description must include descriptive alt text.
Write what the image actually shows, not just "screenshot":

```markdown
![The slice depth slider at position 40% with the 3D model cross-section visible in the viewer panel](url)
```

Not:

```markdown
![screenshot](url)
```

This matters for BLV contributors and maintainers who use screen readers to review issues and PRs.

## Contributing via the GitHub CLI (accessible workflow)

The `gh` CLI is fully usable with screen readers and is often more predictable than the GitHub web interface
when using assistive technology. Enable screen reader mode first:

```bash
gh config set accessibility true
```

Common commands:

```bash
# Browse open issues
gh issue list

# View a specific issue
gh issue view 42

# Fork and clone the repository
gh repo fork cad-accessibility/cad-a11y --clone

# Create a pull request from your current branch
gh pr create --fill

# Check CI status on your PR
gh pr status

# View PR review comments
gh pr view --comments
```

The GitHub CLI documentation is at https://cli.github.com/manual/

## Code style

- Follow existing style in nearby files.
- Prefer readable, well-named functions.
- Indent with 2 spaces.
- Add comments only where logic is non-obvious.
- Ruff is used for linting; run `ruff check .` before pushing.

## Reporting bugs

Please include:

- Expected behavior
- Actual behavior
- Reproduction steps
- OS and Python version
- Display used (DotPad, Monarch, etc.)
- Sample input file if relevant

Use the issue templates at https://github.com/cad-accessibility/cad-a11y/issues/new/choose

## License

By contributing, you agree that your contributions are licensed under the BSD 3-Clause License in this repository.
