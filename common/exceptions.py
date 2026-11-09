class ScenarioChecksFailed(AssertionError):
    """Scenario-level failed checks without regular pytest longrepr output."""


class ScenarioChecksErrored(RuntimeError):
    """Scenario-level execution errors without regular pytest longrepr output."""
