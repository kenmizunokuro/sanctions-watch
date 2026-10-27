"""Read explicit historical notice alias fields, never infer identity or status."""
import re

from .integrity_notice_context import read_records
from .integrity_readers import ReadError

_LIKE = re.compile(r'(?<!\w)(?:[a-z]\.k\.a\.?|d\.b\.a\.?)', re.I)
_PREFIX = re.compile(r'\s*((?:a|f)\.k\.a\.)\s+', re.I)
_QUOTES = {'"': '"', '“': '”', '«': '»', '＂': '＂', '「': '」', '『': '』'}
_CLOSING_QUOTES = set(_QUOTES.values())
_RELATION = re.compile(r'\b(?:related|linked)\s+to\s*[:：]', re.I)


def _group(record, start, end):
    """Split only top-level unquoted semicolons; preserve exact source spans."""
    text = record['record']
    boundaries, depth, quoted = [start + 1], 0, None
    for pos in range(start + 1, end - 1):
        char = text[pos]
        if quoted:
            if char == quoted:
                quoted = None
        elif char in _QUOTES:
            quoted = _QUOTES[char]
        elif not quoted:
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            elif char == ';' and depth == 0:
                boundaries.append(pos + 1)
    boundaries.append(end)
    fields, markers = [], set()
    for left, right in zip(boundaries, boundaries[1:]):
        stop = right - 1  # separator semicolon or final closing parenthesis
        prefix = _PREFIX.match(text, left, stop)
        if not prefix:
            raise ReadError('unknown/ambiguous OFAC notice alias marker')
        value_start, value_end = prefix.end(), stop
        while value_start < value_end and text[value_start].isspace():
            value_start += 1
        while value_end > value_start and text[value_end - 1].isspace():
            value_end -= 1
        if value_start == value_end:
            raise ReadError('empty OFAC notice alias field')
        markers.add(prefix.start(1))
        fields.append(dict(record, alias_marker=prefix.group(1).lower(), name=text[value_start:value_end],
                           alias_start=value_start, alias_end=value_end, group_start=start, group_end=end))
    return fields, markers


def read_notice_aliases(body: bytes) -> list[dict]:
    """A marker inside an unrelated/nested relationship is not an alias field.

    Unknown or broken marked groups reject the body; do not return partial
    fields or pretend that an unparsed declaration contains no names.
    """
    output = []
    for record in read_records(body):
        text = record['record']
        if not _LIKE.search(text):
            continue
        stack, quoted, candidates, consumed = [], None, set(), set()
        fields = []
        relationship = False
        for pos, char in enumerate(text):
            if quoted:
                if char == quoted:
                    quoted = None
                continue
            if char in _QUOTES:
                quoted = _QUOTES[char]
                continue
            if char in _CLOSING_QUOTES:
                raise ReadError('unpaired OFAC notice alias closing quote')
            if _LIKE.match(text, pos):
                candidates.add(pos)
            if not stack and _RELATION.match(text, pos):
                relationship = True
            if char == '(':
                stack.append(pos)
            elif char == ')':
                if not stack:
                    raise ReadError('unbalanced OFAC notice alias parentheses')
                start = stack.pop()
                if not stack and _PREFIX.match(text, start + 1, pos):
                    if relationship:
                        raise ReadError('relationship-scoped OFAC notice alias declaration')
                    group_fields, markers = _group(record, start, pos + 1)
                    fields.extend(group_fields)
                    consumed.update(markers)
        if stack or quoted or candidates != consumed:
            raise ReadError('unclosed or unscoped OFAC notice alias declaration')
        output.extend(fields)
    return output
