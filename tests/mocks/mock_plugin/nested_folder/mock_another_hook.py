from cat.mad_hatter.decorators import hook


@hook(priority=3)
def after_agent_run(result):
    # Second handler for the same core hook, higher priority (runs first).
    result.args["mock_hook_priority_3"] = True
