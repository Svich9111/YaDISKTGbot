#!/bin/bash
set -e

echo "🤖 Yandex Disk Telegram Bot - Local Development Runner"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt --quiet

# Check .env file
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found. Copying from .env.example..."
    cp .env.example .env
    echo "❌ Please edit .env with your credentials and run this script again."
    exit 1
fi

# Run healthcheck
echo "🔍 Running healthcheck..."
python3 healthcheck.py

if [ $? -eq 0 ]; then
    echo "✅ Healthcheck passed."

    # Stop other local bot processes to avoid TelegramConflictError
    EXISTING_PIDS=$(ps aux | grep "[p]ython3.*main.py" | awk '{print $2}')
    if [ -n "$EXISTING_PIDS" ]; then
        echo "🛑 Stopping existing bot processes: $EXISTING_PIDS"
        kill $EXISTING_PIDS 2>/dev/null || true
        sleep 2
    fi

    echo "🚀 Starting bot..."
    python3 main.py
else
    echo "❌ Healthcheck failed. Please check logs."
    exit 1
fi