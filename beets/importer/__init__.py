# This file is part of beets.
# Copyright 2016, Adrian Sampson.
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.

"""Provides the basic, interface-agnostic workflow for importing and
autotagging music files.
"""

from .session import ImportAbortError, ImportSession
from .tasks import (
    Action,
    ArchiveImportTask,
    ImportTask,
    SentinelImportTask,
    SingletonImportTask,
)

# Note: Stages are not exposed to the public API

__all__ = [
    "Action",
    "ArchiveImportTask",
    "ImportAbortError",
    "ImportSession",
    "ImportTask",
    "SentinelImportTask",
    "SingletonImportTask",
]

# Re-export diagnostics helpers so importer API users can access them
# without importing the submodule explicitly.
from .diagnostics import (  # noqa: E402
    DIAG_LEVEL_DEBUG,
    DIAG_LEVEL_ERROR,
    DIAG_LEVEL_INFO,
    DIAG_LEVEL_WARNING,
    DIAG_STAGE_DUPLICATE,
    DIAG_STAGE_FILE_IMPORT,
    DIAG_STAGE_GENERAL,
    DIAG_STAGE_METADATA_MATCH,
    DIAG_STAGE_PATH_PARSE,
    DIAG_STAGE_USER_CHOICE,
    DiagnosticEvent,
    DiagnosticTraceWriter,
    ImportDiagnosticCollector,
    create_collector_for_session,
    create_writer_for_session,
    default_diagnostic_trace_path,
    diagnostics_config,
    should_enable_diagnostics,
)

__all__.extend(
    [
        "DIAG_LEVEL_DEBUG",
        "DIAG_LEVEL_ERROR",
        "DIAG_LEVEL_INFO",
        "DIAG_LEVEL_WARNING",
        "DIAG_STAGE_DUPLICATE",
        "DIAG_STAGE_FILE_IMPORT",
        "DIAG_STAGE_GENERAL",
        "DIAG_STAGE_METADATA_MATCH",
        "DIAG_STAGE_PATH_PARSE",
        "DIAG_STAGE_USER_CHOICE",
        "DiagnosticEvent",
        "DiagnosticTraceWriter",
        "ImportDiagnosticCollector",
        "create_collector_for_session",
        "create_writer_for_session",
        "default_diagnostic_trace_path",
        "diagnostics_config",
        "should_enable_diagnostics",
    ]
)
