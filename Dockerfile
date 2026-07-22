# Pin the base image to avoid silent breakage from upstream updates.
FROM continuumio/miniconda3:24.11.1-0

WORKDIR /project

# System dependencies — curl is needed for the HEALTHCHECK.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libhidapi-hidraw0 \
    libhidapi-dev \
    libgl1 \
    libglib2.0-0 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# --- Dependency layers (cached until environment.yml / requirements.txt change) ---

# Create conda environment with pythonocc-core (conda-only package).
COPY environment.yml .
RUN conda env create -f environment.yml && conda clean -afy

# All subsequent RUN commands execute inside the conda env.
SHELL ["conda", "run", "-n", "cad-a11y", "/bin/bash", "-c"]

# Install pip dependencies.
# polyscope is optional — failures are non-fatal.
COPY requirements.txt .
RUN grep -vE '^\s*(#|polyscope)' requirements.txt \
      | pip install --no-cache-dir -r /dev/stdin
RUN pip install --no-cache-dir polyscope || true

# --- Application source (invalidates only when code changes) ---

COPY app/ ./app/
COPY src/ ./src/
COPY static/ ./static/
COPY accessible-3d-viewer.html ./
COPY workshop-entry.html ./
COPY examples/ ./examples/
COPY src/models/brep/ ./data/models/

# Runtime write directories are created here so the non-root user owns them.
RUN mkdir -p data/renders data/logs data/db

# --- Non-root user ---
# UID 48 matches the apache user the hosting NFS server grants write access to.
# The chown must run before USER so it still executes as root.
RUN useradd -d /home/apache -u 48 -m apache \
    && chown -R apache /project

USER apache

# --- Runtime ---

EXPOSE 6969

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:6969/ || exit 1

# Exec-form ENTRYPOINT for correct signal handling (SIGTERM reaches the process).
ENTRYPOINT ["conda", "run", "--no-capture-output", "-n", "cad-a11y", "python", "-m", "app.server"]
