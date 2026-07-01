"""Default system prompts for the three agent roles.

Prompts live here alongside the strategies that drive these roles, not in
``hillclimber.toml``. The toml declares *intent* —
which model, harness, scorer, budget, strategy — while the role prompts are
hillclimber's own behaviour and rarely need per-experiment tuning. Keeping them
in source means ``Agent.system_prompt`` can stay optional in the config: a user
who wants to override a role's prompt sets it in the toml, otherwise the loop
falls back to the role default below.

The defaults are deliberately artefact-agnostic. The agents learn what the
artefact is (and how it's scored) from the run context, not from these strings.
"""

from __future__ import annotations

# Proposes the next hypothesis for improving the artefact.
HILLCLIMBER_AGENT = (
    "You are improving a code artefact to raise its eval score. "
    "Inspect the artefact and its eval, then propose one concrete, testable "
    "change that should move the score up. Keep each hypothesis small and "
    "specific so its effect can be measured in a single cycle."
)

# Applies the proposed change to the artefact.
WORKER_AGENT = (
    "You apply a proposed change to the artefact. Make the smallest edit that "
    "realizes the hypothesis, preserve the artefact's public contract and the "
    "eval's interface, and do not touch unrelated code."
)

# Reflects on the score delta and steers the next hypothesis.
REFLECTOR_AGENT = (
    "You reflect on the result of a cycle. Given the score delta and the eval "
    "details, explain what helped or hurt and propose the next hypothesis to "
    "try. Be concrete about why the change moved the score the way it did."
)
