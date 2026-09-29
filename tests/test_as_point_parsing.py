"""Regression tests for _as_point string coordinate parsing.

Claude Desktop strips anyOf schemas, so the model sometimes serializes a
`loc` coordinate as a string list like ``["100", "200"]``. The guard used
``stripped.lstrip("+-").isdigit()``, which strips *any* run of leading
signs — so ``"+-5"`` / ``"--5"`` passed validation and then int() raised a
confusing ``invalid literal for int()`` error instead of the intended
``must contain exactly 2 integers`` message. ``.isdigit()`` also accepts
digits int() cannot parse (e.g. the superscript ``"²"``).
"""

import pytest

from windows_mcp.tools.input import _as_point


def test_accepts_plain_and_signed_integer_strings():
    assert _as_point(["100", "200"], "loc") == [100, 200]
    assert _as_point(["+5", "-5"], "loc") == [5, -5]
    assert _as_point([" 100 ", "200"], "loc") == [100, 200]


def test_accepts_real_integers_including_negative():
    assert _as_point([-5, 10], "loc") == [-5, 10]


@pytest.mark.parametrize("bad", ["+-5", "-+5", "--5", "++5"])
def test_multi_sign_strings_raise_the_intended_error(bad):
    # Previously these passed the guard and int() raised a low-level
    # "invalid literal for int()" instead of the intended message.
    with pytest.raises(ValueError, match="exactly 2 integers"):
        _as_point([bad, "0"], "loc")


def test_non_ascii_digit_string_raises_the_intended_error():
    # "²" (superscript two) is str.isdigit() but not int()-parseable.
    with pytest.raises(ValueError, match="exactly 2 integers"):
        _as_point(["²", "0"], "loc")


@pytest.mark.parametrize("bad", ["+", "-", "+-", "5-", "", " ", "1.5", "abc"])
def test_other_non_integer_strings_raise(bad):
    with pytest.raises(ValueError, match="exactly 2 integers"):
        _as_point([bad, "0"], "loc")


def test_booleans_are_rejected_before_int_coercion():
    with pytest.raises(ValueError, match="not booleans"):
        _as_point([True, 0], "loc")


@pytest.mark.parametrize("bad", [["1"], ["1", "2", "3"], "100,200", 100, None])
def test_wrong_shape_raises(bad):
    with pytest.raises(ValueError):
        _as_point(bad, "loc")
