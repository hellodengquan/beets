"""The 'move' command: Move/copy files to the library or a new base directory."""

from __future__ import annotations

import os
from collections import defaultdict
from typing import TYPE_CHECKING

from beets import logging, ui
from beets.exceptions import UserError
from beets.util import MoveOperation, displayable_path, normpath, syspath
from beets.util.diff import colordiff
from beets.util.functemplate import Template
from beets.util.pathformats import PF_KEY_DEFAULT

from .utils import do_query

if TYPE_CHECKING:
    from beets.util import PathLike

log = logging.getLogger("beets")


def _extract_template_fields(path_format):
    """Extract variable field names from a path format Template."""
    if isinstance(path_format, Template):
        tmpl = path_format
    else:
        from beets.util.functemplate import template

        tmpl = template(path_format)
    _, varnames, _ = tmpl.expr.translate()
    return varnames


def _get_matched_path_format(obj, path_formats):
    """Return the (query_string, path_format) that matches obj."""
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
    return PF_KEY_DEFAULT, path_formats[0][1]


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


def preview_paths(lib, dest_path, query, album):
    """Preview where files would be moved based on path format templates.

    For each matched item/album, show the source -> destination path,
    the matched path format rule, any missing template fields, and
    detect destination conflicts.
    """
    dest = os.fsencode(dest_path) if dest_path else dest_path

    try:
        items, albums = do_query(lib, query, album, False)
    except UserError:
        ui.print_("No matching items found.")
        return

    objs = albums if album else items
    if not objs:
        ui.print_("No matching items found.")
        return

    path_formats = lib.path_formats
    entity = "album" if album else "item"

    dest_map = defaultdict(list)
    path_changes = []
    annotations = []

    for obj in objs:
        if album:
            obj_items = list(obj.items())
            for item in obj_items:
                item_dest = item.destination(basedir=dest)
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
            obj_dest = obj.destination(basedir=dest)
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

    conflicts = {
        d: sources for d, sources in dest_map.items() if len(sources) > 1
    }

    ui.print_(
        f"Path format preview for {len(objs)} {entity}"
        f"{'s' if len(objs) != 1 else ''}:"
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

    any_missing = any("missing" in a for a in annotations)
    if any_missing:
        ui.print_("Missing/empty fields detected (shown as original")
        ui.print_("template text in output, e.g. $fieldname):")
        seen = set()
        for ann in annotations:
            if "missing" in ann:
                for field in ann["missing"]:
                    if field not in seen:
                        seen.add(field)
                        ui.print_(f"  ${field}")
        ui.print_("")


def show_path_changes(path_changes):
    """Given a list of tuples (source, destination) that indicate the
    path changes, log the changes as INFO-level output to the beets log.
    The output is guaranteed to be unicode.

    Every pair is shown on a single line if the terminal width permits it,
    else it is split over two lines. E.g.,

    Source -> Destination

    vs.

    Source
      -> Destination
    """
    sources, destinations = zip(*path_changes)

    # Ensure unicode output
    sources = list(map(displayable_path, sources))
    destinations = list(map(displayable_path, destinations))

    # Calculate widths for terminal split
    col_width = (ui.term_width() - len(" -> ")) // 2
    max_width = len(max(sources + destinations, key=len))

    if max_width > col_width:
        # Print every change over two lines
        for source, dest in zip(sources, destinations):
            color_source, color_dest = colordiff(source, dest)
            ui.print_(f"{color_source} \n  -> {color_dest}")
    else:
        # Print every change on a single line, and add a header
        title_pad = max_width - len("Source ") + len(" -> ")

        ui.print_(f"Source {' ' * title_pad} Destination")
        for source, dest in zip(sources, destinations):
            pad = max_width - len(source)
            color_source, color_dest = colordiff(source, dest)
            ui.print_(f"{color_source} {' ' * pad} -> {color_dest}")


def move_items(
    lib,
    dest_path: PathLike,
    query,
    copy,
    album,
    pretend,
    confirm=False,
    export=False,
):
    """Moves or copies items to a new base directory, given by dest. If
    dest is None, then the library's base directory is used, making the
    command "consolidate" files.
    """
    dest = os.fsencode(dest_path) if dest_path else dest_path
    items, albums = do_query(lib, query, album, False)
    objs = albums if album else items
    num_objs = len(objs)

    # Filter out files that don't need to be moved.
    def isitemmoved(item):
        return item.path != item.destination(basedir=dest)

    def isalbummoved(album):
        return any(isitemmoved(i) for i in album.items())

    objs = [o for o in objs if (isalbummoved if album else isitemmoved)(o)]
    num_unmoved = num_objs - len(objs)
    # Report unmoved files that match the query.
    unmoved_msg = ""
    if num_unmoved > 0:
        unmoved_msg = f" ({num_unmoved} already in place)"

    copy = copy or export  # Exporting always copies.
    action = "Copying" if copy else "Moving"
    act = "copy" if copy else "move"
    entity = "album" if album else "item"
    log.info(
        "{} {} {}{}{}.",
        action,
        len(objs),
        entity,
        "s" if len(objs) != 1 else "",
        unmoved_msg,
    )
    if not objs:
        return

    if pretend:
        if album:
            show_path_changes(
                [
                    (item.path, item.destination(basedir=dest))
                    for obj in objs
                    for item in obj.items()
                ]
            )
        else:
            show_path_changes(
                [(obj.path, obj.destination(basedir=dest)) for obj in objs]
            )
    else:
        if confirm:
            objs = ui.input_select_objects(
                f"Really {act}",
                objs,
                lambda o: show_path_changes(
                    [(o.path, o.destination(basedir=dest))]
                ),
            )

        for obj in objs:
            log.debug("moving: {.filepath}", obj)

            if export:
                # Copy without affecting the database.
                obj.move(
                    operation=MoveOperation.COPY, basedir=dest, store=False
                )
            else:
                # Ordinary move/copy: store the new path.
                if copy:
                    obj.move(operation=MoveOperation.COPY, basedir=dest)
                else:
                    obj.move(operation=MoveOperation.MOVE, basedir=dest)


def move_func(lib, opts, args):
    dest = opts.dest
    if dest is not None:
        dest = normpath(dest)
        if not os.path.isdir(syspath(dest)):
            raise UserError(f"no such directory: {displayable_path(dest)}")

    if opts.preview:
        preview_paths(lib, dest, args, opts.album)
        return

    move_items(
        lib,
        dest,
        args,
        opts.copy,
        opts.album,
        opts.pretend,
        opts.timid,
        opts.export,
    )


move_cmd = ui.Subcommand("move", help="move or copy items", aliases=("mv",))
move_cmd.parser.add_option(
    "-d", "--dest", metavar="DIR", dest="dest", help="destination directory"
)
move_cmd.parser.add_option(
    "-c",
    "--copy",
    default=False,
    action="store_true",
    help="copy instead of moving",
)
move_cmd.parser.add_option(
    "-p",
    "--pretend",
    default=False,
    action="store_true",
    help="show how files would be moved, but don't touch anything",
)
move_cmd.parser.add_option(
    "-t",
    "--timid",
    dest="timid",
    action="store_true",
    help="always confirm all actions",
)
move_cmd.parser.add_option(
    "-e",
    "--export",
    default=False,
    action="store_true",
    help="copy without changing the database path",
)
move_cmd.parser.add_option(
    "--preview",
    default=False,
    action="store_true",
    help="preview destination paths with conflict and missing field detection",
)
move_cmd.parser.add_album_option()
move_cmd.func = move_func
