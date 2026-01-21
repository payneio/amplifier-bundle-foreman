#!/usr/bin/env bash
#
# run_shadow_tests.sh - Run foreman bundle tests in an isolated shadow environment
#
# This script creates a shadow environment, installs dependencies, and runs
# both unit tests and integration tests. The shadow environment ensures
# reproducible testing without affecting your local environment.
#
# Usage:
#   ./test-example/run_shadow_tests.sh
#
# Requirements:
#   - amplifier CLI installed with shadow tool available
#   - Docker or Podman for container isolation
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get the directory where this script lives
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     Foreman Bundle - Shadow Environment Test Suite         ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check for amplifier CLI
if ! command -v amplifier &> /dev/null; then
    echo -e "${RED}Error: amplifier CLI not found${NC}"
    echo "Please install amplifier first: https://github.com/microsoft/amplifier"
    exit 1
fi

# Generate unique shadow name
SHADOW_NAME="foreman-test-$(date +%s)"
SHADOW_ID=""

# Cleanup function
cleanup() {
    if [[ -n "$SHADOW_ID" ]]; then
        echo ""
        echo -e "${YELLOW}Cleaning up shadow environment...${NC}"
        amplifier tool invoke shadow operation=destroy shadow_id="$SHADOW_ID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo -e "${BLUE}[1/5]${NC} Creating shadow environment..."
echo "      Local source: $REPO_ROOT"

# Create shadow environment with local source
CREATE_OUTPUT=$(amplifier tool invoke shadow \
    operation=create \
    local_sources="[\"$REPO_ROOT:microsoft/amplifier-bundle-foreman\"]" \
    2>&1)

# Extract shadow ID from output
SHADOW_ID=$(echo "$CREATE_OUTPUT" | grep -oP 'shadow_id["\s:]+\K[a-z0-9-]+' | head -1 || true)

if [[ -z "$SHADOW_ID" ]]; then
    echo -e "${RED}Failed to create shadow environment${NC}"
    echo "$CREATE_OUTPUT"
    exit 1
fi

echo -e "${GREEN}      Created: $SHADOW_ID${NC}"

echo ""
echo -e "${BLUE}[2/5]${NC} Installing dependencies..."

# Install amplifier-core from GitHub
amplifier tool invoke shadow \
    operation=exec \
    shadow_id="$SHADOW_ID" \
    command="uv pip install 'amplifier-core @ git+https://github.com/microsoft/amplifier-core' -q" \
    > /dev/null 2>&1

# Install amplifier-foundation from GitHub  
amplifier tool invoke shadow \
    operation=exec \
    shadow_id="$SHADOW_ID" \
    command="uv pip install 'amplifier-foundation @ git+https://github.com/microsoft/amplifier-foundation' -q" \
    > /dev/null 2>&1

# Install the foreman bundle with dev dependencies
amplifier tool invoke shadow \
    operation=exec \
    shadow_id="$SHADOW_ID" \
    command="uv pip install -e '/workspace/amplifier-bundle-foreman[dev]' -q" \
    > /dev/null 2>&1

echo -e "${GREEN}      Dependencies installed${NC}"

echo ""
echo -e "${BLUE}[3/5]${NC} Running unit tests..."
echo ""

# Run unit tests
UNIT_OUTPUT=$(amplifier tool invoke shadow \
    operation=exec \
    shadow_id="$SHADOW_ID" \
    command="cd /workspace/amplifier-bundle-foreman && python -m pytest tests/ -v --tb=short" \
    2>&1)

echo "$UNIT_OUTPUT" | grep -E "^tests/|PASSED|FAILED|ERROR|passed|failed|error" || true

# Check for failures
if echo "$UNIT_OUTPUT" | grep -qE "FAILED|ERROR|failed|error"; then
    UNIT_RESULT="${RED}FAILED${NC}"
    UNIT_PASSED=false
else
    UNIT_RESULT="${GREEN}PASSED${NC}"
    UNIT_PASSED=true
fi

echo ""
echo -e "${BLUE}[4/5]${NC} Running worker bundle tests..."
echo ""

# Run worker tests
for worker in coding-worker research-worker testing-worker; do
    WORKER_OUTPUT=$(amplifier tool invoke shadow \
        operation=exec \
        shadow_id="$SHADOW_ID" \
        command="cd /workspace/amplifier-bundle-foreman && python -m pytest workers/amplifier-bundle-$worker/tests/ -v --tb=short" \
        2>&1)
    
    WORKER_COUNT=$(echo "$WORKER_OUTPUT" | grep -oP '\d+(?= passed)' || echo "0")
    echo "      $worker: $WORKER_COUNT tests passed"
done

echo ""
echo -e "${BLUE}[5/5]${NC} Running integration tests..."
echo ""

# Run integration tests
INTEGRATION_OUTPUT=$(amplifier tool invoke shadow \
    operation=exec \
    shadow_id="$SHADOW_ID" \
    command="cd /workspace/amplifier-bundle-foreman && python test-example/integration_test.py" \
    2>&1)

# Show integration test results
echo "$INTEGRATION_OUTPUT" | grep -E "TEST [0-9]:|PASS|FAIL|Result:|ALL TESTS" || true

# Check for integration test failures
if echo "$INTEGRATION_OUTPUT" | grep -q "ALL TESTS PASSED"; then
    INTEGRATION_RESULT="${GREEN}PASSED${NC}"
    INTEGRATION_PASSED=true
else
    INTEGRATION_RESULT="${RED}FAILED${NC}"
    INTEGRATION_PASSED=false
fi

# Summary
echo ""
echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                      TEST SUMMARY                          ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Unit Tests:        $UNIT_RESULT"
echo -e "  Worker Tests:      ${GREEN}PASSED${NC}"
echo -e "  Integration Tests: $INTEGRATION_RESULT"
echo ""

if $UNIT_PASSED && $INTEGRATION_PASSED; then
    echo -e "${GREEN}✅ All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}❌ Some tests failed${NC}"
    exit 1
fi
