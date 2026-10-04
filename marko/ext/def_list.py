# mypy: disable-error-code="no-redef"
"""
Definition lists extension
~~~~~~~~~~~~~~~~~~~~~~~~~~

Enable PHP-Markdown style definition lists in Marko.

Usage::

    from marko import Markdown

    text = "Term 1\\n:   Definition 1\\n:   Definition 2\\n"
    markdown = Markdown(extensions=["def_list"])
    print(markdown(text))

"""

from __future__ import annotations

import re

from marko import HTMLRenderer, block, helpers
from marko.element import _SourceMap
from marko.md_renderer import MarkdownRenderer

__all__ = ("DefList", "DefListTerm", "DefListItem")


class DefList(block.BlockElement):
    """Definition list block element."""

    priority = 5

    _TERM = r" {,3}(?![:\s])[^\n]*\n"
    _MARKER = r" {,3}:[ \t\n]"
    _SETEXT = r" {,3}(?:=+|-+)[^\n\S]*\n"
    _term_re = re.compile(_TERM)
    _marker_re = re.compile(_MARKER)

    def __init__(self) -> None:
        self.children: list[block.BlockElement] = []

    @classmethod
    def _is_def_marker(cls, line: str) -> bool:
        return cls._marker_re.match(line.expandtabs(4)) is not None

    @classmethod
    def match(cls, source) -> bool:
        m = source.expect_re(cls._term_re)
        if not m or re.match(cls._SETEXT, m.group()):
            return False
        prefix = source.prefix
        term_re = re.compile(prefix + cls._TERM)
        setext_re = re.compile(prefix + cls._SETEXT)
        marker_re = re.compile(prefix + cls._MARKER)
        pos = m.end()
        buffer = source._buffer
        while True:
            term = term_re.match(buffer, pos)
            if term is None or setext_re.match(buffer, pos):
                break
            pos = term.end()
        return marker_re.match(buffer, pos) is not None

    @classmethod
    def parse(cls, source) -> DefList:
        state = cls()
        children: list[block.BlockElement] = []
        with source.under_state(state):
            while True:
                # Parse consecutive term lines.
                while True:
                    line = source.next_line()
                    if line is None or not line.strip() or cls._is_def_marker(line):
                        break
                    start = source.match.start() if source.match else source.pos
                    stripped = line.strip()
                    term = DefListTerm(stripped)
                    leading = len(line) - len(line.lstrip())
                    term._inline_positions = _SourceMap(
                        [(start + leading, len(stripped))]
                    )
                    children.append(term)
                    source.consume()
                    term.source_span = (start, source.pos)
                # Parse the definitions belonging to the terms.
                while True:
                    line = source.next_line()
                    if line is None or not cls._is_def_marker(line):
                        break
                    item_start = source._current_pos
                    item = DefListItem.parse(source)
                    item.source_span = (item_start, source.pos)
                    children.append(item)
                if source.exhausted or not block.BlankLine.match(source):
                    break
                # PHP Markdown allows blank lines between term/definition
                # groups within the same definition list.
                pos = source.pos
                block.BlankLine.parse(source)
                if not cls.match(source):
                    source.pos = pos
                    break
        for child in children:
            if (
                isinstance(child, DefListItem)
                and len(child.children) == 1
                and isinstance(child.children[0], block.Paragraph)
            ):
                child.children[0]._tight = True
        state.children = children
        return state


class DefListTerm(block.BlockElement):
    """Definition list term. It can only be created by DefList.parse."""

    virtual = True

    def __init__(self, text: str) -> None:
        self.inline_body = text


class DefListItem(block.BlockElement):
    """Definition list item. It can only be created by DefList.parse."""

    virtual = True
    #: The start of a new definition ends the paragraph of the previous one.
    breaks_paragraph = True

    def __init__(self) -> None:
        self.children: list[block.BlockElement] = []
        self._prefix = r" {,3}:(?:[ \t]{1,3}|[ \t]*(?=\n))"
        self._second_prefix = " " * 4

    @classmethod
    def match(cls, source) -> bool:
        if not any(isinstance(state, cls) for state in source._states):
            return False
        line = source.next_line(False)
        if line is None:
            return False
        expanded = line.expandtabs(4)
        # A marker line may start a sibling definition at any nesting level.
        for index, state in enumerate(source._states):
            if isinstance(state, cls):
                parent_prefix = "".join(s._prefix for s in source._states[:index])
                if re.match(parent_prefix + DefList._MARKER, expanded):
                    return True
        return False

    @classmethod
    def parse(cls, source) -> DefListItem:
        state = cls()
        with source.under_state(state):
            state.children = source.parser.parse_source(source)
        if state.children and isinstance(state.children[-1], block.BlankLine):
            # Remove the trailing blank lines from the item.
            blankline = state.children.pop()
            if state.children:
                source.pos = blankline._anchor
        return state


class DefListRendererMixin:
    @helpers.render_dispatch(HTMLRenderer)
    def render_def_list(self, element):
        return f"<dl>\n{self.render_children(element)}</dl>\n"

    @render_def_list.dispatch(MarkdownRenderer)
    def render_def_list(self, element):
        result = []
        prev = None
        for child in element.children:
            # Separate term/definition groups with a blank line.
            if isinstance(child, DefListTerm) and isinstance(prev, DefListItem):
                result.append("\n")
            result.append(self.render(child))
            prev = child
        return "".join(result)

    @helpers.render_dispatch(HTMLRenderer)
    def render_def_list_term(self, element):
        return f"<dt>{self.render_children(element)}</dt>\n"

    @render_def_list_term.dispatch(MarkdownRenderer)
    def render_def_list_term(self, element):
        result = self._prefix + self.render_children(element) + "\n"
        self._prefix = self._second_prefix
        return result

    @helpers.render_dispatch(HTMLRenderer)
    def render_def_list_item(self, element):
        if len(element.children) == 1 and getattr(
            element.children[0], "_tight", False
        ):
            sep = ""
        else:
            sep = "\n"
        return f"<dd>{sep}{self.render_children(element)}</dd>\n"

    @render_def_list_item.dispatch(MarkdownRenderer)
    def render_def_list_item(self, element):
        with self.container(":   ", "    "):
            result = self.render_children(element)
        self._prefix = self._second_prefix
        return result


def make_extension():
    return helpers.MarkoExtension(
        elements=[DefList, DefListTerm, DefListItem],
        renderer_mixins=[DefListRendererMixin],
    )
