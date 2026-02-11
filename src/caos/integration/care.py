"""CARE Dispatcher - Send actions to Cloud Workflows.

Dispatches approved actions to Google Cloud Workflows for physical execution.
The CARE agent handles the actual HAL (Hardware Abstraction Layer) operations.
"""

import json
import structlog
from typing import Any
from datetime import datetime

from google.cloud import workflows_v1
from google.cloud.workflows import executions_v1
from google.cloud.workflows.executions_v1 import Execution

from caos.config import get_settings
from caos.schemas.action import ActionSchema
from caos.schemas.enums import DecisionBand

logger = structlog.get_logger(__name__)


class CareDispatcher:
    """Dispatcher for sending actions to Cloud Workflows (CARE)."""

    def __init__(self) -> None:
        """Initialize the dispatcher."""
        self.settings = get_settings()
        self._executions_client: executions_v1.ExecutionsClient | None = None

    def _get_client(self) -> executions_v1.ExecutionsClient:
        """Get or create the Workflows Executions client."""
        if self._executions_client is None:
            self._executions_client = executions_v1.ExecutionsClient()
        return self._executions_client

    def _build_workflow_path(self, workflow_name: str) -> str:
        """Build the full workflow resource path."""
        return (
            f"projects/{self.settings.google_cloud_project}"
            f"/locations/{self.settings.vertex_ai_location}"
            f"/workflows/{workflow_name}"
        )

    async def dispatch(self, action: ActionSchema) -> dict[str, Any]:
        """Dispatch an action to Cloud Workflows.
        
        Args:
            action: The action to dispatch
            
        Returns:
            Execution result with execution_id and status
        """
        logger.info(
            "care_dispatch_start",
            action_id=action.action_id,
            workflow=action.workflow_name,
            decision=action.decision.value,
        )
        
        # Don't dispatch blocked actions
        if action.decision == DecisionBand.BLOCKED:
            logger.warning(
                "care_dispatch_blocked",
                action_id=action.action_id,
                reason="decision_blocked",
            )
            return {
                "status": "blocked",
                "action_id": action.action_id,
                "reason": "Action was blocked by guardrails",
            }
        
        # Don't dispatch if approval required and not approved
        if action.requires_approval:
            logger.info(
                "care_dispatch_pending_approval",
                action_id=action.action_id,
            )
            return {
                "status": "pending_approval",
                "action_id": action.action_id,
                "reason": "Action requires human approval",
            }
        
        try:
            # Build the workflow input
            workflow_input = {
                "action_id": action.action_id,
                "tenant_id": action.tenant_id,
                "asset_id": action.asset_id,
                "command": {
                    "type": action.command.type.value,
                    "target": action.command.target,
                    "params": action.command.params,
                },
                "verdict_score": action.verdict_score,
                "justification": action.justification,
                "created_at": action.created_at.isoformat(),
            }
            
            # Create the execution
            client = self._get_client()
            workflow_path = self._build_workflow_path(action.workflow_name)
            
            execution = Execution(
                argument=json.dumps(workflow_input),
            )
            
            result = client.create_execution(
                parent=workflow_path,
                execution=execution,
            )
            
            logger.info(
                "care_dispatch_success",
                action_id=action.action_id,
                execution_name=result.name,
                state=result.state.name,
            )
            
            return {
                "status": "dispatched",
                "action_id": action.action_id,
                "execution_id": result.name,
                "execution_state": result.state.name,
            }
        
        except Exception as e:
            logger.error(
                "care_dispatch_error",
                action_id=action.action_id,
                error=str(e),
            )
            return {
                "status": "error",
                "action_id": action.action_id,
                "error": str(e),
            }

    async def get_execution_status(self, execution_id: str) -> dict[str, Any]:
        """Get the status of a workflow execution.
        
        Args:
            execution_id: Full execution resource path
            
        Returns:
            Execution status and result
        """
        try:
            client = self._get_client()
            execution = client.get_execution(name=execution_id)
            
            return {
                "execution_id": execution.name,
                "state": execution.state.name,
                "start_time": execution.start_time.isoformat() if execution.start_time else None,
                "end_time": execution.end_time.isoformat() if execution.end_time else None,
                "result": execution.result if execution.result else None,
                "error": str(execution.error) if execution.error else None,
            }
        
        except Exception as e:
            logger.error("care_status_error", execution_id=execution_id, error=str(e))
            return {"status": "error", "error": str(e)}


# Singleton instance
_care_dispatcher: CareDispatcher | None = None


def get_care_dispatcher() -> CareDispatcher:
    """Get CARE dispatcher singleton."""
    global _care_dispatcher
    if _care_dispatcher is None:
        _care_dispatcher = CareDispatcher()
    return _care_dispatcher
