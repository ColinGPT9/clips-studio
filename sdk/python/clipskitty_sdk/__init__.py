# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Clips Kitty plugin SDK.

A pipeline plugin is a program Clips Kitty starts with a job folder. It reads
the job (`read_job()`), reports progress (`job.progress()`), returns moments
(`job.add_range()`), rates and describes the moments it was given
(`job.rate()`, `job.understand()`) or suggests edits for the clips made from
them (`job.suggest_edit()`), and finishes (`job.finish()`). This package
does those things with the Python standard library only, so a plugin can
depend on it without depending on Clips Kitty. The contract it follows is in
`contract.py`.
"""

from .contract import PLUGIN_API_VERSION, SUPPORTED_PLUGIN_APIS, ContractError, check_job, check_result
from .job import EditSuggestion, Job, Moment, Suggested, read_job, run

__version__ = "1.3.0"

__all__ = [
    "PLUGIN_API_VERSION",
    "SUPPORTED_PLUGIN_APIS",
    "ContractError",
    "EditSuggestion",
    "Job",
    "Moment",
    "Suggested",
    "__version__",
    "check_job",
    "check_result",
    "read_job",
    "run",
]
