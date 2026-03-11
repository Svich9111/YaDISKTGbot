#!/bin/bash

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run healthcheck
echo "Running healthcheck..."
python3 healthcheck.py

if [ $? -eq 0 ]; then
    echo "Healthcheck passed."

    # Try to stop other local bot processes using this project
    # (to avoid TelegramConflictError from multiple getUpdates)
    EXISTING_PIDS=$(ps aux | grep "[p]ython3.*main.py" | awk '{print $2}')
    if [ -n "$EXISTING_PIDS" ]; then
        echo "Stopping existing bot processes: $EXISTING_PIDS"
        kill $EXISTING_PIDS 2>/dev/null || true
        sleep 2
    fi

    echo "Starting bot..."
    # Run bot with auto-restart
    while true; do
        python3 main.py
        echo "Bot crashed. Restarting in 5 seconds..."
        sleep 5
    done
else
    echo "Healthcheck failed. Please check logs."
    exit 1
fi