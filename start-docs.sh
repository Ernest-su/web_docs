#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WEB_DOCS_ROOT="$SCRIPT_DIR"

print_help() {
  cat <<'EOF'
Usage:
  ./start-docs.sh [options]

Options:
  -h, --help, help       Show this help message.
  -p, --port PORT        Run the docs server on PORT.
  --host HOST            Bind the docs server to HOST.
  --no-open              Do not open the browser automatically.
  --title TITLE          Set the browser page title.
  --git-pull             Enable background git pull. Enabled by default.
  --no-git-pull          Disable background git pull.
  --git-pull-interval SECONDS
                         Seconds between background git pull attempts.
  --git-pull-remote REMOTE
                         Optional git remote for pull.
  --git-pull-branch BRANCH
                         Optional git branch for pull.
  --git-pull-timeout SECONDS
                         Seconds before a git pull attempt times out.
  --mermaid-url URL      Browser module URL for Mermaid.
  --marked-url URL       Browser script URL for marked.

Environment:
  PYTHON_BIN             Python executable to use.
  WEB_DOCS_HOST      Default host when --host is not provided.
  WEB_DOCS_PORT      Default port when --port is not provided.
  WEB_DOCS_GIT_PULL  Set to 0/false/no/off to disable auto-pull.
  WEB_DOCS_GIT_PULL_INTERVAL
  WEB_DOCS_GIT_PULL_REMOTE
  WEB_DOCS_GIT_PULL_BRANCH
  WEB_DOCS_GIT_PULL_TIMEOUT
  WEB_DOCS_MERMAID_URL
  WEB_DOCS_MARKED_URL

Examples:
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install -r requirements.txt
  ./start-docs.sh --port 8091
  ./start-docs.sh -p 8091 --no-open
  ./start-docs.sh --git-pull-interval 600
EOF
}

DOCS_ARGS=()
while (($# > 0)); do
  case "$1" in
    -h|--help|help)
      print_help
      exit 0
      ;;
    -p|--port)
      if (($# < 2)); then
        echo "Missing value for $1." >&2
        exit 2
      fi
      DOCS_ARGS+=(--port "$2")
      shift 2
      ;;
    --port=*)
      DOCS_ARGS+=(--port "${1#--port=}")
      shift
      ;;
    *)
      DOCS_ARGS+=("$1")
      shift
      ;;
  esac
done

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$WEB_DOCS_ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$WEB_DOCS_ROOT/.venv/bin/python"
  else
    for candidate in python3.14 python3 python; do
      if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
      fi
    done
  fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python was not found. Install Python 3 and retry." >&2
  exit 1
fi

cd "$WEB_DOCS_ROOT"
exec "$PYTHON_BIN" "$WEB_DOCS_ROOT/serve-docs.py" "${DOCS_ARGS[@]}"
