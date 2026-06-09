Music Importer
==============

The importer component is responsible for the user-centric workflow that adds
music to a library. This is one of the first aspects that a user experiences
when using beets: it finds music in the filesystem, groups it into albums, finds
corresponding metadata in MusicBrainz, asks the user for intervention, applies
changes, and moves/copies files. A description of its user interface is given in
:doc:`/guides/tagger`.

The workflow is implemented in the ``beets.importer`` module and is distinct
from the core logic for matching MusicBrainz metadata (in the ``beets.autotag``
module). The workflow is also decoupled from the command-line interface with the
hope that, eventually, other (graphical) interfaces can be bolted onto the same
importer implementation.

The importer is multithreaded and follows the pipeline pattern. Each pipeline
stage is a Python coroutine. The ``beets.util.pipeline`` module houses a
generic, reusable implementation of a multithreaded pipeline.

.. _import-audit-summary:

Import Audit Summary
--------------------

Each :class:`~beets.importer.ImportSession` exposes an
:class:`~beets.importer.ImportAuditSummary` instance (the ``audit_summary``
attribute) that aggregates how every import task was resolved during the
session. At the end of the pipeline the summary is both written to the
importer log and emitted via the ``import_audit_summary`` event (see
:ref:`plugin_events`).

The data class lives in :mod:`beets.importer` and is part of the public
API:

.. autoclass:: beets.importer.ImportAuditSummary
   :members:
   :member-order: bysource
   :undoc-members:

Count fields
~~~~~~~~~~~~

Every counter below corresponds to a single import task. A task contributes
to exactly one of the main buckets (``applied`` / ``asis`` / ``skipped``)
and, if it entered duplicate resolution, to exactly one of the
``duplicate_*`` buckets. Additionally, the ``needs_review`` counter is
*orthogonal* — tasks in any bucket may also be flagged as "reviewed by a
human".

.. list-table::
   :header-rows: 1

   * - Field
     - Type
     - Meaning

   * - ``total_tasks``
     - ``int``
     - Number of tasks that reached ``log_choice``. Sentinel tasks are
       excluded.

   * - ``applied``
     - ``int``
     - Tasks for which new metadata was fetched and written (i.e. an
       ``AlbumMatch`` or ``TrackMatch`` was chosen).

   * - ``asis``
     - ``int``
     - Tasks imported with their on-disk metadata untouched.

   * - ``skipped``
     - ``int``
     - Tasks that the user or a plugin discarded outright.

   * - ``duplicate_replace``
     - ``int``
     - Duplicate cases where the existing library entry was removed and
       replaced by the newly-imported one.

   * - ``duplicate_keep``
     - ``int``
     - Duplicate cases where both the old and the new entry were kept.

   * - ``duplicate_skip``
     - ``int``
     - Duplicate cases where the new candidate was skipped (the existing
       library entry stays).

   * - ``needs_review``
     - ``int``
     - Tasks that required a human decision because the auto-tagger could
       not reach a strong-enough recommendation or a duplicate conflict
       arose. Non-interactive modes (e.g. ``--quiet``) never raise this
       flag.

Detail fields
~~~~~~~~~~~~~

For every counter above there is a matching ``*_details`` list containing
``(path_display, description)`` tuples. These allow plugins to produce
actionable post-import reports:

* ``applied_details``, ``asis_details``, ``skipped_details``
* ``duplicate_replace_details``, ``duplicate_keep_details``,
  ``duplicate_skip_details``
* ``needs_review_details``

``path_display`` is a human-readable rendering of the source paths
(produced by :func:`beets.util.displayable_path`). ``description`` is a
short ``"Artist — Album"`` / ``"Artist — Title"`` summary derived from
whichever metadata was selected for the task.

The lists are parallel to the counters: the length of ``applied_details``
equals ``applied``, and so on. Subscribers can iterate them, emit
ticket-formatted text, or write the payload to a downstream service.

Public methods
~~~~~~~~~~~~~~

.. automethod:: beets.importer.ImportAuditSummary.record_choice
.. automethod:: beets.importer.ImportAuditSummary._task_description

Downstream subscription
~~~~~~~~~~~~~~~~~~~~~~~

Register a listener on the ``import_audit_summary`` event inside your
plugin's constructor. The handler receives the session and the fully
populated :class:`~beets.importer.ImportAuditSummary` object exactly once,
after all tasks have been processed:

.. code-block:: python

    from beets.plugins import BeetsPlugin
    from beets.importer import ImportAuditSummary


    class PostImportReporter(BeetsPlugin):
        def __init__(self):
            super().__init__()
            self.register_listener(
                "import_audit_summary", self._write_report
            )

        def _write_report(self, session, summary: ImportAuditSummary):
            if summary.skipped or summary.needs_review:
                self._log.warning(
                    "Import finished with {} skipped and {} "
                    "requiring review",
                    summary.skipped,
                    summary.needs_review,
                )

Event guarantees:

* The event fires **only** when ``summary.total_tasks > 0`` — empty
  sessions (including aborted-before-start ones) do not emit it.
* The event fires even when the user explicitly aborts mid-session via
  ``Ctrl-C``: any tasks resolved *before* the abort are reflected in the
  counters.
* The summary object is shared; do not mutate it after receiving the
  event — it is intended for read-only consumption.
