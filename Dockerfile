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
COPY study-control.html ./
COPY examples/ ./examples/
# Built-ins ship here, deliberately NOT into data/models. data/models is a mounted
# volume, so anything copied there is shadowed on a real deployment, and copying
# the directory wholesale would also bake a developer's local uploads into the
# image. The server seeds data/models from this directory on every start.
COPY builtin_models/ ./builtin_models/
# Maintenance script an operator runs inside the container; the data directories
# are Docker-managed volumes and are not conveniently reachable from the host.
COPY scripts/cleanup_ingest_models.py ./scripts/

# Python block-buffers stdout when it is a pipe, which is what it is under
# Docker. A long-running server therefore fills an 8 KB buffer that never
# flushes, so `docker compose logs` shows nothing at all -- not the startup
# banner, not the study control-panel URL, not an error on the way down. Only a
# crash or a print(flush=True) ever revealed any of it.
ENV PYTHONUNBUFFERED=1

# Runtime write directories are created here so the non-root user owns them.
RUN mkdir -p data/models data/uploads data/renders data/logs data/db

# --- Non-root user ---
# UID 48 matches the apache user the hosting NFS server grants write access to.
# The chown must run before USER so it still executes as root.
RUN useradd -d /home/apache -u 48 -m apache \
    && chown -R apache /project

USER apache

# --- Runtime ---

EXPOSE 6969

# /health rather than / — the root answers even when storage is misconfigured or
# the database cannot be opened, which is how a broken deploy looked healthy.
# Compose overrides this, so the deploy gate already uses /health either way;
# baking it in here is what anyone running the image directly gets.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:6969/health || exit 1

# Exec-form ENTRYPOINT for correct signal handling (SIGTERM reaches the process).
ENTRYPOINT ["conda", "run", "--no-capture-output", "-n", "cad-a11y", "python", "-m", "app.server"]
