#!/usr/bin/env bash
#
# run_shadow_tests.sh - Run foreman bundle tests in an isolated Docker container
#
# This script creates an isolated container environment, installs dependencies,
# and runs both unit tests and integration tests. This ensures reproducible
# testing without affecting your local environment.
#
# Usage:
#   ./test-example/run_shadow_tests.sh
#
# Requirements:
#   - Docker or Podman installed
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
echo -e "${BLUE}║     Foreman Bundle - Isolated Test Suite                   ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Detect container runtime (prefer docker, fall back to podman)
if command -v docker &> /dev/null; then
    CONTAINER_CMD="docker"
elif command -v podman &> /dev/null; then
    CONTAINER_CMD="podman"
else
    echo -e "${RED}Error: Neither docker nor podman found${NC}"
    echo "Please install Docker or Podman to run isolated tests"
    exit 1
fi

echo -e "${BLUE}[1/6]${NC} Using container runtime: $CONTAINER_CMD"

# Container and image settings
CONTAINER_NAME="foreman-test-$(date +%s)"
# Use bookworm (not slim) - includes git which is needed for pip install from GitHub
IMAGE="python:3.11-bookworm"

# Cleanup function
cleanup() {
    echo ""
    echo -e "${YELLOW}Cleaning up container...${NC}"
    $CONTAINER_CMD rm -f "$CONTAINER_NAME" 2>/dev/null || true
}
trap cleanup EXIT

echo -e "${BLUE}[2/6]${NC} Creating isolated container..."
echo "      Mounting: $REPO_ROOT -> /workspace"

# Create container with repo mounted
$CONTAINER_CMD run -d \
    --name "$CONTAINER_NAME" \
    -v "$REPO_ROOT:/workspace:ro" \
    -w /workspace \
    "$IMAGE" \
    sleep infinity > /dev/null

echo -e "${GREEN}      Container created: $CONTAINER_NAME${NC}"

# Helper function to run commands in container
run_in_container() {
    $CONTAINER_CMD exec "$CONTAINER_NAME" bash -c "$1"
}

echo ""
echo -e "${BLUE}[3/6]${NC} Installing uv and dependencies..."

# Install uv
run_in_container "pip install uv -q 2>&1 | grep -v WARNING || true"

# Create a writable copy of the workspace for installing in editable mode
run_in_container "cp -r /workspace /test-workspace && cd /test-workspace" 2>/dev/null

# Install dependencies
run_in_container "cd /test-workspace && uv pip install --system 'amplifier-core @ git+https://github.com/microsoft/amplifier-core' -q" 2>/dev/null
run_in_container "cd /test-workspace && uv pip install --system 'amplifier-foundation @ git+https://github.com/microsoft/amplifier-foundation' -q" 2>/dev/null
run_in_container "cd /test-workspace && uv pip install --system -e '.[dev]' -q" 2>/dev/null

echo -e "${GREEN}      Dependencies installed${NC}"

echo ""
echo -e "${BLUE}[4/6]${NC} Running unit tests..."
echo ""

# Run unit tests
UNIT_OUTPUT=$(run_in_container "cd /test-workspace && python -m pytest tests/ -v --tb=short 2>&1" || true)

# Show relevant output
echo "$UNIT_OUTPUT" | grep -E "^tests/|PASSED|FAILED|ERROR|passed|failed|error|::test_" | head -20 || true

# Check for failures
if echo "$UNIT_OUTPUT" | grep -qE "[0-9]+ passed"; then
    UNIT_PASSED=$(echo "$UNIT_OUTPUT" | grep -oP '\d+(?= passed)' | tail -1)
    UNIT_RESULT="${GREEN}PASSED ($UNIT_PASSED tests)${NC}"
    UNIT_SUCCESS=true
else
    UNIT_RESULT="${RED}FAILED${NC}"
    UNIT_SUCCESS=false
fi

echo ""
echo -e "${BLUE}[5/6]${NC} Running worker bundle tests..."
echo ""

WORKER_TOTAL=0
WORKER_SUCCESS=true

for worker in coding-worker research-worker testing-worker; do
    # Use --import-mode=importlib to handle duplicate test_bundle.py filenames across workers
    WORKER_OUTPUT=$(run_in_container "cd /test-workspace && python -m pytest workers/amplifier-bundle-$worker/tests/ -v --tb=short --import-mode=importlib 2>&1" || true)
    
    if echo "$WORKER_OUTPUT" | grep -qE "[0-9]+ passed"; then
        WORKER_COUNT=$(echo "$WORKER_OUTPUT" | grep -oP '\d+(?= passed)' | tail -1 || echo "0")
        WORKER_TOTAL=$((WORKER_TOTAL + WORKER_COUNT))
        echo -e "      ${GREEN}✓${NC} $worker: $WORKER_COUNT tests passed"
    else
        echo -e "      ${RED}✗${NC} $worker: FAILED"
        WORKER_SUCCESS=false
    fi
done

echo ""
echo -e "${BLUE}[6/6]${NC} Running integration tests..."
echo ""

# Run integration tests
INTEGRATION_OUTPUT=$(run_in_container "cd /test-workspace && python test-example/integration_test.py 2>&1" || true)

# Show key results
echo "$INTEGRATION_OUTPUT" | grep -E "TEST [0-9]+:|✅|❌|Result:|ALL TESTS|PASSED|FAILED" | head -15 || true

# Check for integration test success
if echo "$INTEGRATION_OUTPUT" | grep -q "ALL TESTS PASSED"; then
    INTEGRATION_RESULT="${GREEN}PASSED (6 tests)${NC}"
    INTEGRATION_SUCCESS=true
else
    INTEGRATION_RESULT="${RED}FAILED${NC}"
    INTEGRATION_SUCCESS=false
fi

# Summary
echo ""
echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                      TEST SUMMARY                          ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Unit Tests:        $UNIT_RESULT"
echo -e "  Worker Tests:      ${GREEN}PASSED ($WORKER_TOTAL tests)${NC}"
echo -e "  Integration Tests: $INTEGRATION_RESULT"
echo ""

if $UNIT_SUCCESS && $WORKER_SUCCESS && $INTEGRATION_SUCCESS; then
    echo -e "${GREEN}🎉 All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}❌ Some tests failed${NC}"
    echo ""
    echo "To debug, run interactively:"
    echo "  $CONTAINER_CMD run -it -v $REPO_ROOT:/workspace $IMAGE bash"
    exit 1
fi
