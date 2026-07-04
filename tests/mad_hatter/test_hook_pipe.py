"""
Execute-through-the-pipe coverage for the single-value, data-only hook contract.

`tests/ambient/test_verbs.py` already covers the no-op (unregistered) path; this
file drives the real pipe: in-place mutation, wholesale replacement, priority
ordering + error isolation, the single-argument calling convention, and the
request-scoped ambient (`user`) contract.

Hooks are registered directly into the booted cat's `mad_hatter.hooks` (the same
structure plugin discovery fills). The `client` fixture boots a core-only cat per
test, so each test starts from an empty hook catalog.
"""

import asyncio
from types import SimpleNamespace

import pytest

from cat.ambient import execute_hook
from cat.ambient.runtime import ccat
from cat.ambient.context_vars import Ctx, use_ctx
from cat.mad_hatter.decorators.hook import Hook
from cat.types import Task, TaskResult
from cat import user


def _register(name, func, priority=1):
    """Register a handler on the live mad_hatter, keeping the priority sort.

    Also registers a stub plugin under the handler's plugin_id so the pipe's
    error-isolation branch (which looks up `self.plugins[plugin_id]`) resolves.
    """
    mad_hatter = ccat().mad_hatter
    mad_hatter.plugins.setdefault(
        "test",
        SimpleNamespace(plugin_specific_error_message=lambda: "test plugin error"),
    )
    h = Hook(name=name, func=func, priority=priority)
    h.plugin_id = "test"
    mad_hatter.hooks.setdefault(name, []).append(h)
    mad_hatter.hooks[name].sort(key=lambda x: x.priority, reverse=True)
    return h


# --- in-place mutation / return semantics ----------------------------------

def test_mutate_in_place_without_return_survives(client):
    """A handler mutates the piped object and returns nothing; the change and the
    same object reference survive to the caller."""
    def tagger(task):
        task.args["seen"] = True
        # no return

    _register("before_agent_run", tagger)

    piped = Task()
    out = asyncio.run(execute_hook("before_agent_run", piped))

    assert out is piped               # same reference kept on a None return
    assert out.args["seen"] is True


def test_return_replaces_object_wholesale(client):
    """Returning a value replaces the piped object for the caller."""
    replacement = TaskResult()

    def replacer(result):
        return replacement

    _register("after_agent_run", replacer)

    out = asyncio.run(execute_hook("after_agent_run", TaskResult()))
    assert out is replacement


# --- priority order + error isolation --------------------------------------

def test_priority_order_and_error_isolation(client):
    """Three handlers run high-priority-first; a raising handler is isolated and
    the pipe continues, so surviving handlers' mutations all land."""
    order = []

    def h_high(v):
        order.append("high")
        v.append("high")

    def h_mid(v):
        order.append("mid")
        raise RuntimeError("boom")  # isolated by the pipe

    def h_low(v):
        order.append("low")
        v.append("low")

    _register("before_agent_run", h_high, priority=2)
    _register("before_agent_run", h_mid, priority=1)
    _register("before_agent_run", h_low, priority=0)

    out = asyncio.run(execute_hook("before_agent_run", []))

    assert order == ["high", "mid", "low"]   # priority order, mid still ran
    assert out == ["high", "low"]            # mid's error left the value intact


# --- single-argument calling convention ------------------------------------

def test_second_positional_param_breaks_handler(client):
    """The pipe passes exactly one argument, so a v1 `(value, caller)` handler
    cannot be called with one arg (TypeError), and through the pipe that error is
    isolated — the value passes unchanged."""
    def two_arg(value, caller):  # v1 fossil signature
        return value

    with pytest.raises(TypeError):
        two_arg("only-one")

    _register("before_agent_run", two_arg)
    piped = Task()
    out = asyncio.run(execute_hook("before_agent_run", piped))
    assert out is piped  # handler errored and was isolated; value untouched


# --- ambient contract: user is request-scoped ------------------------------

def test_user_resolves_inside_request_hook(client):
    """Inside a request-scoped hook, `from cat import user` resolves the current
    request's user."""
    captured = {}

    def read_user(task):
        captured["id"] = user.id

    _register("before_agent_run", read_user)

    with use_ctx(Ctx(user=SimpleNamespace(id="tester"))):
        asyncio.run(execute_hook("before_agent_run", Task()))

    assert captured["id"] == "tester"


def test_user_raises_inside_bootstrap_hook(client):
    """A hook that fires with no active request (bootstrap/reload) sees `user`
    raise a clear RuntimeError."""
    captured = {}

    def boot_handler(value):
        try:
            _ = user.id
        except RuntimeError as e:
            captured["error"] = str(e)

    _register("after_plugins_reload", boot_handler)

    asyncio.run(execute_hook("after_plugins_reload", None))

    assert "request" in captured["error"].lower()
