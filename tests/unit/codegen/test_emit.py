"""Text emission: literals, docstrings and fields come out the way ruff format leaves them."""

import ast
import unicodedata

import pytest

from ocsf_toolkit.codegen.emit import (
    _HIDDEN,  # pyright: ignore[reportPrivateUsage]
    WIDTH,
    Field,
    Imports,
    docstring,
    string,
    struct_class,
)


@pytest.mark.parametrize(
    ("text", "literal"),
    [("plain", '"plain"'), ('say "hi"', "'say \"hi\"'"), ("both \" '", '"both \\" \'"')],
)
def test_string_literal_quote_style(text: str, literal: str) -> None:
    assert string(text) == literal


def test_short_docstring_is_one_line() -> None:
    assert docstring("Process: an object.", "    ") == ['    """Process: an object."""']


def test_long_docstring_wraps_within_width() -> None:
    text = "word " * 40 + "\n\nDeprecated since 1.9.0: use <code>notes</code> instead."
    lines = docstring(text, "    ")
    assert lines[0].startswith('    """word')
    assert lines[-1] == '    """'
    assert "" in lines  # paragraphs stay apart
    assert all(len(line) <= WIDTH for line in lines)


def test_docstring_escapes() -> None:
    assert docstring('a \\ b """ c"', "") == ['"""a \\\\ b \\"\\"\\" c" """']


def _class(*fields: Field) -> list[str]:
    return struct_class("Thing", "Thing.", ("1.9.0", "thing"), [("group", list(fields))], Imports())


def test_fields_required_optional_aliased_and_always_encoded() -> None:
    lines = _class(
        Field("time", "time", "int", None),
        Field("message", "message", "str", "None"),
        Field("from_", "from", "str", "None"),
        Field("class_", "class", "str", None),
        Field("class_uid", "class_uid", "Literal[2004]", "2004", always=True),
    )
    assert lines[-5:] == [
        "    time: int",
        "    message: str | None = None",
        '    from_: str | None = field(default=None, name="from")',
        '    class_: str = field(name="class")',
        "    class_uid: Literal[2004] = field(default_factory=lambda: 2004)",
    ]


def test_long_fields_wrap_as_ruff_does() -> None:
    long = "_unmanned_system_operating_area.UnmannedSystemOperatingArea" + "X" * (WIDTH - 100)
    assert len(f"    unmanned_system_operating_area: {long} | None = None") > WIDTH
    lines = _class(
        Field("unmanned_system_operating_area", "unmanned_system_operating_area", long, "None"),
        Field("x" * 30, "x" * 30, f'Literal["{"y" * 40}"]', f'"{"y" * 40}"', always=True),
    )
    assert lines[-6:] == [
        "    unmanned_system_operating_area: (",
        f"        {long} | None",
        "    ) = None",
        f'    {"x" * 30}: Literal["{"y" * 40}"] = field(',
        f'        default_factory=lambda: "{"y" * 40}"',
        "    )",
    ]


@pytest.mark.parametrize("text", ['a"\nimport os', "b'\r\nx", 'c"\\', "\x00\x1c" + chr(0x2028), "\"'\n"])
def test_string_literals_hold_any_text(text: str) -> None:
    """Schema text is data: whatever it holds, the literal evaluates back to it."""
    literal = string(text)
    assert "\n" not in literal
    assert "\r" not in literal
    assert ast.literal_eval(literal) == text


def test_docstrings_hold_no_line_breaking_characters() -> None:
    [line] = docstring("caption\rimport os", "    ")
    assert line == '    """caption import os"""'


def test_docstring_hides_controls_and_invisible_spaces_by_a_fixed_table() -> None:
    """Replaced by code point, not str.isprintable(), which changes with each Python's Unicode version."""
    text = "a\rb\u00a0c\u200bd\u2028e\x85f\ufeffg"
    assert docstring(text, "") == ['"""a b c d e f g"""']
    assert docstring("x\n\ny", "") == ['"""x', "", "y", '"""']  # paragraphs keep their newlines
    bidi = "".join(chr(c) for c in (0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069, 0x061C))
    assert docstring(f"x{bidi}y", "") == [f'"""x{" " * len(bidi)}y"""']  # no Trojan Source in generated code
    assert all(c.isprintable() is False for c in bidi)  # what the old rule caught, the table catches too
    unassigned = "\U000e0080"  # unassigned in every Unicode version so far, so not printable today
    assert docstring(unassigned, "") == [f'"""{unassigned}"""']  # kept: no table decides it


def test_the_table_covers_every_control_format_and_separator_this_python_knows() -> None:
    """A newer Unicode may add format characters; this fails then, so the table is extended by hand."""
    hidden_categories = {"Cc", "Cf", "Zl", "Zp", "Zs"}
    known = (c for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF and c not in (0x0A, 0x20))
    missing = [hex(c) for c in known if unicodedata.category(chr(c)) in hidden_categories and c not in _HIDDEN]
    assert missing == []
