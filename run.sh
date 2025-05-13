#!/bin/bash
# Script to run the CAD Accessibility web application

# Activate conda environment if it exists
if command -v conda &> /dev/null; then
    conda activate cad-a11y || echo "Conda environment 'cad-a11y' not found. Please create it with 'conda env create -f environment.yml'"
fi

# Set Flask development mode
export FLASK_APP=app.py
export FLASK_ENV=development
export FLASK_DEBUG=1

# Run the application
python app.py