"""
Foreman Orchestrator - LLM-driven work coordination through issues and workers.

This orchestrator runs a standard agent loop but guides the LLM to coordinate work
through issues and background workers rather than doing everything directly.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Union

from amplifier_core import HookRegistry, ToolSpec
from amplifier_core.message_models import ChatRequest, Message

logger = logging.getLogger(__name__)

# System prompt that makes the LLM act as a foreman
FOREMAN_SYSTEM_PROMPT = """You are a FOREMAN - a work coordinator who delegates tasks to specialized workers.

## YOUR ROLE

You DO NOT do the work yourself. Instead, you:
1. **Break down requests** into discrete issues using the `issue_manager` tool
2. **Let workers handle** the actual implementation
3. **Report progress** from workers to the user
4. **Handle blockers** when workers need clarification

## WORKFLOW

### When user requests work (build, create, implement, fix, etc.):
1. Acknowledge the request briefly
2. Use `issue_manager` with operation="create" to create issues for each task
3. Report what issues were created
4. Workers will be spawned automatically for each issue

### When user asks for status:
1. Use `issue_manager` with operation="list" to get current issues
2. Summarize: in_progress, completed, blocked

### When user provides clarification for a blocked issue:
1. Use `issue_manager` with operation="update" to add the clarification
2. Worker will resume automatically

## CRITICAL RULES

- **NEVER use bash, write_file, or other tools directly for work requests**
- **ALWAYS create issues** for work that needs to be done
- **Keep responses brief** - workers do the heavy lifting
- **Use emojis** for status: 📋 new work, ✅ completed, ⏳ in progress, ⚠️ blocked

## ISSUE TYPES

Valid issue types are: `task`, `feature`, `bug`, `epic`, `chore`
- Use `task` for implementation work
- Use `feature` for new functionality
- Use `bug` for fixes
- Use `chore` for maintenance/setup

## EXAMPLE

User: "Build me a calculator app"

You should respond:
"📋 Creating issues for calculator app...

Created 3 issues:
- Issue #1: Set up project structure (task)
- Issue #2: Implement calculator logic (feature)
- Issue #3: Create CLI interface (task)

🚀 Workers are being dispatched. I'll keep you posted on progress!"

Then CREATE those issues using the issue_manager tool with the correct issue_type.
"""


class ForemanOrchestrator:
    """Orchestrator that guides the LLM to coordinate work through issues and workers."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize the foreman orchestrator with configuration validation."""
        self.config = config
        self.worker_pools = config.get("worker_pools", [])
        self.routing_config = config.get("routing", {})
        self.max_iterations = config.get("max_iterations", 20)

        # Track spawned workers to avoid duplicates
        self._spawned_issues: set[str] = set()
        
        # Track worker spawn errors for reporting to user
        self._spawn_errors: List[str] = []

        # Store coordinator for worker spawning
        self._coordinator: Any = None
        
        # Validate configuration
        self._validate_config()
        
    def _validate_config(self) -> None:
        """Validate orchestrator configuration and log warnings for issues."""
        # Check if worker pools are configured
        if not self.worker_pools:
            logger.warning("No worker pools configured - the foreman will not be able to spawn workers")
            return
            
        # Validate each pool configuration
        for i, pool in enumerate(self.worker_pools):
            pool_name = pool.get("name", f"pool-{i}")
            
            # Check for worker_bundle
            if not pool.get("worker_bundle"):
                logger.warning(f"Worker pool '{pool_name}' is missing worker_bundle configuration")
            
            # Check if worker_bundle is a full URL (recommended)
            worker_bundle = pool.get("worker_bundle", "")
            if worker_bundle and not (
                worker_bundle.startswith("git+") or 
                worker_bundle.startswith("http") or
                worker_bundle.startswith("file:") or
                worker_bundle.startswith("/")
            ):
                logger.warning(
                    f"Worker pool '{pool_name}' uses a relative worker_bundle path '{worker_bundle}'. "
                    "This may cause issues when running from different directories. "
                    "Consider using full URLs like 'git+https://...'"
                )
                
            # Check for name (required for routing)
            if not pool.get("name"):
                logger.warning(f"Worker pool #{i} is missing a name - routing may fail")

    async def execute(
        self,
        prompt: str,
        context: Any,
        providers: dict[str, Any],
        tools: dict[str, Any],
        hooks: HookRegistry,
        coordinator: Any = None,
    ) -> str:
        """
        Execute the foreman agent loop.

        This runs a standard LLM conversation loop but:
        1. Injects foreman system prompt to guide behavior
        2. Monitors issue_manager calls to auto-spawn workers
        3. Reports progress from background workers
        """
        self._coordinator = coordinator

        # Get primary provider
        provider_name = next(iter(providers.keys()), None)
        if not provider_name:
            return "No LLM provider available."

        provider = providers[provider_name]

        # Check for worker updates before processing
        issue_tool = tools.get("issue") or tools.get("tool-issue") or tools.get("issue_manager")
        progress_report = ""
        if issue_tool:
            progress_report = await self._check_worker_progress(issue_tool)
            
        # Add spawn errors to progress report if any exist
        if hasattr(self, "_spawn_errors") and self._spawn_errors:
            if progress_report:
                progress_report += "\n\n"
            progress_report += "⚠️ Worker Spawn Errors:\n" + "\n".join(self._spawn_errors)
            # Clear errors after reporting
            self._spawn_errors = []

        # Build messages with foreman system prompt
        messages = await self._build_messages(prompt, context, progress_report)

        # Get tool specs
        tool_specs = self._get_tool_specs(tools)

        # Run the agent loop
        final_response = ""
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1

            # Call LLM - provider.complete() requires ChatRequest
            try:
                # Convert dict messages to Message objects
                message_objects = [Message(**m) for m in messages]
                request = ChatRequest(messages=message_objects, tools=tool_specs)
                response = await provider.complete(request)
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                return f"Error communicating with LLM: {e}"

            # Extract text content from response content blocks
            if response.content:
                text_parts = []
                for block in response.content:
                    if hasattr(block, "text"):
                        text_parts.append(block.text)
                    elif isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                if text_parts:
                    final_response = "\n".join(text_parts)

            # Handle tool calls
            if response.tool_calls:
                # Build assistant message content from response
                assistant_content: list[Any] = []
                if response.content and isinstance(response.content, list):
                    for block in response.content:
                        if hasattr(block, "model_dump"):
                            assistant_content.append(block.model_dump())
                        else:
                            assistant_content.append(block)
                elif final_response:
                    assistant_content.append({"type": "text", "text": final_response})

                # Add assistant message with tool_calls as SEPARATE field (not in content!)
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_content if assistant_content else "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "tool": tc.name,
                            "arguments": tc.arguments,
                        }
                        for tc in response.tool_calls
                    ],
                }
                messages.append(assistant_msg)

                # Execute tools and collect results
                tool_results = await self._execute_tools(
                    response.tool_calls, tools, issue_tool, hooks
                )

                # Add tool results as role="tool" messages (not user messages!)
                for result in tool_results:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": result["tool_call_id"],
                            "content": result["content"],
                        }
                    )

                # Continue loop for more LLM processing
                continue

            # No tool calls - we're done
            break

        # Store conversation in context
        await self._update_context(context, prompt, final_response)

        return final_response

    async def _build_messages(
        self, prompt: str, context: Any, progress_report: str
    ) -> list[dict[str, Any]]:
        """Build message list with foreman system prompt and history."""
        messages: list[dict[str, Any]] = []

        # Add foreman system prompt
        system_content = FOREMAN_SYSTEM_PROMPT
        if progress_report:
            system_content += f"\n\n## CURRENT WORKER STATUS\n{progress_report}"

        messages.append({"role": "system", "content": system_content})

        # Add conversation history from context (get_messages is async)
        history = []
        if hasattr(context, "get_messages"):
            history = await context.get_messages()
        for msg in history[-10:]:  # Last 10 messages for context
            if msg.get("role") in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg.get("content", "")})

        # Add current prompt
        messages.append({"role": "user", "content": prompt})

        return messages

    def _get_tool_specs(self, tools: dict[str, Any]) -> list[ToolSpec]:
        """Get tool specifications for LLM."""
        specs = []
        for name, tool in tools.items():
            # Use Tool protocol: name, description, input_schema properties
            description = getattr(tool, "description", "")
            input_schema = getattr(tool, "input_schema", {})
            specs.append(
                ToolSpec(
                    name=name,
                    description=description,
                    parameters=input_schema,
                )
            )
        return specs

    async def _execute_tools(
        self,
        tool_calls: list[Any],
        tools: dict[str, Any],
        issue_tool: Any,
        hooks: Any,
    ) -> list[dict[str, Any]]:
        """Execute tool calls and return results, emitting hook events."""
        results = []

        for tc in tool_calls:
            tool = tools.get(tc.name)
            if not tool:
                results.append(
                    {
                        "tool_call_id": tc.id,
                        "content": f"Tool '{tc.name}' not found",
                    }
                )
                continue

            # Emit tool:pre event
            if hooks:
                await hooks.emit(
                    "tool:pre",
                    {"tool_name": tc.name, "arguments": tc.arguments},
                )

            try:
                # Execute the tool
                result = await tool.execute(tc.arguments)
                output = result.output if hasattr(result, "output") else str(result)

                # If this was an issue creation, spawn a worker
                if (
                    tc.name == "issue_manager" or tc.name == "tool-issue" or tc.name == "issue"
                ) and tc.arguments.get("operation") == "create":
                    if isinstance(output, dict):
                        await self._maybe_spawn_worker(output, issue_tool)

                results.append(
                    {
                        "tool_call_id": tc.id,
                        "content": json.dumps(output) if isinstance(output, dict) else str(output),
                    }
                )

                # Emit tool:post event on success
                if hooks:
                    await hooks.emit(
                        "tool:post",
                        {"tool_name": tc.name, "result": output},
                    )

            except Exception as e:
                logger.error(f"Tool {tc.name} failed: {e}")
                results.append(
                    {
                        "tool_call_id": tc.id,
                        "content": f"Error: {e}",
                    }
                )
                # Emit tool:post event on error too (for logging/tracking)
                if hooks:
                    await hooks.emit(
                        "tool:post",
                        {"tool_name": tc.name, "error": str(e)},
                    )

        return results

    async def _maybe_spawn_worker(self, issue_result: dict[str, Any], issue_tool: Any) -> None:
        """Spawn a worker for a newly created issue using direct bundle loading."""
        issue = issue_result.get("issue", {})
        issue_id = issue.get("id")

        if not issue_id or issue_id in self._spawned_issues:
            return

        self._spawned_issues.add(issue_id)
        
        # Track errors for reporting back to user
        errors = []

        # Route to appropriate worker pool
        pool_config = self._route_issue(issue)
        if not pool_config:
            error = f"No worker pool found for issue {issue_id}"
            logger.warning(error)
            self._append_spawn_error(issue_id, error)
            return

        # Get worker bundle path
        worker_bundle = pool_config.get("worker_bundle")
        if not worker_bundle:
            error = f"No worker_bundle configured for pool {pool_config.get('name', 'unknown')}"
            logger.warning(error)
            self._append_spawn_error(issue_id, error)
            return
            
        # Ensure worker_bundle is properly resolved
        worker_bundle = self._resolve_bundle_path(worker_bundle)
        logger.info(f"Resolved worker bundle path: {worker_bundle}")

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
            logger.info(f"Spawning worker for issue {issue_id} using bundle {worker_bundle}")
            
            # Get foundation primitives with enhanced verification
            load_bundle = self._coordinator.get_capability("bundle.load")
            if not load_bundle:
                error = "Required capability 'bundle.load' not available"
                logger.error(error)
                self._append_spawn_error(issue_id, error)
                return
                
            AmplifierSession = self._coordinator.get_capability("session.AmplifierSession")
            if not AmplifierSession:
                error = "Required capability 'session.AmplifierSession' not available"
                logger.error(error)
                self._append_spawn_error(issue_id, error)
                return
            
            # Load worker bundle with detailed logging and error handling
            logger.debug(f"Loading bundle: {worker_bundle} (cwd: {os.getcwd()})")
            try:
                bundle = await load_bundle(worker_bundle)
                if not bundle:
                    error = f"Bundle loaded but returned None: {worker_bundle}"
                    logger.error(error)
                    self._append_spawn_error(issue_id, error)
                    return
            except Exception as e:
                error = f"Error loading bundle '{worker_bundle}': {str(e)}"
                logger.error(error, exc_info=True)
                self._append_spawn_error(issue_id, error)
                return
                
            logger.debug(f"Successfully loaded bundle: {worker_bundle}")
            
            # Get parent session ID with improved fallbacks
            parent_session_id = None
            if hasattr(self._coordinator, "session"):
                if hasattr(self._coordinator.session, "id"):
                    parent_session_id = self._coordinator.session.id
                elif hasattr(self._coordinator.session, "session_id"):
                    parent_session_id = self._coordinator.session.session_id
                elif hasattr(self._coordinator.session, "get_id"):
                    try:
                        parent_session_id = self._coordinator.session.get_id()
                    except Exception as e:
                        logger.debug(f"Error calling get_id(): {e}")

            if not parent_session_id:
                error = "Cannot access parent session ID through any known method"
                logger.error(error)
                self._append_spawn_error(issue_id, error)
                return
                
            logger.debug(f"Using parent session ID: {parent_session_id}")
                
            # Create worker session with bundle config
            logger.debug(f"Creating worker session with parent_id={parent_session_id}")
            worker_session = AmplifierSession(
                config=bundle.config,
                parent_id=parent_session_id
            )
            
            # Run worker session in background
            logger.info(f"Running worker session for issue {issue_id}")
            asyncio.create_task(self._initialize_and_run_worker(worker_session, worker_prompt, issue_id))
            
            logger.info(f"Successfully spawned worker for issue {issue_id}")
        except Exception as e:
            # Handle any errors from the main worker spawning process
            error = f"Failed to spawn worker: {e}"
            logger.error(error, exc_info=True)
            self._append_spawn_error(issue_id, error)
    
    async def _initialize_and_run_worker(self, worker_session, worker_prompt, issue_id):
        """Initialize session and run with proper error handling.
        
        This method ensures that session is properly initialized before running.
        AmplifierSession requires explicit initialization to mount modules and
        configure the session before execution.
        """
        try:
            # First, explicitly initialize the session
            logger.info(f"Initializing worker session for issue {issue_id}")
            await worker_session.initialize()
            
            # After initialization completes, run the session
            logger.info(f"Worker session initialized, running with prompt for issue {issue_id}")
            return await worker_session.run(worker_prompt)
        except Exception as e:
            error_msg = f"Worker execution failed for issue {issue_id}: {e}"
            logger.error(error_msg, exc_info=True)
            self._append_spawn_error(issue_id, f"Worker execution failed: {e}")
            return f"Error: {e}"

    def _route_issue(self, issue: dict[str, Any]) -> dict[str, Any] | None:
        """Route issue to appropriate worker pool based on metadata."""
        issue_type = issue.get("metadata", {}).get("type", "general")

        # Check routing rules
        rules = self.routing_config.get("rules", [])
        for rule in rules:
            if "if_metadata_type" in rule:
                if issue_type in rule["if_metadata_type"]:
                    pool_name = rule.get("then_pool")
                    return self._get_pool_by_name(pool_name)

        # Fall back to default pool
        default_pool = self.routing_config.get("default_pool")
        if default_pool:
            return self._get_pool_by_name(default_pool)

        # Fall back to first pool
        return self.worker_pools[0] if self.worker_pools else None

    def _get_pool_by_name(self, name: str) -> dict[str, Any] | None:
        """Get worker pool config by name."""
        for pool in self.worker_pools:
            if pool.get("name") == name:
                return pool
        return None
        
    def _resolve_bundle_path(self, bundle_path: str) -> str:
        """Ensure bundle path is fully resolved to prevent directory-specific issues.
        
        This method handles different types of bundle paths:
        - git+https://... (absolute git URL)
        - http://... (absolute HTTP URL) 
        - file://... (absolute file URL)
        - /absolute/path (absolute filesystem path)
        - relative/path (relative path - may need resolution)
        """
        # Already absolute URL or path
        if (bundle_path.startswith("git+") or 
            bundle_path.startswith("http") or 
            bundle_path.startswith("file:") or
            bundle_path.startswith("/")):
            return bundle_path
            
        # Try to find repository root for relative paths
        repo_root = self._find_repo_root()
        if repo_root:
            # Resolve path relative to repo root
            absolute_path = os.path.normpath(os.path.join(repo_root, bundle_path))
            logger.debug(f"Resolved relative bundle path '{bundle_path}' to '{absolute_path}'")
            return absolute_path
            
        # If we can't find repo root, return the path as-is but log a warning
        logger.warning(f"Could not resolve relative bundle path '{bundle_path}' to absolute path")
        return bundle_path
        
    def _find_repo_root(self) -> Optional[str]:
        """Find the repository root directory from the current working directory."""
        # Start from current directory
        path = os.getcwd()
        
        # Walk up until we find .git or .amplifier directory
        while path != '/':
            if (os.path.exists(os.path.join(path, '.git')) or 
                os.path.exists(os.path.join(path, '.amplifier'))):
                return path
            # Move up one directory
            parent = os.path.dirname(path)
            if parent == path:  # Reached root
                break
            path = parent
                
        # If coordinator has repo root capability, use that
        if self._coordinator:
            repo_root = self._coordinator.get_capability("repo.root_path")
            if repo_root:
                return repo_root
                
        return None
        
    def _append_spawn_error(self, issue_id: str, error: str) -> None:
        """Add worker spawn error for reporting to user."""
        if not hasattr(self, "_spawn_errors"):
            self._spawn_errors = []
            
        error_msg = f"Issue #{issue_id}: {error}"
        self._spawn_errors.append(error_msg)
        
        # Also update issue status to blocked
        asyncio.create_task(self._update_issue_status_blocked(issue_id, error))
        
    async def _update_issue_status_blocked(self, issue_id: str, error: str) -> None:
        """Update issue status to blocked with error message."""
        # Get issue tool (may be called outside normal execution path)
        if not hasattr(self, "_coordinator") or not self._coordinator:
            logger.error("Cannot update issue status: coordinator not available")
            return
            
        tools = getattr(self._coordinator, "tools", {})
        issue_tool = tools.get("issue") or tools.get("tool-issue") or tools.get("issue_manager")
        if not issue_tool:
            logger.error("Cannot update issue status: issue tool not available")
            return
            
        try:
            await issue_tool.execute({
                "operation": "update",
                "params": {
                    "issue_id": issue_id,
                    "status": "blocked",
                    "comment": f"Worker spawning failed: {error}"
                }
            })
            logger.info(f"Updated issue #{issue_id} status to blocked")
        except Exception as e:
            logger.error(f"Failed to update issue #{issue_id} status: {e}")

    async def _check_worker_progress(self, issue_tool: Any) -> str:
        """Check for completed or blocked issues from workers."""
        parts = []

        try:
            # Check completed issues
            completed_result = await issue_tool.execute(
                {
                    "operation": "list",
                    "params": {"status": "completed"},
                }
            )
            completed = completed_result.output.get("issues", [])
            if completed:
                parts.append(f"✅ {len(completed)} issue(s) completed by workers")

            # Check blocked issues
            blocked_result = await issue_tool.execute(
                {
                    "operation": "list",
                    "params": {"status": "pending_user_input"},
                }
            )
            blocked = blocked_result.output.get("issues", [])
            if blocked:
                parts.append(f"⚠️ {len(blocked)} issue(s) need user input:")
                for issue in blocked[:3]:  # Show first 3
                    parts.append(f"  - #{issue['id']}: {issue['title']}")

            # Check in-progress issues
            in_progress_result = await issue_tool.execute(
                {
                    "operation": "list",
                    "params": {"status": "in_progress"},
                }
            )
            in_progress = in_progress_result.output.get("issues", [])
            if in_progress:
                parts.append(f"⏳ {len(in_progress)} issue(s) in progress")

        except Exception as e:
            logger.error(f"Failed to check worker progress: {e}")

        return "\n".join(parts)

    async def _update_context(self, context: Any, prompt: str, response: str) -> None:
        """Store the conversation turn in context."""
        try:
            if hasattr(context, "add_message"):
                await context.add_message({"role": "user", "content": prompt})
                await context.add_message({"role": "assistant", "content": response})
        except Exception as e:
            logger.error(f"Failed to update context: {e}")


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the foreman orchestrator."""
    config = config or {}
    orchestrator = ForemanOrchestrator(config)
    await coordinator.mount("orchestrator", orchestrator)
    return None