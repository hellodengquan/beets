# Plugin Doctor (beet check / beet doctor)

The `beet check` (alias `beet doctor`) command performs a health check on your
configured plugins and reports issues along with fix suggestions. The JSON
output mode (`--format json`) provides a machine-readable report suitable for
CI pipelines and alerting systems.

## Usage

```bash
# Human-readable text output (default)
beet check

# JSON output for automation
beet check --format json
beet check -f json

# Check specific plugins only
beet check chroma fetchart
beet check --format json chroma
```

## JSON Schema Reference

The JSON output follows a stable schema with a `schema_version` field that uses
[Semantic Versioning](https://semver.org/):

- **Major version** incremented for breaking changes (removed/renamed fields,
  narrowed enum values)
- **Minor version** incremented for backward-compatible additions (new fields,
  new enum values)
- **Patch version** reserved for bug fixes that do not change the schema

### Top-Level Object

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | string | Schema version in SemVer format (`MAJOR.MINOR.PATCH`) |
| `ok_count` | integer | Total number of passing checks |
| `warning_count` | integer | Total number of warning-level issues |
| `error_count` | integer | Total number of error-level issues |
| `has_errors` | boolean | `true` if any error-level checks exist |
| `checks` | object | Results grouped by check category (see below) |

The `checks` object contains one key per category that has results. Categories
with no results are omitted. Each category value is an array of check item
objects.

### Check Item Object

Each entry in a category array has the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `plugin` | string | Name of the plugin this check relates to |
| `category` | string | Check category (see [Categories](#categories)) |
| `status` | string | Check result status (see [Statuses](#statuses)) |
| `message` | string | Human-readable description of the check result |
| `suggestion` | string | Recommended fix, or empty string when not applicable |

### Categories

Valid values for `category` (and `checks` object keys):

| Value | Description |
|-------|-------------|
| `enabled` | Whether the plugin is enabled and loaded successfully |
| `dependency` | Whether required optional dependencies are installed |
| `command` | Whether subcommands are registered correctly and without conflicts |
| `config` | Whether plugin configuration is valid and field names don't collide |

### Statuses

Valid values for `status`:

| Value | Description |
|-------|-------------|
| `ok` | Check passed — no action needed |
| `warning` | Potential issue — review and fix if desired |
| `error` | Definite problem — the plugin likely won't work correctly |

### Example Output

```json
{
  "schema_version": "1.0.0",
  "ok_count": 3,
  "warning_count": 1,
  "error_count": 0,
  "has_errors": false,
  "checks": {
    "enabled": [
      {
        "plugin": "musicbrainz",
        "category": "enabled",
        "status": "ok",
        "message": "Plugin 'musicbrainz' is loaded",
        "suggestion": ""
      }
    ],
    "dependency": [
      {
        "plugin": "musicbrainz",
        "category": "dependency",
        "status": "ok",
        "message": "Plugin 'musicbrainz' has no extra dependencies",
        "suggestion": ""
      }
    ],
    "config": [
      {
        "plugin": "musicbrainz",
        "category": "config",
        "status": "ok",
        "message": "Plugin 'musicbrainz' configuration is valid",
        "suggestion": ""
      }
    ]
  }
}
```

## Version Changelog

Each release of the schema is documented below. New entries are appended in
reverse chronological order (newest first).

## 1.0.0

Initial release of the Plugin Doctor JSON schema.

**Added:**
- `schema_version` field (SemVer string: `"1.0.0"`)
- Top-level fields: `ok_count`, `warning_count`, `error_count`, `has_errors`, `checks`
- Four check categories: `enabled`, `dependency`, `command`, `config`
- Per-item fields: `plugin`, `category`, `status`, `message`, `suggestion`
- Three status values: `ok`, `warning`, `error`
- `beet check` command with `--format text` (default) and `--format json` options
- `beet doctor` alias for the `check` command
