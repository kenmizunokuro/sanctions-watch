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
CANDIDATES = {(url, title): (symbol, marker) for url, symbol, marker, title in ARTICLES.values()}


class _ArticlePage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.headings = []
        self.heading_tag = ''
        self.heading_text = []
        self.hidden_tag = ''

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'template', 'noscript'} and not self.hidden_tag:
            self.hidden_tag = tag
        if tag in {'h1', 'h2', 'h3'} and not self.hidden_tag:
            self.heading_tag = tag
            self.heading_text = []

    def handle_endtag(self, tag):
        if tag == self.hidden_tag:
            self.hidden_tag = ''
        if tag == self.heading_tag and not self.hidden_tag:
            self.headings.append(' '.join(self.heading_text))
            self.heading_tag = ''

    def handle_data(self, data):
        if not self.hidden_tag:
            self.text.append(data)
            if self.heading_tag:
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
