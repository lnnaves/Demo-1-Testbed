#!/usr/bin/env bash

set -Eeuo pipefail

usage() {
  echo "Usage: $(basename "$0") <edhoc|pq-edhoc> <output-directory>"
  echo
  echo "Use a separate output directory for each protocol."
  echo
  echo "Examples:"
  echo "  $(basename "$0") edhoc ./bin/edhoc"
  echo "  $(basename "$0") pq-edhoc ./bin/pq-edhoc"
  echo
  echo "Generated binaries:"
  echo "  <output-directory>/initiator"
  echo "  <output-directory>/responder"
}

confirm() {
  local prompt="$1"
  local answer

  read -r -p "$prompt [y/N] " answer </dev/tty

  case "$answer" in
  y | Y | yes | Yes | YES)
    return 0
    ;;
  *)
    return 1
    ;;
  esac
}

if [[ $# -ne 2 ]]; then
  usage >&2
  exit 1
fi

PROTOCOL="$1"
OUTPUT_DIR="$2"

###############################################################################
# Project paths
###############################################################################

# Resolve paths relative to this script, regardless of the current directory.
#
# Expected structure:
#
#   project/
#   ├── pq-edhoc-config/
#   │   ├── edhoc.mk
#   │   └── pq_edhoc.mk
#   ├── scripts/
#   │   └── build-pq-edhoc-samples.sh
#   └── third-party/
#       └── PQ-EDHOC/
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

REPO_ROOT="$PROJECT_ROOT/third-party/PQ-EDHOC"
CONFIG_DIR="$PROJECT_ROOT/pq-edhoc-config"

if [[ ! -d "$REPO_ROOT" ]]; then
  echo "Error: PQ-EDHOC repository not found:" >&2
  echo "  $REPO_ROOT" >&2
  exit 1
fi

if [[ ! -d "$CONFIG_DIR" ]]; then
  echo "Error: project configuration directory not found:" >&2
  echo "  $CONFIG_DIR" >&2
  exit 1
fi

REPO_ROOT="$(cd -- "$REPO_ROOT" && pwd)"
CONFIG_DIR="$(cd -- "$CONFIG_DIR" && pwd)"

ACTIVE_CONFIG="$REPO_ROOT/makefile_config.mk"

###############################################################################
# Protocol selection
###############################################################################

case "$PROTOCOL" in
edhoc)
  CONFIG_FILE="$CONFIG_DIR/edhoc.mk"
  SAMPLE_DIR="$REPO_ROOT/samples/linux_edhoc"
  ;;

pq-edhoc)
  CONFIG_FILE="$CONFIG_DIR/pq_edhoc.mk"
  SAMPLE_DIR="$REPO_ROOT/samples/linux_pq_edhoc"
  ;;

*)
  echo "Error: unsupported protocol '$PROTOCOL'." >&2
  echo "Expected 'edhoc' or 'pq-edhoc'." >&2
  exit 1
  ;;
esac

INITIATOR_DIR="$SAMPLE_DIR/initiator"
RESPONDER_DIR="$SAMPLE_DIR/responder"

INITIATOR_MAIN="$INITIATOR_DIR/src/main.cpp"
RESPONDER_MAIN="$RESPONDER_DIR/src/main.cpp"

INITIATOR_BINARY="$INITIATOR_DIR/build/initiator"
RESPONDER_BINARY="$RESPONDER_DIR/build/responder"

###############################################################################
# Network addresses embedded in the binaries
###############################################################################

# The sample applications store their IPv4 addresses directly in main.cpp.
# Therefore, the addresses must be configured before compilation.
#
# In the Containernet topology:
#
#   dr1 initiator:
#     bat0 = 192.168.123.1
#
#   gcs0 responder:
#     bat0 = 192.168.123.5
#
# The initiator must connect to 192.168.123.5. The original EDHOC sample may
# use 127.0.0.1, which makes the initiator connect to itself and eventually
# fail with "recv error".
#
# The responder must bind to 0.0.0.0. Binding to 127.0.0.1 allows only local
# loopback communication and prevents packets received through bat0 from
# reaching the responder.
#
# Both source files are backed up before modification and restored when this
# script exits. The generated binaries retain the configured addresses.
INITIATOR_TARGET_IP="192.168.123.5"
RESPONDER_BIND_IP="0.0.0.0"

###############################################################################
# Required files
###############################################################################

for required_file in \
  "$CONFIG_FILE" \
  "$REPO_ROOT/Makefile" \
  "$INITIATOR_DIR/Makefile" \
  "$INITIATOR_MAIN" \
  "$RESPONDER_DIR/Makefile" \
  "$RESPONDER_MAIN"; do
  if [[ ! -f "$required_file" ]]; then
    echo "Error: required file not found:" >&2
    echo "  $required_file" >&2
    exit 1
  fi
done

###############################################################################
# Preserve files temporarily modified during the build
###############################################################################

CONFIG_BACKUP="$(mktemp)"
INITIATOR_MAIN_BACKUP="$(mktemp)"
RESPONDER_MAIN_BACKUP="$(mktemp)"

HAD_ACTIVE_CONFIG=false
CHANGED_INITIATOR_MAIN=false
CHANGED_RESPONDER_MAIN=false

if [[ -f "$ACTIVE_CONFIG" ]]; then
  cp -- "$ACTIVE_CONFIG" "$CONFIG_BACKUP"
  HAD_ACTIVE_CONFIG=true
fi

restore_files() {
  local exit_status=$?

  # Prevent this function from being executed recursively when calling exit.
  trap - EXIT

  if [[ "$CHANGED_INITIATOR_MAIN" == true ]]; then
    cp -- "$INITIATOR_MAIN_BACKUP" "$INITIATOR_MAIN"
  fi

  if [[ "$CHANGED_RESPONDER_MAIN" == true ]]; then
    cp -- "$RESPONDER_MAIN_BACKUP" "$RESPONDER_MAIN"
  fi

  if [[ "$HAD_ACTIVE_CONFIG" == true ]]; then
    cp -- "$CONFIG_BACKUP" "$ACTIVE_CONFIG"
  else
    rm -f -- "$ACTIVE_CONFIG"
  fi

  rm -f -- \
    "$CONFIG_BACKUP" \
    "$INITIATOR_MAIN_BACKUP" \
    "$RESPONDER_MAIN_BACKUP"

  exit "$exit_status"
}

trap restore_files EXIT

###############################################################################
# IPv4 source helpers
###############################################################################

extract_ipv4_address() {
  local source_file="$1"

  sed -nE \
    's|^[[:space:]]*const char IPV4_SERVADDR\[\][[:space:]]*=[[:space:]]*\{[[:space:]]*"([^"]+)".*|\1|p' \
    "$source_file" |
    head -n 1
}

replace_ipv4_address() {
  local source_file="$1"
  local new_address="$2"

  sed -Ei \
    's|^([[:space:]]*const char IPV4_SERVADDR\[\][[:space:]]*=[[:space:]]*\{[[:space:]]*")[^"]+(".*)$|\1'"$new_address"'\2|' \
    "$source_file"
}

###############################################################################
# Prepare liboqs for PQ-EDHOC
###############################################################################

if [[ "$PROTOCOL" == "pq-edhoc" ]]; then
  LIBOQS_DIR="$REPO_ROOT/externals/liboqs"
  LIBOQS_BUILD_DIR="$LIBOQS_DIR/build"
  LIBOQS_HEADER="$LIBOQS_BUILD_DIR/include/oqs/kem.h"
  LIBOQS_LIBRARY="$LIBOQS_BUILD_DIR/lib/liboqs.a"

  if [[ ! -f "$LIBOQS_HEADER" || ! -f "$LIBOQS_LIBRARY" ]]; then
    echo
    echo "liboqs is required by PQ-EDHOC but is not currently built."
    echo "Expected files:"
    echo "  $LIBOQS_HEADER"
    echo "  $LIBOQS_LIBRARY"
    echo

    if ! confirm "Initialize and build liboqs now?"; then
      echo "Error: PQ-EDHOC cannot be built without liboqs." >&2
      exit 1
    fi

    if [[ ! -f "$LIBOQS_DIR/CMakeLists.txt" ]]; then
      echo
      echo "Initializing the liboqs submodule..."

      git -C "$REPO_ROOT" \
        submodule update \
        --init \
        --recursive \
        externals/liboqs
    fi

    echo
    echo "Configuring liboqs..."

    cmake \
      -S "$LIBOQS_DIR" \
      -B "$LIBOQS_BUILD_DIR" \
      -DOQS_USE_OPENSSL=OFF

    echo
    echo "Building liboqs..."

    cmake --build "$LIBOQS_BUILD_DIR"

    if [[ ! -f "$LIBOQS_HEADER" || ! -f "$LIBOQS_LIBRARY" ]]; then
      echo "Error: liboqs build completed, but required files are missing:" >&2
      echo "  $LIBOQS_HEADER" >&2
      echo "  $LIBOQS_LIBRARY" >&2
      exit 1
    fi
  else
    echo
    echo "Using existing liboqs build:"
    echo "  $LIBOQS_BUILD_DIR"
  fi
fi

###############################################################################
# Configure the responder address embedded in the initiator
###############################################################################

CURRENT_INITIATOR_TARGET_IP="$(
  extract_ipv4_address "$INITIATOR_MAIN"
)"

if [[ -z "$CURRENT_INITIATOR_TARGET_IP" ]]; then
  echo "Error: could not find IPV4_SERVADDR in the initiator source:" >&2
  echo "  $INITIATOR_MAIN" >&2
  exit 1
fi

if [[ "$CURRENT_INITIATOR_TARGET_IP" != "$INITIATOR_TARGET_IP" ]]; then
  echo
  echo "The initiator currently targets:"
  echo "  $CURRENT_INITIATOR_TARGET_IP"
  echo
  echo "The Containernet responder gcs0 uses:"
  echo "  $INITIATOR_TARGET_IP"
  echo

  if ! confirm "Change the initiator target address before building?"; then
    echo "Error: build cancelled without changing the initiator address." >&2
    exit 1
  fi

  cp -- "$INITIATOR_MAIN" "$INITIATOR_MAIN_BACKUP"
  CHANGED_INITIATOR_MAIN=true

  replace_ipv4_address \
    "$INITIATOR_MAIN" \
    "$INITIATOR_TARGET_IP"

  CONFIGURED_INITIATOR_TARGET_IP="$(
    extract_ipv4_address "$INITIATOR_MAIN"
  )"

  if [[ "$CONFIGURED_INITIATOR_TARGET_IP" != "$INITIATOR_TARGET_IP" ]]; then
    echo "Error: failed to configure the initiator target address:" >&2
    echo "  $INITIATOR_MAIN" >&2
    exit 1
  fi

  echo "Initiator target address configured: $INITIATOR_TARGET_IP"
else
  echo
  echo "Initiator already targets the correct responder address:"
  echo "  $INITIATOR_TARGET_IP"
fi

###############################################################################
# Configure the responder bind address
###############################################################################

CURRENT_RESPONDER_BIND_IP="$(
  extract_ipv4_address "$RESPONDER_MAIN"
)"

if [[ -z "$CURRENT_RESPONDER_BIND_IP" ]]; then
  echo "Error: could not find IPV4_SERVADDR in the responder source:" >&2
  echo "  $RESPONDER_MAIN" >&2
  exit 1
fi

if [[ "$CURRENT_RESPONDER_BIND_IP" != "$RESPONDER_BIND_IP" ]]; then
  echo
  echo "The responder currently binds to:"
  echo "  $CURRENT_RESPONDER_BIND_IP"
  echo
  echo "To receive packets through bat0, it must bind to:"
  echo "  $RESPONDER_BIND_IP"
  echo

  if ! confirm "Change the responder bind address before building?"; then
    echo "Error: build cancelled without changing the responder address." >&2
    exit 1
  fi

  cp -- "$RESPONDER_MAIN" "$RESPONDER_MAIN_BACKUP"
  CHANGED_RESPONDER_MAIN=true

  replace_ipv4_address \
    "$RESPONDER_MAIN" \
    "$RESPONDER_BIND_IP"

  CONFIGURED_RESPONDER_BIND_IP="$(
    extract_ipv4_address "$RESPONDER_MAIN"
  )"

  if [[ "$CONFIGURED_RESPONDER_BIND_IP" != "$RESPONDER_BIND_IP" ]]; then
    echo "Error: failed to configure the responder bind address:" >&2
    echo "  $RESPONDER_MAIN" >&2
    exit 1
  fi

  echo "Responder bind address configured: $RESPONDER_BIND_IP"
else
  echo
  echo "Responder already uses the correct bind address:"
  echo "  $RESPONDER_BIND_IP"
fi

###############################################################################
# Build
###############################################################################

echo
echo "Protocol:         $PROTOCOL"
echo "Project:          $PROJECT_ROOT"
echo "Repository:       $REPO_ROOT"
echo "Config directory: $CONFIG_DIR"
echo "Configuration:    $CONFIG_FILE"
echo "Samples:          $SAMPLE_DIR"
echo "Initiator target: $INITIATOR_TARGET_IP"
echo "Responder bind:   $RESPONDER_BIND_IP"
echo "Output:           $OUTPUT_DIR"

# The PQ-EDHOC Makefile expects its active configuration at
# third-party/PQ-EDHOC/makefile_config.mk. Copy the configuration maintained
# by this project temporarily and restore the previous file on exit.
echo
echo "Selecting configuration..."
cp -- "$CONFIG_FILE" "$ACTIVE_CONFIG"

echo
echo "Cleaning previous builds..."
make -C "$INITIATOR_DIR" clean
make -C "$RESPONDER_DIR" clean
make -C "$REPO_ROOT" clean

echo
echo "Building library..."
make -C "$REPO_ROOT"

echo
echo "Building initiator..."
make -C "$INITIATOR_DIR"

echo
echo "Building responder..."
make -C "$RESPONDER_DIR"

###############################################################################
# Validate generated binaries
###############################################################################

if [[ ! -f "$INITIATOR_BINARY" ]]; then
  echo "Error: initiator binary was not generated:" >&2
  echo "  $INITIATOR_BINARY" >&2
  exit 1
fi

if [[ ! -f "$RESPONDER_BINARY" ]]; then
  echo "Error: responder binary was not generated:" >&2
  echo "  $RESPONDER_BINARY" >&2
  exit 1
fi

###############################################################################
# Install
###############################################################################

echo
echo "Installing binaries..."

install -d "$OUTPUT_DIR"

install -m 0755 \
  "$INITIATOR_BINARY" \
  "$OUTPUT_DIR/initiator"

install -m 0755 \
  "$RESPONDER_BINARY" \
  "$OUTPUT_DIR/responder"

OUTPUT_DIR="$(cd -- "$OUTPUT_DIR" && pwd)"

echo
echo "Build completed successfully:"
echo "  $OUTPUT_DIR/initiator"
echo "  $OUTPUT_DIR/responder"

echo
echo "Embedded network configuration:"
echo "  Initiator target: $INITIATOR_TARGET_IP"
echo "  Responder bind:   $RESPONDER_BIND_IP"

echo
echo "Configuration used:"
echo "  $CONFIG_FILE"

echo
echo "The modified PQ-EDHOC files will now be restored automatically."
