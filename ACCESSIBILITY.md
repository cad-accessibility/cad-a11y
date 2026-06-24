# Accessibility

CAD A11y exists to make 3D CAD models accessible to blind and low-vision (BLV) users. Accessibility is not a feature — it is the purpose of this project.

## Who this project serves

Blind and low-vision (BLV) designers, engineers, and hobbyists who need nonvisual access to 3D CAD models. The tool was built for people who participate in design, engineering, digital fabrication, or assistive technology work and who interact with computers primarily through refreshable braille displays (such as the APH Monarch or DotPad), screen readers, or tactile hardware. It is developed through co-design with BLV and DeafBlind collaborators.

## Reporting an accessibility problem

If you find a barrier that prevents you from using the viewer, a device integration, or any part of this repository (including documentation), please open an issue using the [Accessibility report template](https://github.com/cad-accessibility/cad-a11y/issues/new?template=accessibility.yml).

Include:
- Which assistive technology you were using (screen reader name and version, braille display model, etc.)
- What you were trying to do
- What happened instead
- Operating system and browser (for viewer issues)

We treat accessibility issues as high priority. There is no such thing as a minor accessibility bug for users who depend on assistive technology.

## Accessibility of this repository

We apply accessible practices to the repository itself:

- Images in issues and pull requests must include descriptive alt text.
- Documentation uses semantic heading hierarchy (no heading levels skipped).
- The GitHub CLI (`gh`) is fully supported for contributors who prefer it over the web interface — see [CONTRIBUTING.md](CONTRIBUTING.md#contributing-via-the-github-cli-accessible-workflow).
- Issue templates use GitHub's structured form fields so screen readers present them as labeled inputs.

## Accessibility of the viewer

The web viewer (`accessible-3d-viewer.html`) targets WCAG 2.1 AA. CI runs automated axe-core checks on every pull request.

Known limitations as of this writing:

- The SVG output rendered by the converter is not yet fully described by structured alt text; this is active research.
- Device integrations (Monarch, DotPad, Trinkey) require physical hardware and cannot be exercised in automated tests.

## Scope of accessibility support

This project primarily targets desktop screen readers and braille displays on Windows, macOS, and Linux. Mobile browser support and voice control (Dragon NaturallySpeaking, Voice Control) are not explicitly tested but we welcome bug reports.

## Contact

Open an issue, or reach the maintainers through [GitHub Discussions](https://github.com/cad-accessibility/cad-a11y/discussions).
