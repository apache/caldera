from app.utility.base_world import BaseWorld

name = 'Malbas'
description = 'Malomatia BAS library: Qatar-sector APT adversary profiles, per-tactic test libraries, and custom abilities'
address = None
access = BaseWorld.Access.RED


async def enable(services):
    """Data-only plugin.

    Caldera's data service automatically loads everything under
    plugins/malbas/data/ (adversaries, abilities, payloads) once this
    plugin name is listed under `plugins:` in conf/*.yml, so there is
    nothing to wire up here.
    """
    pass
