"""Custom exceptions for the sat-to-ip pipeline."""

class PipelineError(Exception):
    """Base exception for all pipeline-related errors."""
    pass

class ConfigurationError(PipelineError):
    """Raised when required configuration (flags, env vars) is missing or invalid."""
    pass

class ServiceConnectionError(PipelineError):
    """Raised when a connection to an external service (Octopus, Channels DVR, KingOfSat) fails."""
    pass

class StepError(PipelineError):
    """Raised when a specific step in the pipeline fails."""
    pass

class StateError(PipelineError):
    """Raised when there is an issue loading or saving state files."""
    pass
