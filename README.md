# Foreman Bundle

Conversational autonomous work orchestration for Amplifier.

## Overview

The Foreman Bundle provides a conversational orchestrator that coordinates multiple specialized worker bundles through a shared issue queue. It enables parallel execution, background work, and proactive progress reporting.

### Key Features

- **Conversational Interface**: Immediate responses, add work anytime, ask for status anytime
- **Background Workers**: Workers run as separate sessions while foreman responds quickly
- **Proactive Updates**: Automatic completion and blocker reporting on every turn
- **Specialized Workers**: Route work to appropriate worker bundles (coding, research, testing)
- **Issue-Based Coordination**: Shared issue queue as coordination primitive
- **Parallel Execution**: Spawn multiple workers simultaneously

## Installation

```bash
# Install from source
pip install -e .

# Or as dependency
pip install git+https://github.com/your-org/amplifier-bundle-foreman@v1.0.0
```

## Usage

### Basic Usage

```yaml
# your-bundle.md
includes:
  - bundle: git+https://github.com/your-org/amplifier-bundle-foreman@v1.0.0
```

Then use conversationally:

```
User: "Refactor the authentication system"

Foreman: 📋 Analyzing work request...

         Created 5 issues:
           • Issue #1: Split auth.py into modules
           • Issue #2: Update imports
           • Issue #3: Update tests
           • Issue #4: Add integration tests
           • Issue #5: Update documentation

         🚀 Spawned 5 workers to handle these issues.
         I'll keep you posted on progress!

[Workers run in background]

User: "status"

Foreman: 📊 Current Status

         ⏳ In Progress (3):
           • Update tests
           • Add integration tests
           • Update documentation

         ✅ Completed (2)
```

### Configuration

Configure worker pools in your bundle:

```yaml
orchestrator:
  module: orchestrator-foreman
  source: git+https://github.com/your-org/amplifier-bundle-foreman@v1.0.0
  config:
    worker_pools:
      # Coding tasks
      - name: coding-pool
        worker_bundle: git+https://github.com/your-org/coding-worker-bundle@v1.0.0
        max_concurrent: 3
        route_types: [coding, implementation, bugfix, refactor]
      
      # Research tasks
      - name: research-pool
        worker_bundle: git+https://github.com/your-org/research-worker-bundle@v1.0.0
        max_concurrent: 2
        route_types: [research, analysis, investigation]
    
    # Routing rules
    routing:
      default_pool: coding-pool
      rules:
        - if_metadata_type: [coding]
          then_pool: coding-pool
        
        - if_status: blocked
          and_retry_count_gte: 2
          then_pool: privileged-pool
```

## Worker Bundles

The foreman coordinates specialized worker bundles. Each worker bundle is self-contained with:

- Specialized instructions for its domain
- Specific tool access (security boundaries)
- Clear capabilities and limitations

### Example Worker Bundle Structure

```yaml
# coding-worker-bundle/bundle.md
---
bundle:
  name: coding-worker
  version: 1.0.0
---

# Coding Worker

You are a coding specialist...

tools:
  - module: tool-filesystem
    config:
      allowed_write_paths: ["src/**", "tests/**"]
  - module: tool-bash
  - module: tool-issue
```

### Creating Worker Bundles

See [Worker Bundle Guide](docs/WORKER_BUNDLE_GUIDE.md) for details on creating worker bundles.

## Architecture

### How It Works

1. **User makes request**: "Refactor authentication"
2. **Foreman analyzes**: Uses LLM to break into issues
3. **Foreman spawns workers**: Via task tool (fire-and-forget)
4. **Workers execute**: In separate sessions, update issue queue
5. **Foreman reports**: On every turn, checks queue and reports updates

### Components

- **Foreman Orchestrator**: Coordinates work, spawns workers, reports progress
- **Worker Bundles**: Specialized agents for specific tasks
- **Issue Queue**: Shared coordination state (via issue tool)
- **Task Tool**: Spawns workers as separate sessions

### Execution Flow

```
User Message
     │
     ▼
┌─────────────────┐
│   Foreman       │  1. Check issue queue for updates
│   execute()     │  2. Report completions/blockers
│                 │  3. Process current request
│                 │  4. Spawn workers if needed
│                 │  5. Return quickly
└─────────────────┘
     │
     ├─→ Spawn Worker 1 (background)
     ├─→ Spawn Worker 2 (background)
     └─→ Spawn Worker 3 (background)
```

## User Experience Patterns

### Adding Work Anytime

```
User: "Implement OAuth"
Foreman: Created 3 issues, spawned 3 workers

[Immediately after]
User: "Also add rate limiting"
Foreman: ✅ Completed (1): ...
         Created 2 issues, spawned 2 workers
```

### Status on Demand

```
User: "how's it going?"
Foreman: 📊 Current Status
         ⏳ In Progress (2)
         ✅ Completed (3)
```

### Handling Blockers

```
Foreman: ⚠️ Need Your Input (1):
         • Design rate limiter
           → Should we use token bucket or sliding window?

User: "Use token bucket"
Foreman: ✅ Resuming work with your input
```

## Configuration Reference

### Worker Pool Options

```yaml
worker_pools:
  - name: pool-name                    # Unique pool identifier
    worker_bundle: bundle-url          # Worker bundle URL
    max_concurrent: 3                  # Max parallel workers (future)
    route_types: [type1, type2]        # Issue types to route here
```

### Routing Options

```yaml
routing:
  default_pool: pool-name              # Fallback pool
  
  rules:
    - if_metadata_type: [type1]        # Match issue type
      then_pool: pool-name             # Send to this pool
    
    - if_status: blocked               # Match issue status
      and_retry_count_gte: 2           # After N retries
      then_pool: escalation-pool       # Escalate here
```

## Development

### Running Tests

There are three levels of testing available:

#### Quick Unit Tests (requires local amplifier-core)

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run unit tests
pytest tests/

# Run with coverage
pytest --cov=amplifier_bundle_foreman
```

#### Full Test Suite in Shadow Environment (Recommended)

The shadow environment provides isolated, reproducible testing without affecting your local environment:

```bash
# Run the full test suite (unit + worker + integration tests)
./test-example/run_shadow_tests.sh
```

This script:
1. Creates an isolated shadow environment
2. Installs all dependencies from GitHub (amplifier-core, amplifier-foundation)
3. Runs 14 unit tests for the orchestrator
4. Runs 19 tests across all 3 worker bundles
5. Runs 6 integration tests demonstrating end-to-end workflow
6. Cleans up the shadow environment

#### Manual Shadow Environment Testing

For interactive testing or debugging:

```bash
# Create shadow environment with local source
amplifier tool invoke shadow \
    operation=create \
    local_sources='["/path/to/amplifier-bundle-foreman:microsoft/amplifier-bundle-foreman"]'

# Note the shadow_id from output, then install dependencies
amplifier tool invoke shadow operation=exec shadow_id=<id> \
    command="uv pip install 'amplifier-core @ git+https://github.com/microsoft/amplifier-core'"
amplifier tool invoke shadow operation=exec shadow_id=<id> \
    command="uv pip install 'amplifier-foundation @ git+https://github.com/microsoft/amplifier-foundation'"
amplifier tool invoke shadow operation=exec shadow_id=<id> \
    command="uv pip install -e '/workspace/amplifier-bundle-foreman[dev]'"

# Run tests
amplifier tool invoke shadow operation=exec shadow_id=<id> \
    command="cd /workspace/amplifier-bundle-foreman && pytest tests/ -v"

# Run integration tests
amplifier tool invoke shadow operation=exec shadow_id=<id> \
    command="cd /workspace/amplifier-bundle-foreman && python test-example/integration_test.py"

# Clean up when done
amplifier tool invoke shadow operation=destroy shadow_id=<id>
```

### Integration Test Details

The integration tests in `test-example/` demonstrate the foreman's capabilities:

| Test | What It Verifies |
|------|------------------|
| Work Request Breakdown | LLM-based decomposition of requests into issues |
| Issue Routing | Correct routing to worker pools by type |
| Status Reporting | Comprehensive status with all issue states |
| Completion Reporting | Proactive reporting without repetition |
| Blocker Handling | Surfacing blockers and handling resolutions |
| Full Workflow | Multi-turn simulation with concurrent work |

See `test-example/README.md` for detailed output examples.

### Code Quality

```bash
# Format code
ruff format .

# Lint
ruff check .

# Type check
pyright
```

## Integration with Other Bundles

### With Observer Bundle

Combine foreman with observers for quality feedback:

```yaml
includes:
  - bundle: foreman-bundle
  - bundle: observer-bundle
    config:
      observers:
        - name: code-quality
          creates_issues: [feedback]

# Workers produce code → Observers create feedback issues → 
# Foreman spawns workers to address feedback
```

### With Issue Bundle

Foreman requires the issue bundle:

```yaml
tools:
  - module: tool-issue
    source: git+https://github.com/your-org/amplifier-bundle-issues@main
```

## Limitations

Current limitations (future enhancements):

- No worker timeout detection (issues stay in_progress if worker crashes)
- No max_concurrent enforcement (will spawn all workers)
- No dependency tracking (can't say "do B after A completes")
- No worker context inheritance (workers start fresh each time)

## Known Issues

### Session Persistence Bug

There is a known issue with the upstream amplifier-app-cli that affects session persistence in the foreman orchestrator:

- **Bug**: SessionStore.get_metadata() crashes when the session directory doesn't exist yet
- **Impact**: Worker sessions may fail with "Session not found" errors
- **Status**: [Issue filed](https://github.com/payneio/amplifier-bundle-foreman/issues/1)
- **Root cause**: In amplifier-app-cli cleanup code (main.py line 1790), the CLI attempts to access session metadata before the directory is created

#### Workaround Options

1. **Patch SessionStore.get_metadata()**

   Create a patched version of amplifier-app-cli that handles non-existent session directories gracefully:

   ```python
   # In amplifier_app_cli/session_store.py
   def get_metadata(self, session_id: str) -> dict | None:
       """Get session metadata."""
       session_dir = self.base_dir / session_id
       if not session_dir.exists():
           return None  # Return None instead of raising error
       metadata_path = session_dir / "metadata.json"
       if not metadata_path.exists():
           return None
       with open(metadata_path, "r", encoding="utf-8") as f:
           return json.load(f)
   ```

2. **Create session directory before first save**

   Another approach is to ensure the session directory exists before the first save:

   ```python
   # In amplifier_app_cli/main.py cleanup section
   session_dir = store.base_dir / actual_session_id
   session_dir.mkdir(exist_ok=True, parents=True)
   existing_metadata = store.get_metadata(actual_session_id) or {}
   ```

## Contributing

Contributions welcome! Please:

1. Add tests for new features
2. Follow code style (ruff)
3. Update documentation
4. Add examples for new patterns

## License

MIT

## Related Projects

- [Amplifier Core](https://github.com/microsoft/amplifier-core) - The kernel
- [Amplifier Foundation](https://github.com/microsoft/amplifier-foundation) - Bundle primitives
- [Issue Bundle](https://github.com/your-org/amplifier-bundle-issues) - Issue management
- [Coding Worker](https://github.com/your-org/amplifier-bundle-coding-worker) - Example worker
- [Research Worker](https://github.com/your-org/amplifier-bundle-research-worker) - Example worker

## Support

For questions and support:
- GitHub Issues: [Report bugs or request features](https://github.com/your-org/amplifier-bundle-foreman/issues)
- Documentation: See `docs/` directory
- Examples: See `examples/` directory
