FROM continuumio/miniconda3:latest

WORKDIR /app

# Install system dependencies for hidapi, meshlib, and other native libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    libhidapi-hidraw0 \
    libhidapi-dev \
    libgl1 \
    libglib2.0-0 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Create conda environment with pythonocc-core (conda-only package)
COPY environment.yml .
RUN conda env create -f environment.yml && conda clean -afy

# Make all subsequent RUN/CMD commands use the conda env
SHELL ["conda", "run", "-n", "cad-a11y", "/bin/bash", "-c"]

# Install pip dependencies — optional packages (godice, polyscope) may not be
# available on all platforms; install them separately so failures don't block the rest.
COPY requirements.txt .
RUN grep -vE '^\s*(#|godice|polyscope)' requirements.txt \
      | pip install --no-cache-dir -r /dev/stdin && \
    pip install --no-cache-dir godice polyscope || true

# Copy application source
COPY server.py braille_display.py cad_comparison_lib.py ./
COPY src/ ./src/
COPY static/ ./static/
COPY accessible-3d-viewer.html ./
COPY model/ ./model/

EXPOSE 6969

ENTRYPOINT ["conda", "run", "--no-capture-output", "-n", "cad-a11y", "python", "server.py"]
