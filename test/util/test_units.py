import pytest

from beets.util.units import (
    human_bytes,
    human_seconds,
    human_seconds_short,
    raw_seconds_short,
)


@pytest.mark.parametrize(
    "input_bytes,expected",
    [
        (0, "0.0 B"),
        (30, "30.0 B"),
        (pow(2, 10), "1.0 KiB"),
        (pow(2, 20), "1.0 MiB"),
        (pow(2, 30), "1.0 GiB"),
        (pow(2, 40), "1.0 TiB"),
        (pow(2, 50), "1.0 PiB"),
        (pow(2, 60), "1.0 EiB"),
        (pow(2, 70), "1.0 ZiB"),
        (pow(2, 80), "1.0 YiB"),
        (pow(2, 90), "1.0 HiB"),
        (pow(2, 100), "big"),
    ],
)
def test_human_bytes(input_bytes, expected):
    assert human_bytes(input_bytes) == expected


@pytest.mark.parametrize(
    "input_seconds,expected",
    [
        (0, "0.0 seconds"),
        (30, "30.0 seconds"),
        (60, "1.0 minutes"),
        (90, "1.5 minutes"),
        (125, "2.1 minutes"),
        (3600, "1.0 hours"),
        (86400, "1.0 days"),
        (604800, "1.0 weeks"),
        (31449600, "1.0 years"),
        (314496000, "1.0 decades"),
    ],
)
def test_human_seconds(input_seconds, expected):
    assert human_seconds(input_seconds) == expected


@pytest.mark.parametrize(
    "input_string,expected",
    [
        ("0:00", 0.0),
        ("0:01", 1.0),
        ("1:00", 60.0),
        ("2:30", 150.0),
        ("10:59", 659.0),
        ("99:59", 5999.0),
    ],
)
def test_raw_seconds_short_valid(input_string, expected):
    assert raw_seconds_short(input_string) == expected


@pytest.mark.parametrize(
    "invalid_input",
    [
        "",
        "notatime",
        "123",
        "1:60",
        "1:99",
        "-1:00",
        "1:-01",
        "1.5:00",
        "1:0",
        ":00",
        "1:",
        " 1:00",
        "1:00 ",
    ],
)
def test_raw_seconds_short_invalid(invalid_input):
    with pytest.raises(ValueError, match="String not in M:SS format"):
        raw_seconds_short(invalid_input)


@pytest.mark.parametrize(
    "input_seconds,expected",
    [
        (0, "0:00"),
        (1, "0:01"),
        (59, "0:59"),
        (60, "1:00"),
        (150, "2:30"),
        (3599, "59:59"),
        (3600, "60:00"),
    ],
)
def test_human_seconds_short_valid(input_seconds, expected):
    assert human_seconds_short(input_seconds) == expected


@pytest.mark.parametrize(
    "input_seconds",
    [
        -1,
        -3600,
    ],
)
def test_human_seconds_short_negative(input_seconds):
    result = human_seconds_short(input_seconds)
    assert isinstance(result, str)
    assert ":" in result


def test_human_bytes_negative():
    result = human_bytes(-1)
    assert isinstance(result, str)
    assert "B" in result or "iB" in result


def test_human_seconds_negative():
    result = human_seconds(-1)
    assert isinstance(result, str)
    assert "second" in result
