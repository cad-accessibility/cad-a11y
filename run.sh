#!/bin/bash
# Script to run the CAD Accessibility web application

if ! command -v pixi >/dev/null 2>&1; then
    echo "pixi is required. Install pixi and run 'pixi install' first."
    exit 1
fi

pixi run start