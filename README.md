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
- Conda (for environment management)

### Installation

1. Clone this repository
2. Create and activate the Conda environment:
   ```
   conda env create -f environment.yml
   conda activate cad-a11y
   ```

### Running the Command-line Tool

#### Converting BREP to SVG
To convert a BREP file to SVG from the command line:

```
python src/converter/brep_to_svg.py path/to/your/file.brep -o output_directory
```

#### Converting OpenSCAD to STEP
To convert OpenSCAD files to STEP format (new feature):

```
python scripts/scad_to_step.py input.scad [output.step]
```

For more options and detailed usage, see `scripts/README_scad_to_step.md`.

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