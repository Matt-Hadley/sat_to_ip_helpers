"""Exceptions raised by the pipeline. Anything else reaching the CLI is a bug."""


class PipelineError(Exception):
    """Base for every expected failure: bad config, a failed check, an unreachable service."""


class ConfigError(PipelineError):
    """The config file is missing, unreadable or invalid. The message names the offending key."""


class CheckError(PipelineError):
    """A safety check refused to let the run change something."""


class IntegrationError(PipelineError):
    """An external system (receiver, guide feed, DVR) answered in a way we can't use."""
