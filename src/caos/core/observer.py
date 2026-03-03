"""Observer Module — Aguardar e Reavaliar.

Implements the OBSERVE action pattern: when CAOS determines the root cause
is operational (e.g., peak-hour door openings + hot weather), it schedules
a re-evaluation after N minutes instead of immediately notifying.

On re-evaluation:
- If the situation resolved → AUTO_RESOLVED (just log)
- If it persists → escalate to NOTIFICATION
"""

import asyncio
import httpx
import structlog
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = structlog.get_logger(__name__)

DEFAULT_OBSERVE_DELAY_MINUTES = 30
MAX_OBSERVE_DELAY_MINUTES = 120


@dataclass
class ObservationTask:
    """A pending observation scheduled for re-evaluation."""

    observation_id: str
    event_id: str
    asset_id: str
    tenant_id: str
    metric: str
    original_value: float
    threshold: float | None
    delay_minutes: int
    original_reasoning: list[str]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "PENDING"  # PENDING, AUTO_RESOLVED, ESCALATED, CANCELLED
    resolution_reason: str = ""
    resolved_at: datetime | None = None


class ObserverScheduler:
    """Singleton scheduler that manages observation tasks.
    
    Schedules asyncio background tasks that sleep for the observation delay,
    then re-fetch data from Atlas and compare with the original trigger.
    """

    _instance: "ObserverScheduler | None" = None
    _lock = asyncio.Lock()

    def __init__(self) -> None:
        self._pending: dict[str, ObservationTask] = {}
        self._background_tasks: dict[str, asyncio.Task] = {}
        self._atlas_url = "http://localhost:9000"  # Simulators

    @classmethod
    def get_instance(cls) -> "ObserverScheduler":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def schedule(self, task: ObservationTask) -> str:
        """Schedule an observation for re-evaluation.
        
        Returns:
            The observation_id
        """
        self._pending[task.observation_id] = task

        # Create background asyncio task
        bg_task = asyncio.create_task(
            self._run_observation(task),
            name=f"observe_{task.observation_id}",
        )
        self._background_tasks[task.observation_id] = bg_task

        logger.info(
            "observation_scheduled",
            observation_id=task.observation_id,
            asset_id=task.asset_id,
            delay_minutes=task.delay_minutes,
            original_value=task.original_value,
        )

        return task.observation_id

    async def _run_observation(self, task: ObservationTask) -> None:
        """Background task: sleep, then re-evaluate."""
        try:
            delay_seconds = task.delay_minutes * 60
            logger.info(
                "observation_waiting",
                observation_id=task.observation_id,
                delay_seconds=delay_seconds,
            )

            await asyncio.sleep(delay_seconds)

            # Re-evaluate
            await self._reevaluate(task)

        except asyncio.CancelledError:
            task.status = "CANCELLED"
            task.resolved_at = datetime.now(timezone.utc)
            task.resolution_reason = "Observação cancelada manualmente"
            logger.info("observation_cancelled", observation_id=task.observation_id)

        except Exception as e:
            logger.error(
                "observation_error",
                observation_id=task.observation_id,
                error=str(e),
            )
            # On error, escalate to be safe
            await self._escalate(task, f"Erro na reavaliação: {e}")

    async def _reevaluate(self, task: ObservationTask) -> None:
        """Fetch fresh data from Atlas and compare with original state."""
        logger.info(
            "observation_reevaluating",
            observation_id=task.observation_id,
            asset_id=task.asset_id,
        )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Fetch fresh Atlas data
                r = await client.get(
                    f"{self._atlas_url}/atlas/v1/context/{task.asset_id}",
                    params={"tenant_id": task.tenant_id},
                )

                if r.status_code != 200:
                    await self._escalate(
                        task, f"Atlas indisponível (status {r.status_code})"
                    )
                    return

                atlas_data = r.json()
                current_state = atlas_data.get("current_state", {})
                sim_data = atlas_data.get("simulation_data", {})

                # Get current temperature
                current_temp = current_state.get("temperature")
                if current_temp is None:
                    current_temp = current_state.get(task.metric)

                # Get current door openings (if relevant)
                current_doors = sim_data.get("door_open_events_last_hour", 0)

                # Decision logic
                threshold = task.threshold or -11.0  # Default freezer threshold
                temp_ok = current_temp is not None and current_temp <= threshold
                doors_reduced = current_doors < 10  # Below "excessive" threshold

                logger.info(
                    "observation_comparison",
                    observation_id=task.observation_id,
                    original_value=task.original_value,
                    current_temp=current_temp,
                    threshold=threshold,
                    temp_ok=temp_ok,
                    current_doors=current_doors,
                    doors_reduced=doors_reduced,
                )

                if temp_ok:
                    # Temperature recovered — auto resolve
                    await self._resolve(
                        task,
                        f"Temperatura normalizou: {current_temp}°C (dentro do limite {threshold}°C). "
                        f"Aberturas de porta: {current_doors}/hora. "
                        f"Causa operacional confirmada — nenhuma ação necessária.",
                    )
                elif doors_reduced and not temp_ok:
                    # Doors reduced but temp still high → possible technical issue
                    await self._escalate(
                        task,
                        f"Temperatura ainda elevada ({current_temp}°C) mesmo com redução "
                        f"de aberturas de porta ({current_doors}/hora). "
                        f"Possível problema técnico — escalando para notificação.",
                    )
                else:
                    # Both still high — operational cause continues
                    # Give one more chance with shorter delay
                    if not task.observation_id.endswith("_r2"):
                        retry_task = ObservationTask(
                            observation_id=f"{task.observation_id}_r2",
                            event_id=task.event_id,
                            asset_id=task.asset_id,
                            tenant_id=task.tenant_id,
                            metric=task.metric,
                            original_value=task.original_value,
                            threshold=task.threshold,
                            delay_minutes=max(15, task.delay_minutes // 2),
                            original_reasoning=task.original_reasoning + [
                                f"🔄 Re-observação: temp={current_temp}°C, portas={current_doors}/h — "
                                f"condições operacionais persistem, reagendando verificação."
                            ],
                        )
                        task.status = "RE_OBSERVING"
                        task.resolution_reason = (
                            f"Condições operacionais persistem (temp={current_temp}°C, "
                            f"portas={current_doors}/h). Reagendada verificação em "
                            f"{retry_task.delay_minutes}min."
                        )
                        self.schedule(retry_task)
                        logger.info(
                            "observation_rescheduled",
                            observation_id=task.observation_id,
                            retry_id=retry_task.observation_id,
                            retry_delay=retry_task.delay_minutes,
                        )
                    else:
                        # Already retried once, escalate
                        await self._escalate(
                            task,
                            f"Condições operacionais persistem após reavaliação "
                            f"(temp={current_temp}°C, portas={current_doors}/hora). "
                            f"Escalando para notificação.",
                        )

        except Exception as e:
            await self._escalate(task, f"Erro ao consultar Atlas: {e}")

    async def _resolve(self, task: ObservationTask, reason: str) -> None:
        """Mark observation as auto-resolved."""
        task.status = "AUTO_RESOLVED"
        task.resolution_reason = reason
        task.resolved_at = datetime.now(timezone.utc)

        logger.info(
            "observation_auto_resolved",
            observation_id=task.observation_id,
            event_id=task.event_id,
            asset_id=task.asset_id,
            reason=reason,
        )

        # Update reasoning store
        self._update_reasoning_store(task)

    async def _escalate(self, task: ObservationTask, reason: str) -> None:
        """Escalate observation — re-trigger as NOTIFICATION."""
        task.status = "ESCALATED"
        task.resolution_reason = reason
        task.resolved_at = datetime.now(timezone.utc)

        logger.warning(
            "observation_escalated",
            observation_id=task.observation_id,
            event_id=task.event_id,
            asset_id=task.asset_id,
            reason=reason,
        )

        # Re-trigger the event with NOTIFICATION action
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = {
                    "tenant_id": task.tenant_id,
                    "asset_id": task.asset_id,
                    "metric": task.metric,
                    "value": task.original_value,
                    "severity": "HIGH",
                    "payload": {
                        "source": "CAOS_OBSERVER",
                        "description": (
                            f"Reavaliação após observação de {task.delay_minutes}min: {reason}"
                        ),
                        "original_event_id": task.event_id,
                        "observation_id": task.observation_id,
                    },
                }

                r = await client.post(
                    "http://localhost:8080/v1/events/trigger",
                    json=payload,
                )
                logger.info(
                    "observation_escalation_triggered",
                    observation_id=task.observation_id,
                    status_code=r.status_code,
                )
        except Exception as e:
            logger.error(
                "observation_escalation_failed",
                observation_id=task.observation_id,
                error=str(e),
            )

        # Update reasoning store
        self._update_reasoning_store(task)

    def _update_reasoning_store(self, task: ObservationTask) -> None:
        """Update the reasoning store with observation result."""
        try:
            from caos.api.reasoning import get_reasoning_store

            store = get_reasoning_store()
            entry = store.get_by_id(task.event_id)

            if entry:
                # Add observation result to reasoning trace
                icon = "✅" if task.status == "AUTO_RESOLVED" else "⚠️"
                entry.reasoning_trace.append(
                    f"{icon} OBSERVE [{task.status}] após {task.delay_minutes}min: "
                    f"{task.resolution_reason}"
                )

                # Save updated store to disk
                store._save_to_disk()

                logger.info(
                    "observation_reasoning_updated",
                    observation_id=task.observation_id,
                    event_id=task.event_id,
                    status=task.status,
                )

        except Exception as e:
            logger.error(
                "observation_reasoning_update_failed",
                observation_id=task.observation_id,
                error=str(e),
            )

    def get_pending(self) -> list[dict[str, Any]]:
        """Get list of all observations (pending and resolved)."""
        return [
            {
                "observation_id": t.observation_id,
                "event_id": t.event_id,
                "asset_id": t.asset_id,
                "metric": t.metric,
                "original_value": t.original_value,
                "delay_minutes": t.delay_minutes,
                "status": t.status,
                "resolution_reason": t.resolution_reason,
                "created_at": t.created_at.isoformat(),
                "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
            }
            for t in self._pending.values()
        ]

    def cancel(self, observation_id: str) -> bool:
        """Cancel a pending observation."""
        if observation_id in self._background_tasks:
            self._background_tasks[observation_id].cancel()
            return True
        return False


def get_observer() -> ObserverScheduler:
    """Get the global ObserverScheduler singleton."""
    return ObserverScheduler.get_instance()
