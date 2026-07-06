# Repository Guidelines

## Project Structure & Module Organization

This repository contains a lightweight Markdown documentation server.

- `serve-docs.py`: main Python HTTP server, Markdown discovery, rendering page generation, search, static file serving, and background Git auto-pull logic.
- `start-docs.sh`, `start-docs.ps1`, `start-docs.cmd`: primary local launchers for Linux/macOS and Windows.
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`: container build and runtime configuration.
- `requirements.txt`: Python runtime dependency entry point; it currently contains no third-party packages.
- `README.md`: user-facing usage documentation and the default discoverable Markdown page.

There is no dedicated `tests/` directory yet. Add tests under `tests/` when behavior becomes large enough to justify repeatable coverage.

## Build, Test, and Development Commands

- `./start-docs.sh --no-open`: run the documentation server locally without launching a browser.
- `./start-docs.sh --help`: list supported runtime options, including Git auto-pull settings.
- `python3 -m venv .venv && . .venv/bin/activate`: create and enter a local virtual environment.
- `python -m pip install -r requirements.txt`: install Python runtime dependencies inside the active virtual environment.
- `python3 -m py_compile serve-docs.py`: check Python syntax.
- `bash -n start-docs.sh`: validate shell script syntax.
- `docker compose config`: validate Compose configuration.
- `WEB_DOCS_UID=$(id -u) WEB_DOCS_GID=$(id -g) docker compose up --build`: build and run the container with host-compatible file ownership.

## Coding Style & Naming Conventions

Use Python 3 standard-library APIs where practical; add third-party runtime packages to `requirements.txt` only when they are needed by `serve-docs.py`. Keep functions small, direct, and named with `snake_case`. Use 4-space indentation in Python and 2-space indentation in YAML. Shell scripts should use `set -euo pipefail` and quote path variables.

## Testing Guidelines

For now, use syntax checks plus focused manual or scripted smoke tests. When adding tests, prefer `pytest` with files named `test_*.py`. Cover document discovery, path traversal protection, Markdown raw serving, search behavior, and Git auto-pull failure cases.

## Commit & Pull Request Guidelines

This repository currently has no commit history to infer conventions from. Use concise imperative commit messages, for example `Add Git auto-pull support` or `Document Docker startup`. Pull requests should include a short summary, commands run, configuration changes, and screenshots only when UI rendering changes.

## Security & Configuration Tips

Git auto-pull uses `git pull --ff-only` and disables interactive prompts. Do not commit credentials. For Docker, keep `/docs` writable only when automatic Git updates are required.
