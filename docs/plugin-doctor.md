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

| Field | Type | Allowed Values | Description |
|-------|------|---------------|-------------|
| `plugin` | string | *(any)* | Name of the plugin this check relates to |
| `category` | enum | `enabled`, `dependency`, `command`, `config` | Check category (see [Categories](#categories)) |
| `status` | enum | `ok`, `warning`, `error` | Check result severity (see [Statuses](#statuses)) |
| `message` | string | *(any)* | Human-readable description of the check result |
| `suggestion` | string | *(any)* | Recommended fix, or empty string when not applicable |

### Categories

Valid values for the `category` field (and `checks` object keys). The output
layer guarantees no other values will appear.

| Value | Semantics |
|-------|-----------|
| `enabled` | Plugin enablement and load status — whether the plugin is configured, not disabled, and successfully loaded |
| `dependency` | Optional dependency availability — whether the Python packages required by the plugin are importable |
| `command` | Subcommand registration — whether the plugin's CLI commands are registered and free of name collisions |
| `config` | Configuration validity — whether the plugin config section parses correctly and template fields don't collide with other plugins |

### Statuses

Valid values for the `status` field. The output layer guarantees no other
values will appear.

| Value | Semantics |
|-------|-----------|
| `ok` | Check passed — the plugin is healthy in this dimension, no action needed |
| `warning` | Potential issue detected — the plugin may still function but a configuration conflict or suboptimal state was found; review recommended |
| `error` | Definite problem — the plugin is likely broken or non-functional in this dimension; immediate fix required |

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
- Per-item fields: `plugin`, `category`, `status`, `message`, `suggestion`
- `category` enum: `enabled`, `dependency`, `command`, `config`
- `status` enum: `ok`, `warning`, `error`
- Runtime validation: output layer rejects any enum value outside the declared sets
- `beet check` command with `--format text` (default) and `--format json` options
- `beet doctor` alias for the `check` command
