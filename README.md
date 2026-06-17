# CAD Accessibility Tool

A tool for converting CAD models in BREP format to accessible SVG representations from multiple viewpoints.

## Project Overview

This project aims to make CAD models more accessible by converting BREP (Boundary Representation) files into SVG visualizations that can be easily viewed and shared. The tool provides multiple views of CAD models (top, front, right) to enhance accessibility for users who may benefit from 2D representations.

## Directory Structure

```
cad-a11y/
│
├── app.py                  # Main web application entry point
├── environment.yml         # Conda environment configuration
│
├── src/
│   ├── converter/          # Core conversion logic
│   │   └── brep_to_svg.py  # BREP to SVG conversion script
│   │
│   ├── models/             # Model files
│   │   ├── brep/           # Input BREP files
│   │   └── svg/            # Output SVG files
│   │
│   └── webapp/             # Web application code
│       ├── app/            # Flask application
│       ├── config/         # Configuration settings
│       ├── routes/         # Route definitions
│       ├── static/         # Static assets (CSS, JS, images)
│       └── templates/      # HTML templates
│
└── scripts/                # Utility scripts
    └── generate_cylinder_brep.py  # Script to generate sample BREP files
```

## Getting Started

### Prerequisites

- Python 3.9+
- Pixi (recommended, for reproducible environment management)
- Conda (optional fallback)

### Installation

1. Clone this repository
2. Install dependencies with Pixi:
    ```
    pixi install
    ```

### Running with Pixi

Use Pixi tasks for consistent local commands:

```
pixi run start
pixi run test
pixi run lint
```

### Conda Fallback

If you are not using Pixi, you can still use Conda:

   ```
   conda env create -f environment.yml
   conda activate cad-a11y
   ```

### Running the Command-line Tool

To convert a BREP file to SVG from the command line:

```
python src/converter/brep_to_svg.py path/to/your/file.brep -o output_directory
```

### Running the Web Application

To run the web application:

```
python app.py
```

Then open a browser and navigate to http://localhost:5000

## Future Development

- Expanding to support additional CAD file formats
- Improving SVG rendering and accessibility features
- Adding user authentication and file management

## Contributing

Contributions are welcome. Please read CONTRIBUTING.md before opening a pull request.


## Code of Conduct

This project follows the guidelines in CODE_OF_CONDUCT.md.

## Security

To report vulnerabilities, follow SECURITY.md and do not file public issues for security reports.

## License

This repository is licensed under the BSD 3-Clause License. See LICENSE for details.