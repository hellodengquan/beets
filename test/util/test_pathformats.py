import pytest

from beets.util.pathformats import get_path_formats


def test_get_path_formats(config):
    """Ensure the function prepends custom and preserves defaults."""
    # override the default 'singleton' path and add a new one
    config["paths"].set({"singleton": "bar", "new": "hello"})

    path_formats = get_path_formats(config["paths"])
    actual_path_formats = [(key, tmpl.original) for key, tmpl in path_formats]
    assert actual_path_formats == [
        ("singleton:true", "bar"),
        ("new", "hello"),
        # defaults
        ("default", "$albumartist/$album%aunique{}/$track $title"),
        ("comp:true", "Compilations/$album%aunique{}/$track $title"),
    ]


def test_get_path_formats_empty_config(config):
    """Test with empty paths configuration returns defaults."""
    config["paths"].set({})

    path_formats = get_path_formats(config["paths"])
    actual_path_formats = [(key, tmpl.original) for key, tmpl in path_formats]

    default_paths = [
        ("default", "$albumartist/$album%aunique{}/$track $title"),
        ("comp:true", "Compilations/$album%aunique{}/$track $title"),
    ]
    for key, template in default_paths:
        assert (key, template) in actual_path_formats


@pytest.mark.parametrize(
    "path_template",
    [
        "",
        "$empty",
        "$artist//$album",
        "path/with/../relative",
        "path/./current",
        "/absolute/path",
        "path with spaces",
        "path_with_underscores",
        "path-with-dashes",
        "path_with_special_!@#$%^&()",
        "path_with_unicode_你好世界",
        "path/with/very/long/path/components/that/should/be/handled/properly",
    ],
)
def test_get_path_formats_special_characters(config, path_template):
    """Test path formats with various special character patterns."""
    config["paths"].set({"custom": path_template})

    path_formats = get_path_formats(config["paths"])
    actual_path_formats = [(key, tmpl.original) for key, tmpl in path_formats]

    assert ("custom", path_template) in actual_path_formats


@pytest.mark.parametrize(
    "query_key",
    [
        "",
        " ",
        "key with spaces",
        "key_with_underscores",
        "key-with-dashes",
        "key_with_special_!@#$",
        "key_with_unicode_测试",
    ],
)
def test_get_path_formats_query_key_boundary(config, query_key):
    """Test path formats with various query key boundary cases."""
    config["paths"].set({query_key: "$title"})

    path_formats = get_path_formats(config["paths"])
    actual_keys = [key for key, _ in path_formats]

    expected_key = "comp:true" if query_key == "comp" else query_key
    expected_key = "singleton:true" if query_key == "singleton" else expected_key
    assert expected_key in actual_keys


@pytest.mark.parametrize(
    "path_template",
    [
        "%func{}",
        "%func{arg}",
        "%func{$var}",
        "%aunique{,$}",
        "$artist$album",
        "${artist}${album}",
        "$artist/${album}/$title",
    ],
)
def test_get_path_formats_template_syntax(config, path_template):
    """Test path formats with various template syntax patterns."""
    config["paths"].set({"custom": path_template})

    path_formats = get_path_formats(config["paths"])
    actual_path_formats = [(key, tmpl.original) for key, tmpl in path_formats]

    assert ("custom", path_template) in actual_path_formats


@pytest.mark.windows
def test_get_path_formats_windows_paths(config):
    """Test Windows-specific path formats (Windows-only test)."""
    config["paths"].set({"windows": "C:\\Music\\$artist"})

    path_formats = get_path_formats(config["paths"])
    actual_path_formats = [(key, tmpl.original) for key, tmpl in path_formats]

    assert ("windows", "C:\\Music\\$artist") in actual_path_formats


@pytest.mark.posix
def test_get_path_formats_posix_paths(config):
    """Test POSIX-specific path formats (POSIX-only test)."""
    config["paths"].set({"posix": "/mnt/music/$artist"})

    path_formats = get_path_formats(config["paths"])
    actual_path_formats = [(key, tmpl.original) for key, tmpl in path_formats]

    assert ("posix", "/mnt/music/$artist") in actual_path_formats

