from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from beets import ui
from beets.exceptions import UserError
from beets.util import displayable_path
from beets.util.diff import colordiff
from beets.util.functemplate import Template, template
from beets.util.pathformats import PF_KEY_DEFAULT

if TYPE_CHECKING:
    from collections.abc import Callable

    from beets.library import Album, Item, Library
    from beets.util.pathformats import PathFormat


def _extract_template_fields(path_format):
    """Extract variable field names from a path format Template."""
    if isinstance(path_format, Template):
        tmpl = path_format
    else:
        tmpl = template(path_format)
    _, varnames, _ = tmpl.expr.translate()
    return varnames


def _get_matched_path_format(obj, path_formats: list[PathFormat]):
    """Return the (query_string, path_format) that matches obj.

    Always falls back to the 'default' rule if no query-based rule matches.
    Raises UserError if there is no 'default' rule in path_formats.
    """
    from beets.library.queries import parse_query_string

    for query, path_format in path_formats:
        if query == PF_KEY_DEFAULT:
            continue
        q, _ = parse_query_string(query, type(obj))
        if q.match(obj):
            return query, path_format
    for query, path_format in path_formats:
        if query == PF_KEY_DEFAULT:
            return query, path_format
    raise UserError(
        "No default path format found. "
        "Please add a 'default' rule to your paths configuration."
    )


def _check_missing_fields(obj, field_names):
    """Return a list of field names that are empty or None on the object."""
    formatted = obj.formatted(for_path=True)
    missing = []
    for name in field_names:
        if name not in formatted:
            missing.append(name)
        else:
            val = formatted[name]
            if val is None or str(val).strip() == "":
                missing.append(name)
    return missing


def preview_paths(
    lib: Library,
    query,
    album: bool,
    dest_getter: Callable[[Item | Album], bytes],
    path_formats: list[PathFormat] | None = None,
    empty_msg: str = "No matching items found.",
):
    """Preview where files would be moved based on path format templates.

    For each matched item/album, show the source -> destination path,
    the matched path format rule, any missing template fields, and
    detect destination conflicts.

    Parameters:
    - lib: The library instance.
    - query: The query to match items/albums.
    - album: If True, query albums; otherwise query items.
    - dest_getter: A callable that takes an item/album and returns its
      destination path bytes.
    - path_formats: Optional list of path formats to use for matching.
      Defaults to lib.path_formats.
    - empty_msg: Message to show when no items match the query.
    """
    from beets.ui.commands.utils import do_query

    try:
        items, albums = do_query(lib, query, album, False)
    except UserError:
        ui.print_(empty_msg)
        ui.print_("Check your query syntax or try a different search.")
        return

    objs = albums if album else items
    if not objs:
        ui.print_(empty_msg)
        ui.print_("Check your query syntax or try a different search.")
        return

    path_formats = path_formats or lib.path_formats
    entity = "album" if album else "item"

    dest_map = defaultdict(list)
    path_changes = []
    annotations = []

    for obj in objs:
        if album:
            obj_items = list(obj.items())
            for item in obj_items:
                item_dest = dest_getter(item)
                matched_query, matched_format = _get_matched_path_format(
                    item, path_formats
                )
                field_names = _extract_template_fields(matched_format)
                missing = _check_missing_fields(item, field_names)
                path_changes.append((item.path, item_dest))
                dest_map[displayable_path(item_dest)].append(
                    displayable_path(item.path)
                )
                ann = {"matched_query": matched_query}
                if missing:
                    ann["missing"] = missing
                annotations.append(ann)
        else:
            obj_dest = dest_getter(obj)
            matched_query, matched_format = _get_matched_path_format(
                obj, path_formats
            )
            field_names = _extract_template_fields(matched_format)
            missing = _check_missing_fields(obj, field_names)
            path_changes.append((obj.path, obj_dest))
            dest_map[displayable_path(obj_dest)].append(
                displayable_path(obj.path)
            )
            ann = {"matched_query": matched_query}
            if missing:
                ann["missing"] = missing
            annotations.append(ann)

    if not path_changes:
        ui.print_(
            f"All {len(objs)} {entity}"
            f"{'s' if len(objs) != 1 else ''} are already in their "
            f"destination paths. No files will be moved."
        )
        return

    conflicts = {
        d: sources for d, sources in dest_map.items() if len(sources) > 1
    }

    ui.print_(
        f"Path format preview for {len(path_changes)} file"
        f"{'s' if len(path_changes) != 1 else ''}"
        f" ({len(objs)} {entity}{'s' if len(objs) != 1 else ''}):"
    )
    ui.print_("")

    for i, (source, destination) in enumerate(path_changes):
        src_str = displayable_path(source)
        dst_str = displayable_path(destination)
        ann = annotations[i]

        conflict_marker = ""
        if dst_str in conflicts:
            conflict_marker = " [CONFLICT]"

        missing_marker = ""
        if "missing" in ann:
            missing_marker = f" [MISSING: {', '.join(ann['missing'])}]"

        rule = ann["matched_query"]
        color_source, color_dest = colordiff(src_str, dst_str)
        ui.print_(f"  {color_source}")
        ui.print_(f"    -> {color_dest}{conflict_marker}{missing_marker}")
        ui.print_(f"       rule: {rule}")

    ui.print_("")

    if conflicts:
        ui.print_("Destination conflicts detected:")
        for dst, sources in conflicts.items():
            ui.print_(f"  {dst}")
            for src in sources:
                ui.print_(f"    <- {src}")
        ui.print_("")
        ui.print_(
            "Note: Multiple files map to the same destination path. "
            "Moving these files may cause data loss."
        )
        ui.print_("")

    any_missing = any("missing" in a for a in annotations)
    if any_missing:
        ui.print_(
            "Missing/empty fields detected (these appear as raw "
            "template text, e.g. $fieldname, in the output path):"
        )
        seen = set()
        for ann in annotations:
            if "missing" in ann:
                for field in ann["missing"]:
                    if field not in seen:
                        seen.add(field)
                        ui.print_(f"  ${field}")
        ui.print_("")
        ui.print_(
            "Tip: Use `beet fields` to see available fields, or "
            "`beet modify` to fill in missing values."
        )
        ui.print_("")
