"""Identity checks for the two UN press originals selected for evidence collection."""
from __future__ import annotations

import re
from html.parser import HTMLParser

from . import un_sources as U

MAX_BYTES = 2 * 1024 * 1024
ARTICLES = {
    'press_sc16506': (
        'https://press.un.org/en/2026/sc16506.doc.htm', 'SC/16506', '1533 Sanctions Committee',
        'Security Council 1533 Sanctions Committee Amends One Entity on Its Sanctions List',
    ),
    'press_sc16508': (
        'https://press.un.org/en/2026/sc16508.doc.htm', 'SC/16508', '2837 (2026)',
        'Security Council Updates Yemen Sanctions Regime to Address Evolving Houthi Threats through '
        'Resolution 2837 (2026)',
    ),
}
CANDIDATES = {url: (symbol, marker, title) for url, symbol, marker, title in ARTICLES.values()}


class _ArticlePage(HTMLParser):
    _HIDDEN = {'script', 'style', 'template', 'noscript'}
    _HEADINGS = {'h1', 'h2', 'h3'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.headings = []
        self.heading_stack = []
        self.heading_text = []
        self.hidden_stack = []

    def handle_starttag(self, tag, attrs):
        if tag in self._HIDDEN:
            self.hidden_stack.append(tag)
            return
        if tag in self._HEADINGS and not self.hidden_stack:
            if not self.heading_stack:
                self.heading_text = []
            self.heading_stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self._HIDDEN and tag in self.hidden_stack:
            for idx in range(len(self.hidden_stack) - 1, -1, -1):
                if self.hidden_stack[idx] == tag:
                    del self.hidden_stack[idx:]
                    break
            return
        if tag in self._HEADINGS and tag in self.heading_stack and not self.hidden_stack:
            for idx in range(len(self.heading_stack) - 1, -1, -1):
                if self.heading_stack[idx] == tag:
                    del self.heading_stack[idx:]
                    break
            if not self.heading_stack:
                self.headings.append(' '.join(self.heading_text))
                self.heading_text = []

    def handle_data(self, data):
        if not self.hidden_stack:
            self.text.append(data)
            if self.heading_stack:
                self.heading_text.append(data)


def markers_found(body, symbol, marker):
    if len(body) > MAX_BYTES:
        raise U.UNSchemaError('UN press article exceeds size limit')
    page = _ArticlePage()
    page.feed(body.decode('utf-8', errors='replace'))
    page.close()
    text = ' '.join(page.text)
    return (re.search(r'(?<![\w/])' + re.escape(symbol) + r'(?![\w/])', text, re.IGNORECASE) is not None
            and any(marker.casefold() in ' '.join(heading.split()).casefold() for heading in page.headings))
