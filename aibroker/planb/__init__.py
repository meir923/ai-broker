"""Plan B — US-focused trader track (isolated from Plan A).

Public entrypoints for tooling; the web UI uses `/api/planb/*`.
"""

from aibroker.planb.config import PlanBConfig, load_plan_b_config

__all__ = ["PlanBConfig", "load_plan_b_config"]
