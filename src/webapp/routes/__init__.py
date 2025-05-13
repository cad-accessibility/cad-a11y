#!/usr/bin/env python3
from flask import Blueprint, render_template, request, jsonify
import os
from src.converter.brep_to_svg import main as convert_brep_to_svg

main_routes = Blueprint('main', __name__)

@main_routes.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')

@main_routes.route('/convert', methods=['POST'])
def convert():
    """Convert a BREP file to SVG."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    # Save uploaded file to temp location
    file_path = os.path.join('/tmp', file.filename)
    file.save(file_path)
    
    # Process the file and generate SVGs
    try:
        # This would need to be adapted to work with the web app
        output_dir = os.path.join('src', 'models', 'svg')
        os.makedirs(output_dir, exist_ok=True)
        # convert_brep_to_svg function would need to be modified to work with web app
        convert_brep_to_svg(file_path, output_dir)
        
        basename = os.path.splitext(os.path.basename(file_path))[0]
        svg_files = [
            f"{basename}_top.svg",
            f"{basename}_front.svg",
            f"{basename}_right.svg"
        ]
        
        return jsonify({
            'success': True,
            'svg_files': svg_files
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500