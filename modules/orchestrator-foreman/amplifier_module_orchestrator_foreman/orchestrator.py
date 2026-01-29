"""
Foreman Orchestrator - LLM-driven work coordination through issues and workers.

This orchestrator runs a standard agent loop but guides the LLM to coordinate work
through issues and background workers rather than doing everything directly.
"""

import asyncio
import json
import logging
import os
from typing import Any

from amplifier_core import HookRegistry, ToolSpec
from amplifier_core.events import ORCHESTRATOR_COMPLETE, PROMPT_SUBMIT
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
        self._spawn_errors: list[str] = []

        # Store coordinator for worker spawning
        self._coordinator: Any = None

        # Validate configuration
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate orchestrator configuration and log warnings for issues."""
        # Check if worker pools are configured
        if not self.worker_pools:
            logger.warning(
                "No worker pools configured - the foreman will not be able to spawn workers"
            )
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
                worker_bundle.startswith("git+")
                or worker_bundle.startswith("http")
                or worker_bundle.startswith("file:")
                or worker_bundle.startswith("/")
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

        # Emit prompt submit event (CRITICAL for session state management)
        prompt_submit_result = await hooks.emit(PROMPT_SUBMIT, {"prompt": prompt})
        if coordinator:
            prompt_submit_result = await coordinator.process_hook_result(
                prompt_submit_result, "prompt:submit", "orchestrator"
            )
            if prompt_submit_result.action == "deny":
                return f"Operation denied: {prompt_submit_result.reason}"

        # Emit execution start event
        await hooks.emit("execution:start", {"prompt": prompt})

        # Add the user message to context at the start of execution
        # This ensures proper conversation state management
        await context.add_message({"role": "user", "content": prompt})
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
        await self._update_context(context, final_response)

        # Emit orchestrator complete event - CRITICAL for session state management
        await hooks.emit(
            ORCHESTRATOR_COMPLETE,
            {
                "orchestrator": "foreman",
                "turn_count": iteration,
                "status": "success" if final_response else "incomplete",
            },
        )

        # Emit execution end event
        await hooks.emit("execution:end", {})

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
        # Note: The current user prompt was already added to context in execute()
        # so it will be included in this history
        history = []
        if hasattr(context, "get_messages"):
            history = await context.get_messages()
        for msg in history[-10:]:  # Last 10 messages for context
            if msg.get("role") in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg.get("content", "")})

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
        """Spawn a worker for a newly created issue by loading bundle directly.

        This method loads worker bundles directly using amplifier_foundation's
        load_bundle() and PreparedBundle.create_session(), enabling spawning
        of full bundles by URL without requiring CLI-level support.

        The foreman pattern specifically needs to spawn complete external bundles
        (not just agents defined in the parent config), so we bypass the CLI's
        session.spawn capability and use foundation primitives directly.
        """
        issue = issue_result.get("issue", {})
        issue_id = issue.get("id")

        if not issue_id or issue_id in self._spawned_issues:
            return

        self._spawned_issues.add(issue_id)

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

        # Mark issue as in_progress before spawning
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

        # Build worker prompt
        worker_prompt = self._build_worker_prompt(issue)

        # Spawn worker in background using direct bundle loading
        try:
            logger.info(f"Spawning worker for issue {issue_id} via direct bundle loading")

            # Fire-and-forget spawn - worker runs asynchronously
            asyncio.create_task(
                self._run_spawn_and_handle_result(worker_bundle, worker_prompt, issue_id)
            )

            logger.info(f"Successfully initiated worker spawn for issue {issue_id}")
        except Exception as e:
            error = f"Failed to spawn worker: {e}"
            logger.error(error, exc_info=True)
            self._append_spawn_error(issue_id, error)

    def _build_worker_prompt(self, issue: dict[str, Any]) -> str:
        """Build the instruction prompt for a worker session.

        Args:
            issue: The issue dict containing id, title, description, metadata

        Returns:
            Formatted prompt string for the worker
        """
        issue_id = issue.get("id", "unknown")
        title = issue.get("title", "Untitled")
        description = issue.get("description", "No description")

        return f"""You are a worker assigned to complete this issue:

## Issue #{issue_id}: {title}

{description}

## Your Task
1. Complete the work described above
2. When done, use the issue_manager tool to update the issue:
   - operation: "update"
   - issue_id: "{issue_id}"
   - status: "completed"
   - Include a summary of what you did in the comment

If you need clarification, update the issue with status "pending_user_input".
If you are blocked, update the issue with status "blocked" and explain what's blocking you.
"""

    async def _run_spawn_and_handle_result(
        self,
        worker_bundle_uri: str,
        worker_prompt: str,
        issue_id: str,
    ) -> None:
        """Load worker bundle and execute in a new session.

        This method is run as an asyncio task (fire-and-forget) so that
        workers execute in the background while the foreman continues
        interacting with the user.

        Uses amplifier_foundation's load_bundle() and PreparedBundle.create_session()
        to spawn complete external bundles directly, without requiring CLI support.

        Args:
            worker_bundle_uri: Bundle URI (git+https://..., file path, etc.)
            worker_prompt: Instruction prompt for the worker
            issue_id: Issue ID for logging and error tracking
        """
        from amplifier_foundation import load_bundle

        try:
            # Load the worker bundle from URI
            logger.info(f"Loading worker bundle from: {worker_bundle_uri}")
            bundle = await load_bundle(worker_bundle_uri)
            logger.info(f"Loaded bundle '{bundle.name}' for issue {issue_id}")

            # Get parent session for config inheritance
            parent_session = getattr(self._coordinator, "session", None)
            parent_id = parent_session.session_id if parent_session else None

            # Inherit providers from parent session
            # Worker bundles typically don't define providers - they inherit from foreman
            if parent_session and parent_session.config.get("providers"):
                parent_providers = parent_session.config["providers"]
                if not bundle.providers:
                    bundle.providers = list(parent_providers)
                    logger.info(f"Inherited {len(parent_providers)} providers from parent session")

            # Prepare the bundle (activates modules, creates resolver)
            prepared = await bundle.prepare()
            logger.info(f"Prepared bundle '{bundle.name}' for issue {issue_id}")

            # Inherit UX systems from parent for consistent display/approval
            approval_system = None
            display_system = None
            if parent_session and hasattr(parent_session, "coordinator"):
                approval_system = getattr(parent_session.coordinator, "approval_system", None)
                display_system = getattr(parent_session.coordinator, "display_system", None)

            # Create worker session
            worker_session = await prepared.create_session(
                parent_id=parent_id,
                approval_system=approval_system,
                display_system=display_system,
            )

            # Inherit working directory from parent so tools use same paths
            # Critical for issue_manager which uses relative path .amplifier/issues
            if parent_session:
                parent_working_dir = parent_session.coordinator.get_capability(
                    "session.working_dir"
                )
                if parent_working_dir:
                    worker_session.coordinator.register_capability(
                        "session.working_dir", parent_working_dir
                    )
                    logger.info(f"Inherited working_dir from parent: {parent_working_dir}")

            try:
                # Execute the worker instruction
                logger.info(f"Executing worker for issue {issue_id}")
                await worker_session.execute(worker_prompt)
                logger.info(
                    f"Worker completed for issue {issue_id}, session: {worker_session.session_id}"
                )
            finally:
                # Always cleanup the worker session
                await worker_session.cleanup()

        except Exception as e:
            error = f"Worker execution failed for issue {issue_id}: {e}"
            logger.error(error, exc_info=True)
            self._append_spawn_error(issue_id, f"Worker execution failed: {e}")

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
        if (
            bundle_path.startswith("git+")
            or bundle_path.startswith("http")
            or bundle_path.startswith("file:")
            or bundle_path.startswith("/")
        ):
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

    def _find_repo_root(self) -> str | None:
        """Find the repository root directory from the current working directory."""
        # Start from current directory
        path = os.getcwd()

        # Walk up until we find .git or .amplifier directory
        while path != "/":
            if os.path.exists(os.path.join(path, ".git")) or os.path.exists(
                os.path.join(path, ".amplifier")
            ):
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
            await issue_tool.execute(
                {
                    "operation": "update",
                    "params": {
                        "issue_id": issue_id,
                        "status": "blocked",
                        "comment": f"Worker spawning failed: {error}",
                    },
                }
            )
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

    async def _update_context(self, context: Any, response: str) -> None:
        """Store the assistant response in context.

        Note: User message is already added at the start of execute(),
        so we only need to add the assistant response here.
        """
        try:
            if hasattr(context, "add_message"):
                await context.add_message({"role": "assistant", "content": response})
        except Exception as e:
            logger.error(f"Failed to update context: {e}")


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the foreman orchestrator."""
    config = config or {}
    orchestrator = ForemanOrchestrator(config)
    await coordinator.mount("orchestrator", orchestrator)
    return None
