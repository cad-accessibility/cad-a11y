# Contributing

Thanks for your interest in improving CAD A11y.

## Before You Start

- Open an issue for large changes so we can discuss options and scope.
- Keep pull requests focused and small when possible.
- Always consider the accessibility impact for new features.

## Development Setup

1. Install Conda and create the environment:
   - `conda env create -f environment.yml`
   - `conda activate cad-a11y`
2. Run the app locally:
   - `python app.py`

## Pull Request Guidelines

- Use clear commit messages.
- Add or update tests when behavior changes.
- Update documentation for user-facing changes.
- Verify there are no accidental generated artifacts or secrets.
- Verify the accessibility of any front end changes with automated tests and a screen reader

## Code Style

- Follow existing style in nearby files.
- Prefer readable, well-named functions.
- Add comments only where logic is non-obvious.

## Reporting Bugs

Please include:

- Expected behavior
- Actual behavior
- Reproduction steps
- OS and Python version
- Display used (DotPad, Monarch, etc)
- Sample input file if relevant

## License

By contributing, you agree that your contributions are licensed under the BSD 3-Clause License in this repository.
