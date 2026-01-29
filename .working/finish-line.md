# Foreman Pattern: Finish Line Analysis

## Executive Summary

The foreman pattern implements a hierarchical orchestration model where a main "foreman" session manages specialized worker sessions through an issue queue. After analyzing the current implementation, I've identified several critical issues that prevent the pattern from working correctly. This document outlines these issues and provides a clear implementation roadmap to create a fully functional foreman pattern.

## Current State Assessment

### What's Working
- The foreman orchestrator basic structure is solid
- Issue queue communication mechanism is well-designed
- Worker pool and routing configuration is flexible
- Background execution model with asyncio is appropriate

### Critical Issues
1. **Worker agent definition inconsistency** - Agent markdown files are deleted but still referenced
2. **Session spawning mechanism confusion** - Current approach needs clarification and consistency
3. **Worker bundle loading uncertainty** - Unclear how worker bundles are loaded and resolved
4. **Lack of comprehensive testing** - Testing focuses on routing but not actual worker execution

## Detailed Findings

### 1. Worker Agent Definition

**Issue:** The `bundle.md` references worker agents (`foreman:coding-worker`, `foreman:research-worker`, `foreman:testing-worker`), but the corresponding agent markdown files (`agents/coding-worker.md`, etc.) are deleted according to git status.

```yaml
# From bundle.md
worker_pools:
  - name: coding-pool
    worker_agent: foreman:coding-worker
    max_concurrent: 3
    route_types: [task, feature, bug]
```

**Impact:** The orchestrator cannot spawn workers because it cannot resolve these agent names to actual bundle sources.

### 2. Session Spawning Mechanism

**Issue:** The current implementation uses an approach that's inconsistent with our desired direct bundle-based spawning:

```python
spawn = self._coordinator.get_capability("session.spawn")
asyncio.create_task(
    spawn(
        agent_name=worker_agent,
        instruction=worker_prompt,
        parent_session=self._coordinator.session,
        agent_configs=WORKER_AGENT_CONFIGS,
    )
)
```

**Impact:** This creates confusion about how workers are spawned, and recent commits show ongoing changes to session handling in an attempt to fix issues.

### 3. Worker Bundle Loading

**Issue:** There's uncertainty about how worker bundles are loaded. The hardcoded `WORKER_AGENT_CONFIGS` dictionary specifies `"bundle": "git+https://github.com/microsoft/amplifier-foundation@main"` for all worker types, which doesn't align with the specialized worker bundles in the `./workers/` directory.

**Impact:** Workers may not be loading the correct bundle configuration, causing them to run with incorrect tools or instructions.

### 4. Testing Coverage

**Issue:** Current tests focus on routing and issue management but don't verify if worker sessions are actually created and executed correctly. The tests mock the coordinator and spawn capability without validating the actual session creation mechanism.

**Impact:** Changes to the spawning mechanism may break functionality without test failures, making it difficult to identify issues.

## Implementation Roadmap

### Phase 1: Fix Agent References (Priority: High)

1. **Implement direct bundle references in bundle.md**
   - Modify bundle.md to reference actual worker bundles directly instead of using agent references
   - Replace `worker_agent` with `worker_bundle` in pool configuration
   - Example:
     ```yaml
     worker_pools:
       - name: coding-pool
         worker_bundle: "git+https://github.com/payneio/amplifier-bundle-foreman@main#subdirectory=workers/amplifier-bundle-coding-worker"
         max_concurrent: 3
         route_types: [task, feature, bug]
     ```

2. **Update orchestrator to handle bundle references**
   - Modify routing and spawning code to use bundle paths instead of agent names
   - Remove or repurpose `WORKER_AGENT_CONFIGS` dictionary to support direct bundle loading

3. **Verify bundle resolution works**
   - Create a simple test that confirms bundle paths resolve correctly
   - Test that worker sessions can be created from bundle references

### Phase 2: Implement Direct Bundle Spawning (Priority: High)

1. **Create direct bundle spawning mechanism**
   - Implement a clean, focused approach that directly loads and spawns bundles
   - Use amplifier-foundation primitives like `load_bundle()` and `AmplifierSession`
   - No need to maintain compatibility with task tool

2. **Refactor spawning mechanism**
   - Modify `_maybe_spawn_worker` to use direct bundle loading
   - Ensure correct handling of session IDs (not full session objects)
   - Implement proper error handling for spawn failures

3. **Add robust logging**
   - Add detailed logging around worker spawning for easier debugging
   - Include bundle loading and session creation details in logs

### Phase 3: Improve Test Coverage (Priority: Medium)

1. **Implement unit tests** for:
   - Bundle loading and session creation
   - Session creation and execution
   - Worker lifecycle management
   - Issue queue communication

2. **Create integration tests** for the complete workflow:
   - Foreman receiving request
   - Breaking down into issues
   - Spawning workers
   - Workers updating issues
   - Foreman reporting completion

### Phase 4: Documentation and Examples (Priority: Medium)

1. **Update architecture documentation**
   - Clarify worker spawning mechanism
   - Document bundle resolution process
   - Update diagrams showing session relationship

2. **Create example worker bundles**
   - Develop clear examples of worker bundles
   - Document required configuration for worker bundles

## Concrete Implementation Steps

### 1. Update bundle.md to Use Direct Bundle References

```yaml
# In bundle.md
worker_pools:
  - name: coding-pool
    worker_bundle: "git+https://github.com/payneio/amplifier-bundle-foreman@main#subdirectory=workers/amplifier-bundle-coding-worker"
    max_concurrent: 3
    route_types: [task, feature, bug]
  
  - name: research-pool
    worker_bundle: "git+https://github.com/payneio/amplifier-bundle-foreman@main#subdirectory=workers/amplifier-bundle-research-worker"
    max_concurrent: 2
    route_types: [epic]
    
  - name: testing-pool
    worker_bundle: "git+https://github.com/payneio/amplifier-bundle-foreman@main#subdirectory=workers/amplifier-bundle-testing-worker"
    max_concurrent: 2
    route_types: [chore]
```

### 2. Implement Direct Bundle Spawning

```python
async def _maybe_spawn_worker(self, issue_result: dict[str, Any], issue_tool: Any) -> None:
    """Spawn a worker for a newly created issue."""
    issue = issue_result.get("issue", {})
    issue_id = issue.get("id")

    if not issue_id or issue_id in self._spawned_issues:
        return

    self._spawned_issues.add(issue_id)

    # Route to appropriate worker pool
    pool_config = self._route_issue(issue)
    if not pool_config:
        logger.warning(f"No worker pool for issue {issue_id}")
        return

    # Get worker bundle path
    worker_bundle_path = pool_config.get("worker_bundle")
    if not worker_bundle_path:
        logger.warning(f"No worker_bundle configured for pool {pool_config.get('name')}")
        return

    # Build worker prompt
    worker_prompt = f"""You are a worker assigned to complete this issue:

## Issue #{issue_id}: {issue.get("title", "Untitled")}

{issue.get("description", "No description")}

## Your Task
1. Complete the work described above
2. When done, use the issue_manager tool to update the issue:
   - operation: "update"
   - issue_id: "{issue_id}"
   - status: "completed"
   - Include a summary of what you did in the comment

If you need clarification, update the issue with status "pending_user_input".
"""

    # Mark issue as in_progress
    try:
        await issue_tool.execute(
            {
                "operation": "update",
                "params": {
                    "issue_id": issue_id,
                    "status": "in_progress",
                },
            }
        )
    except Exception as e:
        logger.error(f"Failed to update issue status: {e}")

    # Spawn worker using direct bundle loading
    try:
        logger.info(f"Spawning worker for issue {issue_id} using bundle {worker_bundle_path}")
        
        # Get foundation primitives
        load_bundle = self._coordinator.get_capability("bundle.load")
        AmplifierSession = self._coordinator.get_capability("session.AmplifierSession")
        if not load_bundle or not AmplifierSession:
            logger.error("Required capabilities not available")
            return
        
        # Load worker bundle directly
        bundle = await load_bundle(worker_bundle_path)
        if not bundle:
            logger.error(f"Failed to load bundle: {worker_bundle_path}")
            return
            
        # Create worker session with parent ID for relationship
        parent_session_id = getattr(self._coordinator.session, "id", None)
        if not parent_session_id:
            logger.error("Cannot access parent session ID")
            return
            
        # Create worker session with bundle config
        worker_session = AmplifierSession(
            config=bundle.config,
            parent_id=parent_session_id
        )
        
        # Execute worker session in background
        asyncio.create_task(worker_session.run(worker_prompt))
        
        logger.info(f"Successfully spawned worker for issue {issue_id}")
    except Exception as e:
        logger.error(f"Failed to spawn worker: {e}")
```

### 3. Create Test for Direct Bundle Spawning

```python
@pytest.mark.asyncio
async def test_direct_bundle_spawning():
    """Test the complete direct bundle spawning mechanism."""
    # Create coordinator with required capabilities
    coordinator = await create_test_coordinator()
    
    # Mock capabilities
    mock_load_bundle = AsyncMock(return_value=MagicMock(config={}))
    mock_amplifier_session = MagicMock(return_value=MagicMock(run=AsyncMock()))
    coordinator.get_capability.side_effect = lambda name: {
        "bundle.load": mock_load_bundle,
        "session.AmplifierSession": mock_amplifier_session,
    }.get(name)
    
    # Configure foreman orchestrator
    config = {
        "worker_pools": [
            {
                "name": "coding-pool",
                "worker_bundle": "git+https://github.com/example/worker-bundle",
                "max_concurrent": 1,
                "route_types": ["task"]
            }
        ],
        "routing": {"default_pool": "coding-pool"}
    }
    
    # Initialize orchestrator
    orchestrator = ForemanOrchestrator(config)
    orchestrator._coordinator = coordinator
    
    # Create a test issue
    issue_tool = AsyncMock()
    issue_tool.execute.return_value = {"issue": {"id": "test-1"}}
    
    # Attempt to spawn a worker
    await orchestrator._maybe_spawn_worker(
        {"issue": {"id": "issue-1", "title": "Test Issue", "metadata": {"type": "task"}}},
        issue_tool
    )
    
    # Verify bundle was loaded
    mock_load_bundle.assert_called_once_with("git+https://github.com/example/worker-bundle")
    
    # Verify session was created
    assert mock_amplifier_session.called
    
    # Verify session.run was called with the worker prompt
    worker_session = mock_amplifier_session.return_value
    assert worker_session.run.called
    assert "issue-1" in worker_session.run.call_args[0][0]
```

## Conclusion

The foreman pattern is a powerful concept for orchestrating work across specialized worker bundles. The current implementation has most of the fundamental components in place but is facing issues with worker spawning and bundle resolution. By implementing the fixes outlined in this document, the pattern can become fully functional.

The key to success lies in:

1. **Direct bundle references** - Modify bundle.md to reference worker bundles directly
2. **Clean bundle spawning** - Implement a direct bundle loading and session creation mechanism
3. **Comprehensive testing** - Ensure every aspect of the foreman-worker relationship is tested
4. **Clear documentation** - Document the pattern for others to understand and extend

Once these issues are addressed, the foreman pattern will provide a robust framework for managing complex, multi-agent workflows within the Amplifier ecosystem.