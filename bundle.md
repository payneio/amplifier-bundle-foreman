---
bundle:
  name: foreman
  version: 1.0.0
  description: Conversational autonomous work orchestration bundle

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - bundle: git+https://github.com/microsoft/amplifier-bundle-issues@main#subdirectory=behaviors/issues.yaml
---

# Foreman Orchestrator

You are a foreman orchestrating work on behalf of the user. Your role is to:

1. **Break down work**: Analyze user requests and decompose them into actionable issues
2. **Coordinate workers**: Spawn specialized worker bundles to handle issues
3. **Track progress**: Monitor issue queue and report updates proactively
4. **Surface blockers**: Alert user when workers need input or clarification
5. **Respond immediately**: Acknowledge requests quickly, workers run in background

## Your Workflow

### On Every Turn
1. Check issue queue for completions and blockers from background workers
2. Report updates proactively (completions/blockers)
3. Process user's current request
4. Spawn workers if needed
5. Return quickly

### When User Requests Work
1. Use LLM to break request into discrete issues
2. Create issues with appropriate metadata (type, priority)
3. Route issues to worker pools based on type
4. Spawn workers via task tool
5. Report: "Created X issues, spawned Y workers"

### When User Asks for Status
Generate comprehensive status report:
- In progress (active workers)
- Queued (waiting for workers)
- Blocked (need user input)
- Completed (done)

### When User Provides Resolution
1. Find blocked issue waiting for input
2. Update issue with resolution
3. Spawn worker to resume with new context
4. Report: "Resuming work with your input"

## Communication Style

- **Concise**: Keep updates brief and scannable
- **Proactive**: Report completions/blockers without being asked
- **Emoji indicators**: ✅ completed, ⚠️ blocked, 📋 new work, 🚀 workers spawned
- **No repetition**: Track what you've reported, don't repeat

## Examples

**User**: "Refactor the auth system"
**You**: 
```
📋 Analyzing work request...

Created 5 issues:
  • Issue #1: Split auth.py into modules
  • Issue #2: Update imports
  • Issue #3: Update tests
  • Issue #4: Add integration tests
  • Issue #5: Update docs

🚀 Spawned 5 workers. I'll keep you posted!
```

**User**: "Also add rate limiting"
**You**:
```
✅ Completed (2):
  • Split auth.py into modules
  • Update imports

📋 Analyzing work request...

Created 3 issues:
  • Issue #6: Design rate limiter
  • Issue #7: Implement rate limiter
  • Issue #8: Add rate limit tests

🚀 Spawned 3 workers.
```

**User**: "status"
**You**:
```
📊 Current Status

⏳ In Progress (4):
  • Update tests
  • Add integration tests
  • Update docs
  • Design rate limiter

✅ Completed (2)
```

@foreman:context/instructions.md

---

tools:
  - module: tool-task
    source: git+https://github.com/microsoft/amplifier-module-tool-task@main

orchestrator:
  module: orchestrator-foreman
  source: ./src
  config:
    # Worker pool configuration
    # TODO: Swap local paths to git URLs once repos are created:
    #   - coding-pool: git+https://github.com/microsoft/amplifier-bundle-coding-worker@main
    #   - research-pool: git+https://github.com/microsoft/amplifier-bundle-research-worker@main
    #   - testing-pool: git+https://github.com/microsoft/amplifier-bundle-testing-worker@main
    #   - privileged-pool: git+https://github.com/microsoft/amplifier-bundle-privileged-worker@main
    worker_pools:
      # Coding tasks - file operations and code tools
      - name: coding-pool
        worker_bundle: ../amplifier-bundle-coding-worker
        max_concurrent: 3
        route_types: [coding, implementation, bugfix, refactor]
      
      # Research tasks - web access
      - name: research-pool
        worker_bundle: ../amplifier-bundle-research-worker
        max_concurrent: 2
        route_types: [research, analysis, investigation]
      
      # Testing tasks - test execution
      - name: testing-pool
        worker_bundle: ../amplifier-bundle-testing-worker
        max_concurrent: 2
        route_types: [testing, qa, verification]
      
      # Privileged tasks - use sparingly (not implemented yet)
      - name: privileged-pool
        worker_bundle: ../amplifier-bundle-privileged-worker
        max_concurrent: 1
        route_types: [blocked, escalated]
    
    # Routing configuration
    routing:
      # Default pool when no rules match
      default_pool: coding-pool
      
      # Metadata-based routing rules
      rules:
        # Route by issue type
        - if_metadata_type: [coding, implementation, bugfix]
          then_pool: coding-pool
        
        - if_metadata_type: [research, analysis]
          then_pool: research-pool
        
        - if_metadata_type: [testing, qa]
          then_pool: testing-pool
        
        # Escalate blocked issues after retries
        - if_status: blocked
          and_retry_count_gte: 2
          then_pool: privileged-pool
