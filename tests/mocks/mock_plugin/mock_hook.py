from cat.mad_hatter.decorators import hook


@hook(priority=2)
def after_agent_run(result):
    # Data-only core hook: one argument (the piped TaskResult), no `caller`.
    # Mutate in place; the change survives without a return.
    result.args["mock_hook_priority_2"] = True
