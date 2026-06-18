#!/bin/bash
set -euo pipefail

echo
echo "============================================"
echo "  Hit4EMR Lab Simulator - Student Installer"
echo "============================================"
echo
echo "This installer is guided for first-time students."
echo "It will keep the workflow simple and readable."
echo

MODE="${ONTARIO_LAB_MODE:-}"
COMPOSE_FILE="${ONTARIO_LAB_COMPOSE:-docker-compose-8.0.x.yml}"

detect_host_style() {
    if [ -n "${AWS_EXECUTION_ENV:-}" ]; then
        echo "aws"
        return
    fi

    if [ -r /sys/class/dmi/id/product_name ] && grep -qi 'ec2' /sys/class/dmi/id/product_name 2>/dev/null; then
        echo "aws"
        return
    fi

    if [ -r /sys/devices/virtual/dmi/id/product_name ] && grep -qi 'ec2' /sys/devices/virtual/dmi/id/product_name 2>/dev/null; then
        echo "aws"
        return
    fi

    echo "local"
}

pause_for_student() {
    local prompt="$1"
    echo
    read -r -p "$prompt" _
}

render_progress() {
    local current="$1"
    local total="$2"
    local status="$3"
    local width=24
    local filled=$((current * width / total))
    local empty=$((width - filled))
    local pct=$((current * 100 / total))
    local bar
    bar="$(printf '%*s' "$filled" '' | tr ' ' '#')"
    bar="$bar$(printf '%*s' "$empty" '' | tr ' ' '-')"
    printf '\r   [%s] %3d%% OpenEMR status: %s' "$bar" "$pct" "$status"
}

if [ -z "$MODE" ] && { [ -n "${OPENEMR_ROOT:-}" ] || [ -n "${OPENEMR_SITES:-}" ] || [ -n "${OPENEMR_SQLCONF:-}" ]; }; then
    MODE="host"
fi

if [ -z "$MODE" ]; then
    MODE="host"
fi

HOST_STYLE="$(detect_host_style)"
DEFAULT_CHOICE="2"
if [ "$HOST_STYLE" = "aws" ]; then
    DEFAULT_CHOICE="1"
fi

echo "Step 1 of 5: Choose your setup"
echo
if [ "$HOST_STYLE" = "aws" ]; then
    echo "  Detected environment: AWS-style Ubuntu host"
else
    echo "  Detected environment: local Ubuntu host"
fi

echo
echo "Choose the setup that matches this machine:"
echo "  1. AWS EC2 Ubuntu host install"
echo "  2. Local Ubuntu host install like ElCurioso"
if [ -f "$COMPOSE_FILE" ]; then
    echo "  3. Docker sandbox on this machine"
fi

read -r -p "Enter choice [${DEFAULT_CHOICE}]: " SETUP_CHOICE
SETUP_CHOICE="${SETUP_CHOICE:-$DEFAULT_CHOICE}"

case "$SETUP_CHOICE" in
    1)
        MODE="host"
        HOST_STYLE="aws"
        ;;
    2)
        MODE="host"
        HOST_STYLE="local"
        ;;
    3)
        if [ -f "$COMPOSE_FILE" ]; then
            MODE="docker"
        else
            echo
            echo "Docker mode is not available because $COMPOSE_FILE was not found."
            echo "Choose 1 or 2 instead."
            exit 1
        fi
        ;;
    *)
        echo
        echo "Invalid choice. Please rerun the installer and choose 1, 2, or 3."
        exit 1
        ;;
esac

echo
echo "  Selected mode: $MODE"
if [ "$MODE" = "host" ]; then
    if [ "$HOST_STYLE" = "aws" ]; then
        echo "  This will prepare the OpenEMR install on the AWS EC2 host."
    else
        echo "  This will prepare the OpenEMR install on the local Ubuntu server."
    fi
else
    echo "  This will start the Docker lab stack from $COMPOSE_FILE."
fi

echo
echo "What will happen next:"
echo "  1. OpenEMR will be prepared for the student lab workflow"
echo "  2. Sample lab tests will be added"
echo "  3. The order form will be softened for teaching use"
echo "  4. You will get a short first-login walkthrough"

if ! python3 - <<'PY' >/dev/null 2>&1
import pymysql
PY
then
    echo
    echo "ERROR: Python module 'pymysql' is missing."
    if python3 -m pip install --user pymysql >/dev/null 2>&1; then
        echo "  Installed pymysql for the current user."
    else
        echo "  Automatic install failed."
        echo "  Install it once as the server admin, then rerun the installer."
        echo "  For example: python3 -m pip install --user pymysql"
        exit 1
    fi
fi

pause_for_student "Press Enter when you are ready to continue."

if [ "$MODE" = "host" ]; then
    echo
    echo "Step 2 of 5: Preparing your existing OpenEMR install"
    echo
    python3 ontario_lab_turnkey.py --install

    echo
    echo "Step 3 of 5: Starting the background simulator"
    SIM_LOG="./hit4emr-simulator.log"
    if pgrep -f "ontario_lab_turnkey.py" >/dev/null 2>&1; then
        echo
        echo "Simulator is already running."
    else
        echo
        echo "Starting the background simulator..."
        nohup python3 ontario_lab_turnkey.py >"$SIM_LOG" 2>&1 &
        echo "  Log file: $SIM_LOG"
    fi
else
    if [ ! -f "$COMPOSE_FILE" ]; then
        echo
        echo "ERROR: $COMPOSE_FILE not found."
        echo "To use Docker mode, add the compose file beside this installer."
        echo "Otherwise set ONTARIO_LAB_MODE=host and point the script at your OpenEMR install."
        exit 1
    fi

    if command -v docker-compose >/dev/null 2>&1; then
        DOCKER_COMPOSE_CMD="docker-compose"
    elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        DOCKER_COMPOSE_CMD="docker compose"
    else
        echo
        echo "ERROR: Docker Compose not found."
        echo "Install Docker and Docker Compose first, or switch to host mode."
        exit 1
    fi

    if ! docker info >/dev/null 2>&1; then
        echo
        echo "ERROR: Docker is not running."
        echo "Start Docker and run the installer again."
        exit 1
    fi

    echo
    echo "Step 2 of 5: Starting the Docker lab"
    echo "   - OpenEMR database"
    echo "   - OpenEMR web application"
    echo "   - Lab simulator"
    echo
    echo "   Building and starting services..."

    compose_log="$(mktemp)"
    compose_exit="$(mktemp)"
    cleanup() {
        rm -f "$compose_log" "$compose_exit"
    }
    trap cleanup EXIT

    ( $DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" up -d --build >"$compose_log" 2>&1; echo $? >"$compose_exit" ) &
    compose_pid=$!
    spinner='|/-\'
    spinner_index=0
    while kill -0 "$compose_pid" 2>/dev/null; do
        frame="${spinner:$((spinner_index % 4)):1}"
        printf '\r   Building and starting services %s' "$frame"
        sleep 0.2
        spinner_index=$((spinner_index + 1))
    done
    wait "$compose_pid" || true
    compose_status="$(cat "$compose_exit" 2>/dev/null || echo 1)"
    if [ "$compose_status" -ne 0 ]; then
        echo
        echo "ERROR: Docker Compose failed."
        echo "Here are the last lines from the startup log:"
        tail -n 40 "$compose_log" || cat "$compose_log"
        exit 1
    fi

    printf '\r   Building and starting services complete.      \n'

    echo
    echo "Step 3 of 5: Waiting for OpenEMR to become healthy..."
    echo "   This can take a few minutes."
    echo
    status="starting"
    max_attempts=60
    for attempt in $(seq 1 "$max_attempts"); do
        status="$(docker inspect -f '{{.State.Health.Status}}' openemr-8x-1 2>/dev/null || echo starting)"
        render_progress "$attempt" "$max_attempts" "$status"
        if [ "$status" = "healthy" ]; then
            echo
            break
        fi
        sleep 5
    done

    if [ "$status" != "healthy" ]; then
        echo
        echo "ERROR: OpenEMR did not become healthy."
        echo "Check the container logs with: $DOCKER_COMPOSE_CMD -f $COMPOSE_FILE logs --tail=100"
        exit 1
    fi

    echo
    echo "Step 4 of 5: Configuring the student lab"
    echo
    python3 ontario_lab_turnkey.py --install

    echo
    echo "Step 5 of 5: Starting the background simulator"
    SIM_LOG="./hit4emr-simulator.log"
    if pgrep -f "ontario_lab_turnkey.py" >/dev/null 2>&1; then
        echo
        echo "Simulator is already running."
    else
        echo
        echo "Starting the background simulator..."
        nohup python3 ontario_lab_turnkey.py >"$SIM_LOG" 2>&1 &
        echo "  Log file: $SIM_LOG"
    fi
fi

echo
echo "============================================"
echo "  Installation Complete"
echo "============================================"
echo
echo "Login:"
echo "  Username: admin"
echo "  Password: pass"
echo
echo "Available lab tests:"
echo "  - 6690-2  WBC"
echo "  - 718-7   Hemoglobin"
echo "  - 1558-6  Glucose (Fasting)"
echo "  - 3016-3  TSH"
echo "  - 2093-3  Total Cholesterol"
echo "  - 4548-4  Hemoglobin A1c"
echo
echo "Available diagnosis codes:"
echo "  - R79.89  Other specified abnormal findings of blood chemistry"
echo "  - D64.9   Anemia, unspecified"
echo "  - E11.9   Type 2 diabetes mellitus without complications"
echo "  - E78.5   Hyperlipidemia, unspecified"
echo "  - E03.9   Hypothyroidism, unspecified"
echo "  - R73.03  Prediabetes"
echo "  - R53.83  Other fatigue"
echo "  - N18.9   Chronic kidney disease, unspecified"
echo "  - Z13.1   Encounter for screening for diabetes mellitus"
echo "  - Z13.220 Encounter for screening for lipoid disorders"
echo
echo "Student workflow:"
echo "  1. Open the patient chart"
echo "  2. Go to Encounter -> Orders -> Procedure Orders"
echo "  3. Open the new procedure order form"
echo "  4. Set Default Procedure Type = Laboratory Test"
echo "  5. Fill the required fields:"
echo "     - Primary Diagnosis"
echo "     - Specimen Collection Date, if shown"
echo "     - Billing Type, if shown"
echo "  6. Pick a sample test"
echo "  7. Save the order"
echo "  8. Wait a few seconds"
echo "  9. Check Procedures -> Pending Review -> Procedure Results"
echo
echo "Simulator log:"
echo "  hit4emr-simulator.log"
echo
if [ "$MODE" = "host" ] && [ "$HOST_STYLE" = "aws" ]; then
    echo "AWS note:"
    echo "  Use the EC2 public IP or public DNS name to reach OpenEMR."
elif [ "$MODE" = "host" ]; then
    echo "Local Ubuntu note:"
    echo "  Use the local network address for ElCurioso to reach OpenEMR."
fi
echo
echo "For more help, see: INSTALL.md"
