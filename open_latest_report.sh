#!/bin/bash
# Opens the most recent HTML report in your default browser
REPORTS_DIR="$(dirname "$0")/reports"

LATEST=$(ls -t "$REPORTS_DIR"/*.html 2>/dev/null | head -1)

if [ -z "$LATEST" ]; then
    echo "No HTML reports found in $REPORTS_DIR"
    exit 1
fi

echo "Opening: $(basename "$LATEST")"
open "$LATEST"
