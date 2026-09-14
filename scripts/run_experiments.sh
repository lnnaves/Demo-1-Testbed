#!/usr/bin/env bash

# Usage:
#   ./run_experiments.sh <edhoc|pq-edhoc> [executions] [output.csv] [debug] [ardupilot]
#
# Examples:
#   ./run_experiments.sh edhoc
#   ./run_experiments.sh edhoc 30 results_edhoc.csv 0 0
#   ./run_experiments.sh edhoc 30 results_edhoc.csv 0 1
#   ./run_experiments.sh pq-edhoc 30 results_pq_edhoc.csv 0 1
#   ./run_experiments.sh pq-edhoc 1 test.csv 1 1
#
# Optional environment variables:
#   INITIATOR_CONTAINER=mn.dr1
#   RESPONDER_CONTAINER=mn.gcs0
#   IFACE=bat0
#   BIN_ROOT=/opt/edhoc-bin
#   ARDUPILOT=0|1
#   ARDUPILOT_HOME=-35.363261,149.165230,584,353
#   ARDUPILOT_CMD="arducopter -S -I0 --model quad --home ..."
#
# The output of bin/ on the host is expected to be mounted as:
#
#   /opt/edhoc-bin/
#   ├── edhoc/
#   │   ├── initiator
#   │   └── responder
#   └── pq-edhoc/
#       ├── initiator
#       └── responder

set -uo pipefail

###############################################################################
# Arguments
###############################################################################

usage() {
  echo "Usage:"
  echo "  $(basename "$0") <edhoc|pq-edhoc> [executions] [output.csv] [debug] [ardupilot]"
  echo
  echo "Arguments:"
  echo "  <protocol>     'edhoc' or 'pq-edhoc'"
  echo "  [executions]   Number of runs (default: 30)"
  echo "  [output.csv]   Output CSV filename (default: results_<protocol>.csv)"
  echo "  [debug]        Debug mode: 0 or 1 (default: 0)"
  echo "  [ardupilot]    Enable ArduPilot in background: 0 or 1 (default: 0)"
  echo
  echo "Examples:"
  echo "  $(basename "$0") edhoc"
  echo "  $(basename "$0") edhoc 30 results_edhoc.csv 0 0"
  echo "  $(basename "$0") edhoc 30 results_edhoc.csv 0 1"
  echo "  $(basename "$0") pq-edhoc 30 results_pq_edhoc.csv 0 1"
  echo "  $(basename "$0") pq-edhoc 1 test.csv 1 1"
}

if [[ $# -lt 1 || $# -gt 5 ]]; then
  usage >&2
  exit 1
fi

PROTOCOL="$1"
N="${2:-30}"
DEBUG="${4:-0}"
ARDUPILOT_ARG="${5:-${ARDUPILOT:-0}}"

case "$PROTOCOL" in
edhoc)
  DEFAULT_OUTPUT="results_edhoc.csv"
  ;;
pq-edhoc)
  DEFAULT_OUTPUT="results_pq_edhoc.csv"
  ;;
*)
  echo "Error: unsupported protocol '$PROTOCOL'." >&2
  echo "Expected 'edhoc' or 'pq-edhoc'." >&2
  exit 1
  ;;
esac

OUTPUT_CSV="${3:-$DEFAULT_OUTPUT}"

if ! [[ "$N" =~ ^[1-9][0-9]*$ ]]; then
  echo "Error: executions must be a positive integer: $N" >&2
  exit 1
fi

if [[ "$DEBUG" != "0" && "$DEBUG" != "1" ]]; then
  echo "Error: debug must be 0 or 1." >&2
  exit 1
fi

case "$ARDUPILOT_ARG" in
0|false|no|off|none)
  ENABLE_ARDUPILOT=0
  ;;
1|true|yes|on|ardupilot|with-ardupilot)
  ENABLE_ARDUPILOT=1
  ;;
*)
  echo "Error: ardupilot must be 0 or 1 (or 'ardupilot'/'no-ardupilot'): $ARDUPILOT_ARG" >&2
  exit 1
  ;;
esac

OUTPUT_NAME="$(basename -- "${3:-$DEFAULT_OUTPUT}")"

# Store every output from this invocation in a dedicated run directory.
# Paths are resolved relative to the project instead of the directory from
# which this script is executed.
#
# Example:
#   logs/20260805-134530_edhoc_12345/
#
# RUN_ID and LOGS_ROOT can optionally be overridden:
#   RUN_ID=my-test LOGS_ROOT=/tmp/logs ./run_experiments.sh edhoc
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

LOGS_ROOT="${LOGS_ROOT:-$PROJECT_ROOT/logs}"
RUN_ID="${RUN_ID:-$(date +'%Y%m%d-%H%M%S')_${PROTOCOL}_$$}"
RUN_DIR="$LOGS_ROOT/$RUN_ID"

mkdir -p -- "$RUN_DIR"

OUTPUT_DIR="$RUN_DIR"
OUTPUT_CSV="$RUN_DIR/$OUTPUT_NAME"
OUTPUT_STEM="${OUTPUT_NAME%.csv}"

###############################################################################
# Containers and binaries
###############################################################################

INITIATOR_CONTAINER="${INITIATOR_CONTAINER:-mn.dr1}"
RESPONDER_CONTAINER="${RESPONDER_CONTAINER:-mn.gcs0}"
IFACE="${IFACE:-bat0}"

BIN_ROOT="${BIN_ROOT:-/opt/edhoc-bin}"
PROTOCOL_BIN_DIR="$BIN_ROOT/$PROTOCOL"

INITIATOR_BIN="$PROTOCOL_BIN_DIR/initiator"
RESPONDER_BIN="$PROTOCOL_BIN_DIR/responder"

# ArduPilot configuration
ARDUPILOT_CONTAINER="${ARDUPILOT_CONTAINER:-$INITIATOR_CONTAINER}"
ARDUPILOT_HOME="${ARDUPILOT_HOME:--35.363261,149.165230,584,353}"
ARDUPILOT_CMD="${ARDUPILOT_CMD:-arducopter -S -I0 --model quad --home $ARDUPILOT_HOME}"

###############################################################################
# Expected CoAP sequence
###############################################################################

# Message 1: MID 0, first occurrence.
# Message 2: MID 0, second occurrence.
# Message 3: MID 1, first occurrence.
EXPECTED_MID_SEQUENCE="0 0 1"

###############################################################################
# Wait times
###############################################################################

SLEEP_CLEANUP=0.5
SLEEP_TCPDUMP_START=1.5
SLEEP_RESPONDER_START=1
SLEEP_PERF_ATTACH=0.5
SLEEP_ARDUPILOT_START=1
SLEEP_CAPTURE_DRAIN=2
SLEEP_PERF_STOP=0.5
SLEEP_TCPDUMP_STOP=1

###############################################################################
# Current execution state
###############################################################################

CURRENT_INIT_TCPDUMP_PID=""
CURRENT_RESP_TCPDUMP_PID=""
CURRENT_RESP_PID=""
CURRENT_PERF_PID=""
CURRENT_ARDUPILOT_PID=""

CLEANUP_IN_PROGRESS=0

###############################################################################
# Messages
###############################################################################

debug() {
  if [[ "$DEBUG" == "1" ]]; then
    printf '[DEBUG] %s\n' "$*"
  fi
}

info() {
  printf '[INFO] %s\n' "$*"
}

ok() {
  printf '[OK] %s\n' "$*"
}

warn() {
  printf '[WARNING] %s\n' "$*" >&2
}

error() {
  printf '[ERROR] %s\n' "$*" >&2
}

section() {
  echo
  echo "======================================================================"
  echo "$1"
  echo "======================================================================"
}

###############################################################################
# Docker and process helpers
###############################################################################

container_running() {
  local container="$1"

  docker inspect \
    --format '{{.State.Running}}' \
    "$container" 2>/dev/null |
    grep -qx 'true'
}

read_remote_pid() {
  local container="$1"
  local pid_file="$2"

  docker exec "$container" \
    bash -c "cat '$pid_file' 2>/dev/null" |
    tr -d '[:space:]'
}

remote_pid_running() {
  local container="$1"
  local pid="$2"

  [[ -n "$pid" ]] || return 1

  docker exec "$container" \
    bash -c "kill -0 '$pid' 2>/dev/null"
}

stop_remote_pid() {
  local container="$1"
  local pid="$2"
  local signal="${3:-INT}"

  [[ -n "$pid" ]] || return 0

  docker exec "$container" \
    bash -c "kill -s '$signal' '$pid' 2>/dev/null || true" \
    >/dev/null 2>&1 || true
}

wait_remote_pid_exit() {
  local container="$1"
  local pid="$2"
  local attempts="${3:-30}"
  local attempt

  [[ -n "$pid" ]] || return 0

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if ! remote_pid_running "$container" "$pid"; then
      return 0
    fi

    sleep 0.1
  done

  return 1
}

copy_from_container() {
  local container="$1"
  local source="$2"
  local destination="$3"

  if ! docker exec "$container" test -e "$source" 2>/dev/null; then
    warn "File does not exist: $container:$source"
    return 1
  fi

  if docker cp \
    "$container:$source" \
    "$destination" \
    >/dev/null 2>&1; then
    debug "Copied: $container:$source -> $destination"
    return 0
  fi

  error "Failed to copy $container:$source"
  return 1
}

###############################################################################
# Cleanup
###############################################################################

cleanup_current_execution() {
  if [[ "$CLEANUP_IN_PROGRESS" -eq 1 ]]; then
    return
  fi

  CLEANUP_IN_PROGRESS=1

  debug "Stopping processes from the current execution"

  # Stop perf first so it can flush its output file.
  if [[ -n "$CURRENT_PERF_PID" ]]; then
    debug "Stopping responder perf PID $CURRENT_PERF_PID"

    stop_remote_pid \
      "$RESPONDER_CONTAINER" \
      "$CURRENT_PERF_PID" \
      INT

    if ! wait_remote_pid_exit \
      "$RESPONDER_CONTAINER" \
      "$CURRENT_PERF_PID" \
      20; then
      warn "Responder perf did not stop with SIGINT; sending SIGTERM."

      stop_remote_pid \
        "$RESPONDER_CONTAINER" \
        "$CURRENT_PERF_PID" \
        TERM
    fi
  fi

  # Stop tcpdump with SIGINT so PCAP files are finalized.
  if [[ -n "$CURRENT_INIT_TCPDUMP_PID" ]]; then
    stop_remote_pid \
      "$INITIATOR_CONTAINER" \
      "$CURRENT_INIT_TCPDUMP_PID" \
      INT
  fi

  if [[ -n "$CURRENT_RESP_TCPDUMP_PID" ]]; then
    stop_remote_pid \
      "$RESPONDER_CONTAINER" \
      "$CURRENT_RESP_TCPDUMP_PID" \
      INT
  fi

  if [[ -n "$CURRENT_INIT_TCPDUMP_PID" ]]; then
    if ! wait_remote_pid_exit \
      "$INITIATOR_CONTAINER" \
      "$CURRENT_INIT_TCPDUMP_PID" \
      40; then
      warn "Initiator tcpdump did not stop; sending SIGTERM."

      stop_remote_pid \
        "$INITIATOR_CONTAINER" \
        "$CURRENT_INIT_TCPDUMP_PID" \
        TERM
    fi
  fi

  if [[ -n "$CURRENT_RESP_TCPDUMP_PID" ]]; then
    if ! wait_remote_pid_exit \
      "$RESPONDER_CONTAINER" \
      "$CURRENT_RESP_TCPDUMP_PID" \
      40; then
      warn "Responder tcpdump did not stop; sending SIGTERM."

      stop_remote_pid \
        "$RESPONDER_CONTAINER" \
        "$CURRENT_RESP_TCPDUMP_PID" \
        TERM
    fi
  fi

  # Stop ArduPilot if running
  if [[ -n "$CURRENT_ARDUPILOT_PID" ]]; then
    debug "Stopping ArduPilot PID $CURRENT_ARDUPILOT_PID on $ARDUPILOT_CONTAINER"

    stop_remote_pid \
      "$ARDUPILOT_CONTAINER" \
      "$CURRENT_ARDUPILOT_PID" \
      TERM

    if ! wait_remote_pid_exit \
      "$ARDUPILOT_CONTAINER" \
      "$CURRENT_ARDUPILOT_PID" \
      20; then
      warn "ArduPilot did not stop with SIGTERM; sending SIGKILL."

      stop_remote_pid \
        "$ARDUPILOT_CONTAINER" \
        "$CURRENT_ARDUPILOT_PID" \
        KILL
    fi
  fi

  docker exec "$INITIATOR_CONTAINER" sync >/dev/null 2>&1 || true
  docker exec "$RESPONDER_CONTAINER" sync >/dev/null 2>&1 || true

  # Stop the responder last.
  if [[ -n "$CURRENT_RESP_PID" ]]; then
    debug "Stopping responder PID $CURRENT_RESP_PID"

    stop_remote_pid \
      "$RESPONDER_CONTAINER" \
      "$CURRENT_RESP_PID" \
      TERM

    if ! wait_remote_pid_exit \
      "$RESPONDER_CONTAINER" \
      "$CURRENT_RESP_PID" \
      20; then
      warn "Responder did not stop with SIGTERM; sending SIGKILL."

      stop_remote_pid \
        "$RESPONDER_CONTAINER" \
        "$CURRENT_RESP_PID" \
        KILL
    fi
  fi

  CURRENT_INIT_TCPDUMP_PID=""
  CURRENT_RESP_TCPDUMP_PID=""
  CURRENT_RESP_PID=""
  CURRENT_PERF_PID=""
  CURRENT_ARDUPILOT_PID=""

  CLEANUP_IN_PROGRESS=0
}

handle_interrupt() {
  echo
  warn "Execution interrupted by the user."

  cleanup_current_execution

  warn "Results already written were preserved in $OUTPUT_CSV"

  exit 130
}

trap handle_interrupt INT TERM

###############################################################################
# Tshark helpers
###############################################################################

# Enable IPv4 reassembly because large PQ messages may be fragmented.
tshark_read() {
  local pcap="$1"
  shift

  tshark \
    -o ip.defragment:true \
    -r "$pcap" \
    "$@"
}

count_coap_messages() {
  local pcap="$1"

  tshark_read "$pcap" \
    -Y 'coap' \
    -T fields \
    -e frame.number \
    2>/dev/null |
    sed '/^[[:space:]]*$/d' |
    wc -l
}

count_mid() {
  local pcap="$1"
  local mid="$2"

  tshark_read "$pcap" \
    -Y "coap && coap.mid == $mid" \
    -T fields \
    -e frame.number \
    2>/dev/null |
    sed '/^[[:space:]]*$/d' |
    wc -l
}

extract_mid_sequence() {
  local pcap="$1"

  tshark_read "$pcap" \
    -Y 'coap' \
    -T fields \
    -e coap.mid \
    2>/dev/null |
    sed '/^[[:space:]]*$/d' |
    paste -sd' ' -
}

extract_nth_timestamp() {
  local pcap="$1"
  local mid="$2"
  local occurrence="$3"

  tshark_read "$pcap" \
    -Y "coap && coap.mid == $mid" \
    -T fields \
    -e frame.time_epoch \
    2>/dev/null |
    sed '/^[[:space:]]*$/d' |
    sed -n "${occurrence}p"
}

count_ip_fragments() {
  local pcap="$1"

  tshark_read "$pcap" \
    -Y 'ip.flags.mf == 1 || ip.frag_offset > 0' \
    -T fields \
    -e frame.number \
    2>/dev/null |
    sed '/^[[:space:]]*$/d' |
    wc -l
}

extract_task_clock() {
  local perf_file="$1"

  grep 'task-clock' "$perf_file" 2>/dev/null |
    head -n 1 |
    cut -d',' -f1
}

###############################################################################
# CSV
###############################################################################

append_error_row() {
  local execution="$1"
  local status="$2"
  local coap_init="${3:-0}"
  local coap_resp="${4:-0}"
  local fragments_init="${5:-0}"
  local fragments_resp="${6:-0}"

  echo \
    "$execution,ERROR,ERROR,ERROR,ERROR,ERROR,ERROR,ERROR,ERROR,$coap_init,$coap_resp,$fragments_init,$fragments_resp,$status" \
    >>"$OUTPUT_CSV"
}

echo \
  "execution,pi_ms,pr_ms,cpu_total_ms,t1_ms,t2_ms,t3_ms,network_total_ms,handshake_total_ms,coap_init,coap_resp,fragments_init,fragments_resp,status" \
  >"$OUTPUT_CSV"

###############################################################################
# Initial validation
###############################################################################

section "$PROTOCOL experiment runner"

echo "Protocol: $PROTOCOL"
echo "Executions: $N"
echo "Run ID: $RUN_ID"
echo "Run directory: $RUN_DIR"
echo "CSV: $OUTPUT_CSV"
echo "Debug: $DEBUG"
echo "Interface: $IFACE"
echo "Initiator: $INITIATOR_CONTAINER:$INITIATOR_BIN"
echo "Responder: $RESPONDER_CONTAINER:$RESPONDER_BIN"
echo "ArduPilot: $([ "$ENABLE_ARDUPILOT" -eq 1 ] && echo "enabled ($ARDUPILOT_CMD)" || echo "disabled")"
echo "Expected CoAP sequence: $EXPECTED_MID_SEQUENCE"

for command in docker tshark bc sed grep awk paste stat seq; do
  if ! command -v "$command" >/dev/null 2>&1; then
    error "Required command not found: $command"
    exit 1
  fi
done

if ! container_running "$INITIATOR_CONTAINER"; then
  error "Container is not running: $INITIATOR_CONTAINER"
  exit 1
fi

if ! container_running "$RESPONDER_CONTAINER"; then
  error "Container is not running: $RESPONDER_CONTAINER"
  exit 1
fi

if ! docker exec "$INITIATOR_CONTAINER" test -x "$INITIATOR_BIN"; then
  error "Initiator binary not found or not executable:"
  error "  $INITIATOR_CONTAINER:$INITIATOR_BIN"
  exit 1
fi

if ! docker exec "$RESPONDER_CONTAINER" test -x "$RESPONDER_BIN"; then
  error "Responder binary not found or not executable:"
  error "  $RESPONDER_CONTAINER:$RESPONDER_BIN"
  exit 1
fi

if [[ "$ENABLE_ARDUPILOT" -eq 1 ]]; then
  if ! docker exec "$ARDUPILOT_CONTAINER" \
    sh -c "command -v arducopter >/dev/null 2>&1"; then
    error "Required binary 'arducopter' not found in $ARDUPILOT_CONTAINER"
    exit 1
  fi
fi

for container in "$INITIATOR_CONTAINER" "$RESPONDER_CONTAINER"; do
  for command in bash tcpdump perf stdbuf sync; do
    if ! docker exec "$container" \
      sh -c "command -v '$command' >/dev/null 2>&1"; then
      error "Required command '$command' not found in $container"
      exit 1
    fi
  done

  if ! docker exec "$container" \
    ip link show dev "$IFACE" >/dev/null 2>&1; then
    error "Interface '$IFACE' not found in $container"
    exit 1
  fi
done

###############################################################################
# Executions
###############################################################################

for i in $(seq 1 "$N"); do
  section "Execution $i/$N"

  CLEANUP_IN_PROGRESS=0

  CURRENT_INIT_TCPDUMP_PID=""
  CURRENT_RESP_TCPDUMP_PID=""
  CURRENT_RESP_PID=""
  CURRENT_PERF_PID=""

  EXECUTION_ERROR=0

  ###########################################################################
  # Paths
  ###########################################################################

  REMOTE_DIR="/tmp/${PROTOCOL}_experiment_$i"

  PCAP_INIT="$REMOTE_DIR/initiator.pcap"
  PCAP_RESP="$REMOTE_DIR/responder.pcap"

  INIT_PERF="$REMOTE_DIR/initiator_perf.csv"
  RESP_PERF="$REMOTE_DIR/responder_perf.csv"

  RESP_LOG="$REMOTE_DIR/responder.log"

  INIT_TCPDUMP_LOG="$REMOTE_DIR/initiator_tcpdump.log"
  RESP_TCPDUMP_LOG="$REMOTE_DIR/responder_tcpdump.log"
  RESP_PERF_LOG="$REMOTE_DIR/responder_perf.log"

  INIT_TCPDUMP_PID_FILE="$REMOTE_DIR/initiator_tcpdump.pid"
  RESP_TCPDUMP_PID_FILE="$REMOTE_DIR/responder_tcpdump.pid"
  RESP_PID_FILE="$REMOTE_DIR/responder.pid"
  PERF_PID_FILE="$REMOTE_DIR/responder_perf.pid"
  ARDUPILOT_PID_FILE="$REMOTE_DIR/ardupilot.pid"

  ARDUPILOT_LOG="$REMOTE_DIR/ardupilot.log"

  LOCAL_PREFIX="$OUTPUT_DIR/${OUTPUT_STEM}_run_$i"

  LOCAL_INIT_PERF="${LOCAL_PREFIX}_init_perf.csv"
  LOCAL_RESP_PERF="${LOCAL_PREFIX}_resp_perf.csv"

  LOCAL_INIT_PCAP="${LOCAL_PREFIX}_init.pcap"
  LOCAL_RESP_PCAP="${LOCAL_PREFIX}_resp.pcap"

  LOCAL_INIT_LOG="${LOCAL_PREFIX}_init.log"
  LOCAL_RESP_LOG="${LOCAL_PREFIX}_resp.log"

  LOCAL_INIT_TCPDUMP_LOG="${LOCAL_PREFIX}_init_tcpdump.log"
  LOCAL_RESP_TCPDUMP_LOG="${LOCAL_PREFIX}_resp_tcpdump.log"
  LOCAL_RESP_PERF_LOG="${LOCAL_PREFIX}_resp_perf.log"
  LOCAL_ARDUPILOT_LOG="${LOCAL_PREFIX}_ardupilot.log"

  ###########################################################################
  # 0. Cleanup
  ###########################################################################

  debug "[0] Cleaning old processes"

  if [[ "$ENABLE_ARDUPILOT" -eq 1 ]]; then
    docker exec "$ARDUPILOT_CONTAINER" bash -c \
      "pkill -9 -x arducopter 2>/dev/null || true"
  fi

  docker exec "$RESPONDER_CONTAINER" bash -c \
    "pkill -9 -x responder 2>/dev/null || true
         pkill -9 -x tcpdump 2>/dev/null || true
         pkill -9 -x perf 2>/dev/null || true
         true"

  docker exec "$INITIATOR_CONTAINER" bash -c \
    "pkill -9 -x initiator 2>/dev/null || true
         pkill -9 -x tcpdump 2>/dev/null || true
         pkill -9 -x perf 2>/dev/null || true
         true"

  if ! docker exec "$RESPONDER_CONTAINER" bash -c \
    "rm -rf -- '$REMOTE_DIR' && mkdir -p -- '$REMOTE_DIR'"; then
    error "Could not create $REMOTE_DIR in the responder container."
    append_error_row "$i" "RESP_DIR_ERROR"
    continue
  fi

  if ! docker exec "$INITIATOR_CONTAINER" bash -c \
    "rm -rf -- '$REMOTE_DIR' && mkdir -p -- '$REMOTE_DIR'"; then
    error "Could not create $REMOTE_DIR in the initiator container."
    append_error_row "$i" "INIT_DIR_ERROR"
    continue
  fi

  sleep "$SLEEP_CLEANUP"

  ###########################################################################
  # 1. Start tcpdump
  ###########################################################################

  debug "[1] Starting tcpdump"

  if ! docker exec "$INITIATOR_CONTAINER" bash -c \
    "nohup tcpdump \
            --immediate-mode \
            -U \
            -n \
            -s 0 \
            -B 4096 \
            -i '$IFACE' \
            -w '$PCAP_INIT' \
            >'$INIT_TCPDUMP_LOG' 2>&1 &
         echo \$! >'$INIT_TCPDUMP_PID_FILE'"; then
    error "Failed to start tcpdump in the initiator container."
    EXECUTION_ERROR=1
  fi

  if ! docker exec "$RESPONDER_CONTAINER" bash -c \
    "nohup tcpdump \
            --immediate-mode \
            -U \
            -n \
            -s 0 \
            -B 4096 \
            -i '$IFACE' \
            -w '$PCAP_RESP' \
            >'$RESP_TCPDUMP_LOG' 2>&1 &
         echo \$! >'$RESP_TCPDUMP_PID_FILE'"; then
    error "Failed to start tcpdump in the responder container."
    EXECUTION_ERROR=1
  fi

  sleep "$SLEEP_TCPDUMP_START"

  CURRENT_INIT_TCPDUMP_PID="$(
    read_remote_pid \
      "$INITIATOR_CONTAINER" \
      "$INIT_TCPDUMP_PID_FILE" 2>/dev/null || true
  )"

  CURRENT_RESP_TCPDUMP_PID="$(
    read_remote_pid \
      "$RESPONDER_CONTAINER" \
      "$RESP_TCPDUMP_PID_FILE" 2>/dev/null || true
  )"

  if ! remote_pid_running \
    "$INITIATOR_CONTAINER" \
    "$CURRENT_INIT_TCPDUMP_PID"; then
    error "Initiator tcpdump did not start."
    EXECUTION_ERROR=1

    docker exec "$INITIATOR_CONTAINER" \
      bash -c "cat '$INIT_TCPDUMP_LOG' 2>/dev/null || true"
  fi

  if ! remote_pid_running \
    "$RESPONDER_CONTAINER" \
    "$CURRENT_RESP_TCPDUMP_PID"; then
    error "Responder tcpdump did not start."
    EXECUTION_ERROR=1

    docker exec "$RESPONDER_CONTAINER" \
      bash -c "cat '$RESP_TCPDUMP_LOG' 2>/dev/null || true"
  fi

  if [[ "$EXECUTION_ERROR" -eq 1 ]]; then
    cleanup_current_execution
    append_error_row "$i" "TCPDUMP_ERROR"
    continue
  fi

  ###########################################################################
  # 2. Start responder
  ###########################################################################

  debug "[2] Starting responder"

  if ! docker exec "$RESPONDER_CONTAINER" bash -c \
    "nohup stdbuf -oL -eL '$RESPONDER_BIN' \
            >'$RESP_LOG' 2>&1 &
         echo \$! >'$RESP_PID_FILE'"; then
    error "Failed to start responder."
    EXECUTION_ERROR=1
  fi

  sleep "$SLEEP_RESPONDER_START"

  CURRENT_RESP_PID="$(
    read_remote_pid \
      "$RESPONDER_CONTAINER" \
      "$RESP_PID_FILE" 2>/dev/null || true
  )"

  if ! remote_pid_running \
    "$RESPONDER_CONTAINER" \
    "$CURRENT_RESP_PID"; then
    error "Responder did not start or terminated early."

    docker exec "$RESPONDER_CONTAINER" \
      bash -c "cat '$RESP_LOG' 2>/dev/null || true"

    cleanup_current_execution
    append_error_row "$i" "RESPONDER_ERROR"
    continue
  fi

  ###########################################################################
  # 3. Attach perf to responder
  ###########################################################################

  debug "[3] Attaching perf to responder"

  docker exec "$RESPONDER_CONTAINER" bash -c \
    "nohup perf stat \
            -e task-clock \
            -x, \
            -p '$CURRENT_RESP_PID' \
            -o '$RESP_PERF' \
            >'$RESP_PERF_LOG' 2>&1 &
         echo \$! >'$PERF_PID_FILE'"

  sleep "$SLEEP_PERF_ATTACH"

  CURRENT_PERF_PID="$(
    read_remote_pid \
      "$RESPONDER_CONTAINER" \
      "$PERF_PID_FILE" 2>/dev/null || true
  )"

  if ! remote_pid_running \
    "$RESPONDER_CONTAINER" \
    "$CURRENT_PERF_PID"; then
    warn "Responder perf did not remain active."
  fi

  ###########################################################################
  # 3.5. Start ArduPilot on initiator
  ###########################################################################

  if [[ "$ENABLE_ARDUPILOT" -eq 1 ]]; then
    debug "[3.5] Starting ArduPilot on initiator"

    if ! docker exec "$ARDUPILOT_CONTAINER" bash -c \
      "cd '$REMOTE_DIR' && nohup $ARDUPILOT_CMD \
              >'$ARDUPILOT_LOG' 2>&1 &
           echo \$! >'$ARDUPILOT_PID_FILE'"; then
      error "Failed to start ArduPilot on initiator container."
      EXECUTION_ERROR=1
    fi

    sleep "$SLEEP_ARDUPILOT_START"

    CURRENT_ARDUPILOT_PID="$(
      read_remote_pid \
        "$ARDUPILOT_CONTAINER" \
        "$ARDUPILOT_PID_FILE" 2>/dev/null || true
    )"

    if ! remote_pid_running \
      "$ARDUPILOT_CONTAINER" \
      "$CURRENT_ARDUPILOT_PID"; then
      error "ArduPilot did not start or terminated early."

      docker exec "$ARDUPILOT_CONTAINER" \
        bash -c "cat '$ARDUPILOT_LOG' 2>/dev/null || true"

      cleanup_current_execution
      append_error_row "$i" "ARDUPILOT_ERROR"
      continue
    fi
  fi

  ###########################################################################
  # 4. Run initiator
  ###########################################################################

  debug "[4] Running initiator"

  docker exec "$INITIATOR_CONTAINER" bash -c \
    "perf stat \
            -e task-clock \
            -x, \
            -o '$INIT_PERF' \
            -- '$INITIATOR_BIN'" \
    >"$LOCAL_INIT_LOG" 2>&1

  INIT_EXIT_CODE=$?

  if [[ "$INIT_EXIT_CODE" -eq 0 ]]; then
    ok "Initiator completed successfully."
  else
    error "Initiator exited with code $INIT_EXIT_CODE."
    EXECUTION_ERROR=1
  fi

  ###########################################################################
  # 5. Drain capture buffers
  ###########################################################################

  sleep "$SLEEP_CAPTURE_DRAIN"

  ###########################################################################
  # 6. Stop processes
  ###########################################################################

  cleanup_current_execution

  sleep "$SLEEP_PERF_STOP"
  sleep "$SLEEP_TCPDUMP_STOP"

  ###########################################################################
  # 7. Copy artifacts
  ###########################################################################

  debug "[7] Copying artifacts"

  copy_from_container \
    "$INITIATOR_CONTAINER" \
    "$INIT_PERF" \
    "$LOCAL_INIT_PERF" ||
    EXECUTION_ERROR=1

  copy_from_container \
    "$RESPONDER_CONTAINER" \
    "$RESP_PERF" \
    "$LOCAL_RESP_PERF" ||
    EXECUTION_ERROR=1

  copy_from_container \
    "$INITIATOR_CONTAINER" \
    "$PCAP_INIT" \
    "$LOCAL_INIT_PCAP" ||
    EXECUTION_ERROR=1

  copy_from_container \
    "$RESPONDER_CONTAINER" \
    "$PCAP_RESP" \
    "$LOCAL_RESP_PCAP" ||
    EXECUTION_ERROR=1

  copy_from_container \
    "$RESPONDER_CONTAINER" \
    "$RESP_LOG" \
    "$LOCAL_RESP_LOG" || true

  copy_from_container \
    "$INITIATOR_CONTAINER" \
    "$INIT_TCPDUMP_LOG" \
    "$LOCAL_INIT_TCPDUMP_LOG" || true

  copy_from_container \
    "$RESPONDER_CONTAINER" \
    "$RESP_TCPDUMP_LOG" \
    "$LOCAL_RESP_TCPDUMP_LOG" || true

  copy_from_container \
    "$RESPONDER_CONTAINER" \
    "$RESP_PERF_LOG" \
    "$LOCAL_RESP_PERF_LOG" || true

  if [[ "$ENABLE_ARDUPILOT" -eq 1 ]]; then
    copy_from_container \
      "$ARDUPILOT_CONTAINER" \
      "$ARDUPILOT_LOG" \
      "$LOCAL_ARDUPILOT_LOG" || true
  fi

  ###########################################################################
  # 8. Validate PCAP files
  ###########################################################################

  INIT_PCAP_SIZE="$(
    stat -c '%s' "$LOCAL_INIT_PCAP" 2>/dev/null || echo 0
  )"

  RESP_PCAP_SIZE="$(
    stat -c '%s' "$LOCAL_RESP_PCAP" 2>/dev/null || echo 0
  )"

  if [[ "$INIT_PCAP_SIZE" -le 24 ]]; then
    error "Initiator PCAP is empty or contains only the header."
    EXECUTION_ERROR=1
  fi

  if [[ "$RESP_PCAP_SIZE" -le 24 ]]; then
    error "Responder PCAP is empty or contains only the header."
    EXECUTION_ERROR=1
  fi

  if [[ "$EXECUTION_ERROR" -eq 1 ]]; then
    append_error_row "$i" "ARTIFACT_ERROR"
    warn "Artifacts were preserved with prefix $LOCAL_PREFIX"
    continue
  fi

  ###########################################################################
  # 9. Analyze CoAP
  ###########################################################################

  COAP_INIT="$(count_coap_messages "$LOCAL_INIT_PCAP")"
  COAP_RESP="$(count_coap_messages "$LOCAL_RESP_PCAP")"

  MID0_INIT="$(count_mid "$LOCAL_INIT_PCAP" 0)"
  MID0_RESP="$(count_mid "$LOCAL_RESP_PCAP" 0)"

  MID1_INIT="$(count_mid "$LOCAL_INIT_PCAP" 1)"
  MID1_RESP="$(count_mid "$LOCAL_RESP_PCAP" 1)"

  SEQUENCE_INIT="$(extract_mid_sequence "$LOCAL_INIT_PCAP")"
  SEQUENCE_RESP="$(extract_mid_sequence "$LOCAL_RESP_PCAP")"

  FRAGMENTS_INIT="$(count_ip_fragments "$LOCAL_INIT_PCAP")"
  FRAGMENTS_RESP="$(count_ip_fragments "$LOCAL_RESP_PCAP")"

  debug "Initiator CoAP messages: $COAP_INIT"
  debug "Responder CoAP messages: $COAP_RESP"
  debug "Initiator sequence: [$SEQUENCE_INIT]"
  debug "Responder sequence: [$SEQUENCE_RESP]"
  debug "Initiator fragments: $FRAGMENTS_INIT"
  debug "Responder fragments: $FRAGMENTS_RESP"

  VALID_CAPTURE=1

  if [[ "$COAP_INIT" -ne 3 || "$COAP_RESP" -ne 3 ]]; then
    error "Expected 3 CoAP messages in each capture."
    VALID_CAPTURE=0
  fi

  if [[ "$MID0_INIT" -ne 2 || "$MID0_RESP" -ne 2 ]]; then
    error "Expected 2 MID 0 messages in each capture."
    VALID_CAPTURE=0
  fi

  if [[ "$MID1_INIT" -ne 1 || "$MID1_RESP" -ne 1 ]]; then
    error "Expected 1 MID 1 message in each capture."
    VALID_CAPTURE=0
  fi

  if [[ "$SEQUENCE_INIT" != "$EXPECTED_MID_SEQUENCE" ]]; then
    error "Unexpected initiator sequence: [$SEQUENCE_INIT]"
    VALID_CAPTURE=0
  fi

  if [[ "$SEQUENCE_RESP" != "$EXPECTED_MID_SEQUENCE" ]]; then
    error "Unexpected responder sequence: [$SEQUENCE_RESP]"
    VALID_CAPTURE=0
  fi

  if [[ "$VALID_CAPTURE" -eq 0 ]]; then
    append_error_row \
      "$i" \
      "CAPTURE_ERROR" \
      "$COAP_INIT" \
      "$COAP_RESP" \
      "$FRAGMENTS_INIT" \
      "$FRAGMENTS_RESP"

    warn "Artifacts were preserved with prefix $LOCAL_PREFIX"
    continue
  fi

  ok "Valid CoAP sequence: $EXPECTED_MID_SEQUENCE"

  ###########################################################################
  # 10. Extract timestamps
  ###########################################################################

  MSG1_OUT="$(extract_nth_timestamp "$LOCAL_INIT_PCAP" 0 1)"
  MSG1_IN="$(extract_nth_timestamp "$LOCAL_RESP_PCAP" 0 1)"

  MSG2_OUT="$(extract_nth_timestamp "$LOCAL_RESP_PCAP" 0 2)"
  MSG2_IN="$(extract_nth_timestamp "$LOCAL_INIT_PCAP" 0 2)"

  MSG3_OUT="$(extract_nth_timestamp "$LOCAL_INIT_PCAP" 1 1)"
  MSG3_IN="$(extract_nth_timestamp "$LOCAL_RESP_PCAP" 1 1)"

  if [[ -z "$MSG1_OUT" ||
    -z "$MSG1_IN" ||
    -z "$MSG2_OUT" ||
    -z "$MSG2_IN" ||
    -z "$MSG3_OUT" ||
    -z "$MSG3_IN" ]]; then
    error "Could not extract all timestamps."

    append_error_row \
      "$i" \
      "TIMESTAMP_ERROR" \
      "$COAP_INIT" \
      "$COAP_RESP" \
      "$FRAGMENTS_INIT" \
      "$FRAGMENTS_RESP"

    continue
  fi

  ###########################################################################
  # 11. Extract perf results
  ###########################################################################

  PI="$(extract_task_clock "$LOCAL_INIT_PERF")"
  PR="$(extract_task_clock "$LOCAL_RESP_PERF")"

  if [[ -z "$PI" || -z "$PR" ]]; then
    error "Could not extract task-clock."

    append_error_row \
      "$i" \
      "PERF_ERROR" \
      "$COAP_INIT" \
      "$COAP_RESP" \
      "$FRAGMENTS_INIT" \
      "$FRAGMENTS_RESP"

    continue
  fi

  ###########################################################################
  # 12. Calculate times
  ###########################################################################

  CPU_TOTAL="$(echo "$PI + $PR" | bc -l)"

  T1_MS="$(echo "($MSG1_IN - $MSG1_OUT) * 1000" | bc -l)"
  T2_MS="$(echo "($MSG2_IN - $MSG2_OUT) * 1000" | bc -l)"
  T3_MS="$(echo "($MSG3_IN - $MSG3_OUT) * 1000" | bc -l)"

  NETWORK_TOTAL_MS="$(
    echo "$T1_MS + $T2_MS + $T3_MS" |
      bc -l
  )"

  HANDSHAKE_TOTAL_MS="$(
    echo "$CPU_TOTAL + $NETWORK_TOTAL_MS" |
      bc -l
  )"

  ###########################################################################
  # 13. Reject negative network times
  ###########################################################################

  NEGATIVE=0

  for value in "$T1_MS" "$T2_MS" "$T3_MS"; do
    if [[ "$(echo "$value < 0" | bc -l)" -eq 1 ]]; then
      NEGATIVE=1
    fi
  done

  if [[ "$NEGATIVE" -eq 1 ]]; then
    error "Negative time: t1=$T1_MS t2=$T2_MS t3=$T3_MS"

    echo \
      "$i,$PI,$PR,$CPU_TOTAL,ERROR,ERROR,ERROR,ERROR,ERROR,$COAP_INIT,$COAP_RESP,$FRAGMENTS_INIT,$FRAGMENTS_RESP,NEGATIVE_TIME" \
      >>"$OUTPUT_CSV"

    warn "Artifacts were preserved with prefix $LOCAL_PREFIX"
    continue
  fi

  ###########################################################################
  # 14. Save result
  ###########################################################################

  echo \
    "$i,$PI,$PR,$CPU_TOTAL,$T1_MS,$T2_MS,$T3_MS,$NETWORK_TOTAL_MS,$HANDSHAKE_TOTAL_MS,$COAP_INIT,$COAP_RESP,$FRAGMENTS_INIT,$FRAGMENTS_RESP,OK" \
    >>"$OUTPUT_CSV"

  ok "Execution $i completed successfully."

  ###########################################################################
  # 15. Remove temporary artifacts from successful executions
  ###########################################################################

  if [[ "$DEBUG" != "1" ]]; then
    rm -f \
      "$LOCAL_INIT_PERF" \
      "$LOCAL_RESP_PERF" \
      "$LOCAL_INIT_PCAP" \
      "$LOCAL_RESP_PCAP" \
      "$LOCAL_INIT_LOG" \
      "$LOCAL_RESP_LOG" \
      "$LOCAL_INIT_TCPDUMP_LOG" \
      "$LOCAL_RESP_TCPDUMP_LOG" \
      "$LOCAL_RESP_PERF_LOG" \
      "$LOCAL_ARDUPILOT_LOG"
  else
    info "Artifacts preserved with prefix: $LOCAL_PREFIX"
  fi
done

###############################################################################
# Summary
###############################################################################

section "Experiments completed"

TOTAL_OK="$(
  awk -F',' \
    'NR > 1 && $NF == "OK" { count++ }
         END { print count + 0 }' \
    "$OUTPUT_CSV"
)"

TOTAL_ERROR="$(
  awk -F',' \
    'NR > 1 && $NF != "OK" { count++ }
         END { print count + 0 }' \
    "$OUTPUT_CSV"
)"

echo "Protocol: $PROTOCOL"
echo "Run ID: $RUN_ID"
echo "Run directory: $RUN_DIR"
echo "Results: $OUTPUT_CSV"
echo "Successful executions: $TOTAL_OK"
echo "Failed executions: $TOTAL_ERROR"

if [[ "$TOTAL_ERROR" -gt 0 ]]; then
  warn "Some executions failed. Check the CSV status column."
  exit 1
fi

ok "All executions completed successfully."
