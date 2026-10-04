# mypy: disable-error-code="no-redef"
"""
Definition lists extension
~~~~~~~~~~~~~~~~~~~~~~~~~~

Enable PHP-Markdown style definition lists in Marko. A term line followed by
one or more lines starting with ``:`` forms a definition list. A term may
carry several definitions, and the definitions may contain inline markup and
nested blocks (lists, paragraphs, ...)::

    Term 1
    :   Definition 1
    :   Definition 2

    Term 2
    :   Definition 2.1

Usage::

    from marko import Markdown

    markdown = Markdown(extensions=['deflist'])
    print(markdown(text))

"""

from __future__ import annotations

import re
from re import Match
from typing import TYPE_CHECKING, cast

from marko import HTMLRenderer, block, helpers
from marko.ext.latex_renderer import LatexRendererMixin
from marko.md_renderer import MarkdownRenderer

if TYPE_CHECKING:
    from marko.source import Source

#: A line that starts a definition: up to 3 spaces, a colon and whitespace.
_DEF_LINE = re.compile(r" {,3}:(?=[^\n\S]|$)")

#: Lines that cannot be term lines because they start another block element.
_NOT_TERM = re.compile(
    r"(?:"
    r" {,3}#{1,6}(?=[^\n\S]|$)"  # ATX heading
    r"| {,3}>"  # block quote
    r"| {,3}(?:`{3,}|~{3,})"  # fenced code
    r"| {,3}(?:\d{1,9}[.)]|[-+*])[^\n\S]"  # list marker
    r"| {,3}(?:[-_*][^\n\S]*){3,}$"  # thematic break
    r"| {,3}[=-]+[^\n\S]*$"  # setext heading underline
    r"| {,3}<[A-Za-z!/?.-]"  # HTML block
    r"| {4}\S"  # indented code block
    r")"
)


def _scan_ahead(source: Source) -> bool:
    """Check that a run of term lines starting at the current position is
    followed by a definition marker line, before a blank line or the start of
    another block element."""
    line = source.next_line()
    if line is None or not line.strip():
        return False
    if _DEF_LINE.match(line) or _NOT_TERM.match(line.expandtabs(4)):
        return False
    pos = source.match.end()
    prefix = source.prefix
    while True:
        m = source._expect_re(r"(?m)[^\n]*$\n?", pos)
        if m is None:
            return False
        raw = m.group()
        prefix_len = source.match_prefix(prefix, raw)
        if prefix_len < 0:
            return False
        body = raw[prefix_len:]
        if _DEF_LINE.match(body):
            return True
        if not body.strip() or _NOT_TERM.match(body.expandtabs(4)):
            return False
        pos = m.end()


class DefinitionList(block.BlockElement):
    """A definition list block element (``<dl>``)"""

    priority = 6
    breaks_paragraph = True

    def __init__(self) -> None:
        self.children: list[DefinitionItem] = []
        self.tight = True

    @classmethod
    def match(cls, source: Source) -> bool:
        state = source.state
        if isinstance(state, (block.Document, block.Quote)):
            line = source.next_line()
            if line is None or not line.strip():
                return False
            # A line like ``[label]: dest`` must remain a reference definition.
            if line.lstrip()[:1] == "[":
                for name in ("LinkRefDef", "FootnoteDef"):
                    element = source.parser.block_elements.get(name)
                    if element is not None and element.match(source):
                        return False
        elif not isinstance(state, DefinitionList):
            return False
        return _scan_ahead(source)

    @classmethod
    def parse(cls, source: Source) -> DefinitionList:
        state = cls()
        children: list[DefinitionItem] = []
        with source.under_state(state):
            while not source.exhausted:
                if DefinitionItem.match(source):
                    children.append(DefinitionItem.parse(source))
                    source.anchor()
                elif block.BlankLine.match(source):
                    block.BlankLine.parse(source)
                else:
                    source.reset()
                    break
        state.children = children
        state.tight = not any(item._loose for item in children)
        if state.tight:
            for item in children:
                for child in item.children:
                    if isinstance(child, Definition):
                        for grandchild in child.children:
                            if isinstance(grandchild, block.Paragraph):
                                grandchild._tight = True
        return state


class DefinitionItem(block.BlockElement):
    """A group of terms sharing one or more definitions.

    It can only be created by ``DefinitionList.parse``.
    """

    virtual = True
    _loose = False

    def __init__(self) -> None:
        self.children: list[DefinitionTerm | Definition] = []

    @classmethod
    def match(cls, source: Source) -> bool:
        return _scan_ahead(source)

    @classmethod
    def parse(cls, source: Source) -> DefinitionItem:
        state = cls()
        children: list[DefinitionTerm | Definition] = []
        # Consume the term lines.
        while not source.exhausted:
            line = source.next_line()
            if line is None or not line.strip() or _DEF_LINE.match(line):
                break
            if children and _NOT_TERM.match(line.expandtabs(4)):
                break
            children.append(DefinitionTerm(line.strip()))
            source.consume()
        # Consume the definitions.
        has_blank_line = False
        while not source.exhausted:
            if Definition.match(source):
                if has_blank_line:
                    state._loose = True
                    has_blank_line = False
                children.append(Definition.parse(source))
                source.anchor()
            elif block.BlankLine.match(source):
                block.BlankLine.parse(source)
                has_blank_line = True
            else:
                source.reset()
                break
        state.children = children
        return state


class DefinitionTerm(block.BlockElement):
    """A definition term (``<dt>``).

    It can only be created by ``DefinitionItem.parse``.
    """

    virtual = True

    def __init__(self, text: str) -> None:
        self.inline_body = text


class Definition(block.BlockElement):
    """A single definition (``<dd>``).

    It can only be created by ``DefinitionItem.parse``.
    """

    virtual = True
    breaks_paragraph = True
    pattern = re.compile(r" {,3}:[^\n\S]*(?=\S| {4}|\n|$)", re.MULTILINE)

    def __init__(self, marker: str) -> None:
        self.children: list[block.BlockElement] = []
        self._prefix = re.escape(marker)
        self._second_prefix = " " * len(marker.expandtabs(4))

    @classmethod
    def match(cls, source: Source) -> Match[str] | None:
        if not isinstance(source.state, DefinitionList):
            return None
        return source.expect_re(cls.pattern)

    @classmethod
    def parse(cls, source: Source) -> Definition:
        state = cls(source.match.group())
        with source.under_state(state):
            state.children = source.parser.parse_source(source)
        if state.children and isinstance(state.children[-1], block.BlankLine):
            # Leave the trailing blank lines to the surrounding list.
            blankline = cast(block.BlankLine, state.children.pop())
            if state.children:
                source.pos = blankline._anchor
        return state


class DefListRendererMixin:
    @helpers.render_dispatch(HTMLRenderer)
    def render_definition_list(self, element):
        return f"<dl>\n{self.render_children(element)}</dl>\n"

    @render_definition_list.dispatch(MarkdownRenderer)
    def render_definition_list(self, element):
        return "\n".join(self.render(child) for child in element.children)

    @render_definition_list.dispatch(LatexRendererMixin)
    def render_definition_list(self, element):
        return self._environment("description", self.render_children(element))

    @helpers.render_dispatch((HTMLRenderer, LatexRendererMixin))
    def render_definition_item(self, element):
        return self.render_children(element)

    @render_definition_item.dispatch(MarkdownRenderer)
    def render_definition_item(self, element):
        parts = []
        has_definition = False
        for child in element.children:
            if isinstance(child, Definition):
                if element._loose and has_definition:
                    parts.append("\n")
                has_definition = True
            parts.append(self.render(child))
        return "".join(parts)

    @helpers.render_dispatch(HTMLRenderer)
    def render_definition_term(self, element):
        return f"<dt>{self.render_children(element)}</dt>\n"

    @render_definition_term.dispatch(MarkdownRenderer)
    def render_definition_term(self, element):
        result = self._prefix + self.render_children(element) + "\n"
        self._prefix = self._second_prefix
        return result

    @render_definition_term.dispatch(LatexRendererMixin)
    def render_definition_term(self, element):
        return f"\\item[{self.render_children(element)}] "

    @helpers.render_dispatch(HTMLRenderer)
    def render_definition(self, element):
        return f"<dd>{self.render_children(element)}</dd>\n"

    @render_definition.dispatch(MarkdownRenderer)
    def render_definition(self, element):
        with self.container(":   ", "    "):
            result = self.render_children(element)
        self._prefix = self._second_prefix
        return result

    @render_definition.dispatch(LatexRendererMixin)
    def render_definition(self, element):
        return self.render_children(element) + "\n"


def make_extension():
    return helpers.MarkoExtension(
        elements=[DefinitionList, DefinitionItem, DefinitionTerm, Definition],
        renderer_mixins=[DefListRendererMixin],
    )
