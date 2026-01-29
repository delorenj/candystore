#!/bin/bash
# Quick start script for Candystore

set -e

echo "🍬 Starting Candystore..."

# Check if .env exists
if [ ! -f .env ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
fi

# Install dependencies
echo "Installing dependencies..."
uv sync

# Initialize database
echo "Initializing database..."
uv run candystore init-db

# Start service
echo "Starting Candystore service..."
echo "  - Consumer: RabbitMQ wildcard subscription"
echo "  - API: http://localhost:8683"
echo "  - Metrics: http://localhost:9090/metrics"
echo ""
uv run candystore serve
