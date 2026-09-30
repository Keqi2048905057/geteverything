#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQUIREMENTS_FILE="$ROOT_DIR/requirement.txt"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GO_BIN="$HOME/go/bin"

CHECK_ONLY=0
SKIP_SYSTEM=0
SKIP_PYTHON_DEPS=0
SKIP_GO_TOOLS=0
WITH_OPTIONAL=1
DRY_RUN=0

declare -A INSTALL_SUMMARY=()

GO_TOOLS=(
  "subfinder=github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest"
  "shuffledns=github.com/projectdiscovery/shuffledns/cmd/shuffledns@latest"
  "alterx=github.com/projectdiscovery/alterx/cmd/alterx@latest"
  "gospider=github.com/jaeles-project/gospider@latest"
  "dnsx=github.com/projectdiscovery/dnsx/cmd/dnsx@latest"
  "httpx=github.com/projectdiscovery/httpx/cmd/httpx@latest"
  "naabu=github.com/projectdiscovery/naabu/v2/cmd/naabu@latest"
  "waybackurls=github.com/tomnomnom/waybackurls@latest"
  "katana=github.com/projectdiscovery/katana/cmd/katana@latest"
  "assetfinder=github.com/tomnomnom/assetfinder@latest"
)

EXPECTED_TOOLS=(
  subfinder dnsx httpx http-x naabu nmap katana gospider waybackurls
  feroxbuster dirsearch oneforall enscan assetfinder shuffledns alterx amass
)

usage() {
  cat <<'EOF'
Usage: bash scripts/install_linux.sh [options]

Options:
  --check-only
  --skip-system
  --skip-python-deps
  --skip-go-tools
  --with-optional
  --dry-run
  -h, --help
EOF
}

set_status() {
  INSTALL_SUMMARY["$1"]="$2"
}

run() {
  echo "[*] $*"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    "$@"
  fi
}

exists() {
  command -v "$1" >/dev/null 2>&1
}

sudo_cmd() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
  elif exists sudo; then
    sudo "$@"
  else
    echo "[!] This step requires root privileges or sudo: $*" >&2
    return 1
  fi
}

run_root() {
  echo "[*] $*"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    sudo_cmd "$@"
  fi
}

python_cmd() {
  if exists python3; then
    echo "python3"
  elif exists python; then
    echo "python"
  else
    echo ""
  fi
}

ensure_go_bin() {
  run mkdir -p "$GO_BIN"
  export PATH="$PATH:$GO_BIN"
}

add_go_path() {
  ensure_go_bin
  local profile="$HOME/.profile"
  local line='export PATH="$PATH:$HOME/go/bin"'
  if [[ -f "$profile" ]] && ! grep -Fq "$line" "$profile"; then
    echo "[*] Add Go bin to $profile"
    if [[ "$DRY_RUN" -eq 0 ]]; then
      printf '\n# Go user binaries\n%s\n' "$line" >> "$profile"
    fi
  fi
}

resolve_tool_path() {
  command -v "$1" 2>/dev/null || true
}

ensure_httpx_alias() {
  local target="$GO_BIN/httpx"
  local alias_path="$GO_BIN/http-x"
  if [[ ! -f "$target" ]]; then
    echo "[!] ProjectDiscovery httpx was not found at: $target"
    return 1
  fi
  echo "[*] Create http-x alias: $alias_path"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    cat >"$alias_path" <<EOF
#!/usr/bin/env bash
"$target" "\$@"
EOF
    chmod +x "$alias_path"
  fi
}

test_httpx_installation() {
  local expected_httpx="$GO_BIN/httpx"
  local expected_alias="$GO_BIN/http-x"
  local resolved_httpx resolved_alias
  resolved_httpx="$(resolve_tool_path httpx)"
  resolved_alias="$(resolve_tool_path http-x)"
  echo "[i] Expected ProjectDiscovery httpx: $expected_httpx"
  echo "[i] which httpx => ${resolved_httpx:-<missing>}"
  echo "[i] which http-x => ${resolved_alias:-<missing>}"
  [[ -f "$expected_httpx" ]] || return 1
  [[ -f "$expected_alias" ]] || return 1
  [[ "$resolved_alias" == "$expected_alias" ]] || return 1
  if [[ -n "$resolved_httpx" && "$resolved_httpx" != "$expected_httpx" ]]; then
    echo "[!] httpx resolves to another executable. The project will use http-x instead."
  fi
  echo "[ok] ProjectDiscovery httpx alias is ready"
}

install_system_dependencies() {
  echo
  echo "=== System dependencies ==="
  if exists apt-get; then
    run_root apt-get update
    run_root apt-get install -y python3 python3-pip golang-go git nmap curl wget unzip ca-certificates
  elif exists dnf; then
    run_root dnf install -y python3 python3-pip golang git nmap curl wget unzip ca-certificates
  elif exists yum; then
    run_root yum install -y python3 python3-pip golang git nmap curl wget unzip ca-certificates
  elif exists pacman; then
    run_root pacman -Sy --needed --noconfirm python python-pip go git nmap curl wget unzip ca-certificates
  elif exists zypper; then
    run_root zypper install -y python3 python3-pip go git nmap curl wget unzip ca-certificates
  elif exists apk; then
    run_root apk add --no-cache python3 py3-pip go git nmap curl wget unzip ca-certificates
  else
    echo "[!] Unsupported package manager. Install dependencies manually."
  fi
  add_go_path
}

install_python_dependencies() {
  echo
  echo "=== Python dependencies ==="
  local py
  py="$(python_cmd)"
  if [[ -z "$py" ]]; then
    echo "[!] Python is not installed or not in PATH."
    return
  fi
  if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
    echo "[!] Missing requirements file: $REQUIREMENTS_FILE"
    return
  fi
  run "$py" -m pip install --user --upgrade pip || run "$py" -m pip install --user --break-system-packages --upgrade pip
  run "$py" -m pip install --user -r "$REQUIREMENTS_FILE" || run "$py" -m pip install --user --break-system-packages -r "$REQUIREMENTS_FILE"
}

install_amass() {
  echo
  echo "=== amass ==="
  if exists amass; then
    echo "[=] amass already exists in PATH"
    return
  fi
  echo "[WARN] Please install amass manually on Linux/macOS if needed."
}

install_go_tools() {
  echo
  echo "=== Go tools ==="
  if ! exists go; then
    echo "[!] Go is not installed or not in PATH."
    return
  fi
  add_go_path
  local entry tool module tool_path
  for entry in "${GO_TOOLS[@]}"; do
    tool="${entry%%=*}"
    module="${entry#*=}"
    tool_path="$GO_BIN/$tool"
    if [[ -f "$tool_path" ]]; then
      echo "[=] $tool already installed in Go bin"
      continue
    fi
    run go install "$module"
  done
  ensure_httpx_alias || true
  test_httpx_installation || true
  install_amass
}

find_local_binary() {
  local candidate
  for candidate in "$@"; do
    if [[ -f "$SCRIPT_DIR/$candidate" ]]; then
      echo "$SCRIPT_DIR/$candidate"
      return 0
    fi
  done
  return 1
}

copy_local_binary() {
  local tool_name="$1"
  local target_name="$2"
  shift 2
  ensure_go_bin
  local source=""
  if source="$(find_local_binary "$@" 2>/dev/null)"; then
    local target="$GO_BIN/$target_name"
    run cp "$source" "$target"
    run chmod +x "$target"
    set_status "$tool_name" "success"
    return 0
  fi
  set_status "$tool_name" "failed"
  return 1
}

install_feroxbuster() {
  echo
  echo "=== feroxbuster ==="
  ensure_go_bin
  local target="$GO_BIN/feroxbuster"
  if exists feroxbuster; then
    echo "[SKIP] feroxbuster already installed"
    set_status "feroxbuster" "skipped"
    return
  fi
  if exists cargo; then
    run cargo install feroxbuster
    if [[ -f "$HOME/.cargo/bin/feroxbuster" && "$HOME/.cargo/bin/feroxbuster" != "$target" ]]; then
      run cp "$HOME/.cargo/bin/feroxbuster" "$target"
      run chmod +x "$target"
    fi
    set_status "feroxbuster" "success"
  else
    echo "[WARN] cargo not found, please install feroxbuster manually"
    set_status "feroxbuster" "failed"
  fi
}

install_enscan() {
  echo
  echo "=== enscan ==="
  ensure_go_bin
  local target="$GO_BIN/enscan"
  if [[ -f "$target" ]]; then
    echo "[SKIP] enscan already installed"
    set_status "enscan" "skipped"
    return
  fi
  if ! exists go; then
    echo "[WARN] Go is not installed or not in PATH, cannot build enscan."
    set_status "enscan" "failed"
    return
  fi
  if ! exists git; then
    echo "[WARN] Git is not installed or not in PATH, cannot fetch ENScan_GO."
    set_status "enscan" "failed"
    return
  fi

  local repo_dir="${TMPDIR:-/tmp}/ENScan_go_install"
  rm -rf "$repo_dir"
  run git clone --depth 1 https://github.com/wgpsec/ENScan_GO.git "$repo_dir"
  (
    cd "$repo_dir"
    GOBIN="$GO_BIN" go install .
  )

  if [[ -f "$GO_BIN/ENScan" && ! -f "$target" ]]; then
    run mv "$GO_BIN/ENScan" "$target"
  fi

  if [[ -f "$target" ]]; then
    run chmod +x "$target"
    set_status "enscan" "success"
  else
    set_status "enscan" "failed"
  fi
}

install_optional_tools() {
  echo
  echo "=== Optional tools ==="
  install_feroxbuster
  install_enscan

  if find_local_binary "oneforall.exe" "OneForAll.exe" "one_for_all.exe" >/dev/null 2>&1; then
    echo "[WARN] Detected OneForAll as Windows exe; Linux/macOS cannot run it directly."
    set_status "oneforall" "skipped"
  elif find_local_binary "oneforall" "OneForAll" >/dev/null 2>&1; then
    copy_local_binary "oneforall" "oneforall" "oneforall" "OneForAll" || true
  else
    echo "[WARN] oneforall binary not found beside installer."
    set_status "oneforall" "skipped"
  fi

  if find_local_binary "dirsearch.exe" "Dirsearch.exe" >/dev/null 2>&1; then
    echo "[WARN] Detected dirsearch as Windows exe; Linux/macOS cannot run it directly."
    set_status "dirsearch" "skipped"
  elif find_local_binary "dirsearch" >/dev/null 2>&1; then
    copy_local_binary "dirsearch" "dirsearch" "dirsearch" || true
  else
    echo "[WARN] dirsearch binary not found beside installer."
    set_status "dirsearch" "skipped"
  fi
}

verify_environment() {
  echo
  echo "=== Verification ==="
  local tool
  for tool in "${EXPECTED_TOOLS[@]}"; do
    if exists "$tool"; then
      echo "[ok] $tool"
    else
      echo "[--] $tool not found"
    fi
  done
  test_httpx_installation || true
}

print_install_summary() {
  echo
  echo "工具安装汇总："
  for tool in ENScan OneForAll dirsearch naabu feroxbuster; do
    local key="${tool,,}"
    local status="${INSTALL_SUMMARY[$key]:-skipped}"
    echo "- $tool: $status"
  done
  echo
  echo "Go bin 路径："
  echo "- Linux/macOS: $GO_BIN"
  echo
  echo "PATH 状态："
  if [[ ":$PATH:" == *":$GO_BIN:"* ]]; then
    echo "- Go bin 已在 PATH"
  else
    echo "- Go bin 不在 PATH，请手动添加"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only) CHECK_ONLY=1 ;;
    --skip-system) SKIP_SYSTEM=1 ;;
    --skip-python-deps) SKIP_PYTHON_DEPS=1 ;;
    --skip-go-tools) SKIP_GO_TOOLS=1 ;;
    --with-optional) WITH_OPTIONAL=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[!] Unknown argument: $1"; usage; exit 1 ;;
  esac
  shift
done

echo "get_everything_framework Linux installer"
echo "Project root: $ROOT_DIR"

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  verify_environment
  print_install_summary
  exit 0
fi

if [[ "$SKIP_SYSTEM" -eq 0 ]]; then install_system_dependencies; fi
if [[ "$SKIP_PYTHON_DEPS" -eq 0 ]]; then install_python_dependencies; fi
if [[ "$SKIP_GO_TOOLS" -eq 0 ]]; then install_go_tools; fi

if [[ "$WITH_OPTIONAL" -eq 1 ]]; then
  install_optional_tools
fi

if [[ -f "$GO_BIN/naabu" ]]; then set_status "naabu" "success"; else set_status "naabu" "failed"; fi
[[ -n "${INSTALL_SUMMARY[oneforall]:-}" ]] || set_status "oneforall" "skipped"
[[ -n "${INSTALL_SUMMARY[dirsearch]:-}" ]] || set_status "dirsearch" "skipped"
[[ -n "${INSTALL_SUMMARY[enscan]:-}" ]] || set_status "enscan" "skipped"
[[ -n "${INSTALL_SUMMARY[feroxbuster]:-}" ]] || set_status "feroxbuster" "skipped"

verify_environment
print_install_summary
echo
echo "[+] Done. Restart the shell if newly installed commands are still not found."
