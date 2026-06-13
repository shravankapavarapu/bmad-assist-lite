"""Exception hierarchy for the parallel execution module."""


from bmad_assist_lite.core.exceptions import BmadAssistError


class ParallelError(BmadAssistError):
    """Base exception for all parallel execution errors."""

    pass
