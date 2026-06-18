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
    if [ -f "$COMPOSE_FILE" ]; then
        MODE="docker"
    else
        MODE="host"
    fi
fi

echo "Step 1 of 4: Checking your setup"
echo
echo "  Detected mode: $MODE"
if [ "$MODE" = "host" ]; then
    echo "  This will connect to OpenEMR already installed on the server."
else
    echo "  This will start the Docker lab stack from $COMPOSE_FILE."
fi

echo
echo "What will happen next:"
echo "  1. OpenEMR will be prepared for the student lab workflow"
echo "  2. Sample lab tests will be added"
echo "  3. The order form will be softened for teaching use"
echo "  4. You will get a short first-login walkthrough"

pause_for_student "Press Enter when you are ready to continue."

if [ "$MODE" = "host" ]; then
    echo
    echo "Step 2 of 4: Preparing your existing OpenEMR install"
    echo
    python3 ontario_lab_turnkey.py --install
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
    echo "Step 2 of 4: Starting the Docker lab"
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
    echo "Step 3 of 4: Waiting for OpenEMR to become healthy..."
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
    echo "Step 4 of 4: Configuring the student lab"
    echo
    python3 ontario_lab_turnkey.py --install
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
echo "  3. Create a new order"
echo "  4. Pick a sample test"
echo "  5. Wait a few seconds"
echo "  6. Check Procedures -> Pending Review -> Procedure Results"
echo
echo "For more help, see: INSTALL.md"
