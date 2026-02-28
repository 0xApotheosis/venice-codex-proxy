#!/usr/bin/env bash
# One-time setup: create env, install deps, and configure Codex Desktop.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

CODEX_CONFIG="$HOME/.codex/config.toml"
ENV_FILE="$DIR/.env"

API_KEY="${VENICE_API_KEY:-}"

BOLD=""
DIM=""
RED=""
GREEN=""
YELLOW=""
BLUE=""
CYAN=""
RESET=""

if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]] && command -v tput >/dev/null 2>&1; then
  colors="$(tput colors 2>/dev/null || echo 0)"
  if [[ "$colors" -ge 8 ]]; then
    BOLD="$(tput bold)"
    DIM="$(tput dim)"
    RED="$(tput setaf 1)"
    GREEN="$(tput setaf 2)"
    YELLOW="$(tput setaf 3)"
    BLUE="$(tput setaf 4)"
    CYAN="$(tput setaf 6)"
    RESET="$(tput sgr0)"
  fi
fi

header() {
  printf "\n%b\n" "${BOLD}${CYAN}╭────────────────────────────────────╮${RESET}"
  printf "%b\n" "${BOLD}${CYAN}│      Venice Codex Proxy Setup      │${RESET}"
  printf "%b\n" "${BOLD}${CYAN}╰────────────────────────────────────╯${RESET}"
}

divider() {
  printf "%b\n" "${DIM}────────────────────────────────────────${RESET}"
}

step() {
  printf "\n%b\n" "${BOLD}${CYAN}[$1/$2]${RESET} ${BOLD}$3${RESET}"
}

ok() {
  printf "  %b\n" "${GREEN}✔${RESET} $1"
}

info() {
  printf "  %b\n" "${DIM}•${RESET} $1"
}

warn() {
  printf "  %b\n" "${YELLOW}⚠${RESET} $1"
}

die() {
  printf "%b\n" "${RED}✖${RESET} $1" >&2
  exit 1
}

cmd_hint() {
  local label="$1"
  local command="$2"
  local branch="${3:-├─}"
  printf "  %b %b  %b\n" "${DIM}${branch}${RESET}" "${DIM}${label}${RESET}" "${BLUE}${command}${RESET}"
}

is_placeholder_key() {
  local v
  v="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  [[ -z "$v" || "$v" == *"your-venice-api-key"* || "$v" == "changeme" || "$v" == "replace-me" ]]
}

usage() {
  cat <<USAGE
Usage: ./setup.sh [options]

Options:
  --api-key <key>   Venice API key (skips interactive prompt)
  -h, --help        Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-key)
      [[ $# -ge 2 ]] || die "--api-key requires a value"
      API_KEY="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf "%b\n" "${RED}✖${RESET} Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

header
info "Workspace: $DIR"

if ! command -v python3 >/dev/null 2>&1; then
  die "python3 is required but was not found in PATH."
fi

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$DIR/.env.example" "$ENV_FILE"
  ok "Created $ENV_FILE from .env.example"
fi

if [[ -z "$API_KEY" ]]; then
  API_KEY="$(python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
value = ""
if path.exists():
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == "VENICE_API_KEY":
            value = v.strip().strip('"').strip("'")
            break
print(value)
PY
)"
fi

if is_placeholder_key "$API_KEY"; then
  API_KEY=""
fi

if [[ -z "$API_KEY" ]] && [[ -t 0 ]]; then
  printf "%b" "${BOLD}${BLUE}🔐 Enter your Venice API key:${RESET} "
  read -r -s API_KEY
  printf "\n"
fi

if [[ -z "$API_KEY" ]]; then
  die "Venice API key is required."
fi

python3 - "$ENV_FILE" "$API_KEY" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
api_key = sys.argv[2]

lines = path.read_text().splitlines() if path.exists() else []
out = []
updated = False
for line in lines:
    stripped = line.strip()
    if stripped.startswith("VENICE_API_KEY="):
        out.append(f"VENICE_API_KEY={api_key}")
        updated = True
    else:
        out.append(line)
if not updated:
    if out and out[-1] != "":
        out.append("")
    out.append(f"VENICE_API_KEY={api_key}")
path.write_text("\n".join(out).rstrip("\n") + "\n")
PY

ok "Saved API key to $ENV_FILE"

step 1 3 "Python environment"
if [[ ! -d "$DIR/venv" ]]; then
  python3 -m venv "$DIR/venv"
  ok "Created virtual environment"
else
  info "Using existing virtual environment"
fi
info "Installing dependencies"
"$DIR/venv/bin/python" -m pip install --quiet --upgrade pip
"$DIR/venv/bin/pip" install --quiet -r "$DIR/requirements.txt"
ok "Dependencies installed"

step 2 3 "Codex Desktop config"
mkdir -p "$HOME/.codex"
if [[ -f "$CODEX_CONFIG" ]]; then
  cp "$CODEX_CONFIG" "${CODEX_CONFIG}.bak"
  ok "Backup saved: ${CODEX_CONFIG}.bak"
fi

python3 - "$CODEX_CONFIG" <<'PY'
from pathlib import Path
import re
import sys

config_path = Path(sys.argv[1])
text = config_path.read_text() if config_path.exists() else ""

# Remove any previous managed block first
begin = "# >>> venice-codex-proxy >>>"
end = "# <<< venice-codex-proxy <<<"
pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.S)
text = pattern.sub("", text)

# Ensure top-level keys exist (update in place rather than appending a block)
# This avoids corrupting the TOML by inserting duplicate sections/keys in the
# wrong place (e.g. inside an [mcp_servers.*] section).

def set_top_level_key(text, key, value):
    """Set a top-level key (before any [section] header)."""
    # Find first section header
    first_section = re.search(r'^\[', text, re.M)
    top = text[:first_section.start()] if first_section else text
    rest = text[first_section.start():] if first_section else ""

    pat = re.compile(rf'^{re.escape(key)}\s*=.*$', re.M)
    if pat.search(top):
        top = pat.sub(f'{key} = {value}', top)
    else:
        top = top.rstrip("\n") + "\n" + f"{key} = {value}\n"
    return top + rest

text = set_top_level_key(text, "model", '"openai-gpt-53-codex"')
text = set_top_level_key(text, "model_provider", '"venice"')

# Ensure [model_providers.venice] section exists
section_pat = re.compile(r'^\[model_providers\.venice\].*?(?=^\[|\Z)', re.M | re.S)
venice_section = """[model_providers.venice]
name = "Venice AI"
base_url = "http://127.0.0.1:4000/v1"
experimental_bearer_token = "x"
"""
if section_pat.search(text):
    text = section_pat.sub(venice_section, text)
else:
    # Insert before [mcp_servers] if it exists, otherwise append
    mcp_match = re.search(r'^\[mcp_servers\]', text, re.M)
    if mcp_match:
        text = text[:mcp_match.start()] + venice_section + "\n" + text[mcp_match.start():]
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n" + venice_section

config_path.write_text(text)
PY
ok "Updated $CODEX_CONFIG"

step 3 3 "Smoke check"
if "$DIR/venv/bin/python" -m unittest -q "$DIR/test_proxy_normalization.py" >/dev/null 2>&1; then
  ok "Tests passed"
else
  warn "Unit tests failed during setup"
fi

printf "\n%b\n" "${BOLD}${GREEN}🎉 Setup complete${RESET}"
divider
cmd_hint "Start the proxy" "./start.sh" "├─"
cmd_hint "Health check" "curl -s http://127.0.0.1:4000/healthz" "├─"
cmd_hint "Follow logs" "tail -f proxy.log" "└─"
