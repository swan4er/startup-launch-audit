"""HTML-отчёт скилла startup-launch-audit: report.md и calc.json → report.html с графиками."""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sys
import unicodedata

from calc import (CONFIDENCE_LABELS, CRITERIA, GATE_LABELS, LTV_CAC_BANDS, MINUS, NBSP, SCENARIOS, SEGMENT_PICK_ABOVE,
                  SENSITIVITY_LABELS, SENSITIVITY_STEP, VERDICT_CHECK_FROM, VERDICT_GO_ABOVE, VERDICT_LABELS,
                  fmt_money, fmt_num, fmt_pct)

CSS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "assets", "report.css")

THEME_JS = """\
(function () {
  var root = document.documentElement;
  var key = "report-theme";
  var dark = window.matchMedia("(prefers-color-scheme: dark)");
  try {
    var saved = localStorage.getItem(key);
    if (saved === "dark" || saved === "light") root.setAttribute("data-theme", saved);
  } catch (e) {}
  function current() { return root.getAttribute("data-theme") || (dark.matches ? "dark" : "light"); }
  document.addEventListener("DOMContentLoaded", function () {
    var toc = document.getElementById("toc");
    if (toc && window.matchMedia("(max-width: 640px)").matches) toc.removeAttribute("open");
    document.getElementById("theme-toggle").addEventListener("click", function () {
      var next = current() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem(key, next); } catch (e) {}
    });
  });
})();"""

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([^`\s]*)[^`]*$")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
HR_RE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
QUOTE_RE = re.compile(r"^ {0,3}>")
ITEM_RE = re.compile(r"^( *)([-*+]|\d{1,9}[.)])[ \t]+(\S.*)$")
COMMENT_RE = re.compile(r"^\s*<!--")
MARKER_LINE_RE = re.compile(r"^\s*<!--\s*chart:([a-z0-9_-]+)\s*-->\s*$")
MARKER_RE = re.compile(r"<!-- chart:([a-z0-9_-]+) -->")
DELIMITER_CELL_RE = re.compile(r"^\s*(:?)-+(:?)\s*$")
TASK_RE = re.compile(r"^\[([ xX])\][ \t]+(.*)$", re.S)
URL_RE = re.compile("[^\\s<>*`\\[\\]\ue000\ue001]+")
HOLD_RE = re.compile("\ue000(\\d+)\ue001")
ESCAPABLE = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
LIST_DEPTH = 2


class _State:
    def __init__(self):
        self.headings = 0


def _escape(text):
    return html.escape(str(text), quote=True)


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _code_span(text, i):
    n = len(text)
    j = i
    while j < n and text[j] == "`":
        j += 1
    size = j - i
    k = j
    while k < n:
        if text[k] != "`":
            k += 1
            continue
        m = k
        while m < n and text[m] == "`":
            m += 1
        if m - k == size:
            body = text[j:k].replace("\n", " ")
            if len(body) > 2 and body[0] == body[-1] == " " and body.strip():
                body = body[1:-1]
            return f"<code>{_escape(body)}</code>", m
        k = m
    return None


def _bracket_end(text, start):
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _destination(text, k):
    n = len(text)
    while k < n and text[k] in " \t\n":
        k += 1
    if k < n and text[k] == "<":
        end = text.find(">", k)
        if end < 0 or "\n" in text[k:end]:
            return None
        url, k = text[k + 1:end], end + 1
    else:
        depth, start = 0, k
        while k < n and text[k] not in " \t\n":
            if text[k] == "\\":
                k += 2
                continue
            if text[k] == "(":
                depth += 1
            elif text[k] == ")":
                if depth == 0:
                    break
                depth -= 1
            k += 1
        url = text[start:k]
    while k < n and text[k] in " \t\n":
        k += 1
    if k < n and text[k] in "\"'(":
        closer = ")" if text[k] == "(" else text[k]
        end = text.find(closer, k + 1)
        if end < 0:
            return None
        k = end + 1
        while k < n and text[k] in " \t\n":
            k += 1
    if k >= n or text[k] != ")":
        return None
    return re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", url), k + 1


def _safe_url(url):
    cleaned = re.sub(r"[\x00-\x20\x7f]+", "", url)
    low = cleaned.lower()
    if low.startswith(("http://", "https://")) and len(cleaned) > 8:
        return cleaned, True
    if (low.startswith("mailto:") and len(cleaned) > 7) or (cleaned.startswith("#") and len(cleaned) > 1):
        return cleaned, False
    return None


def _anchor(url, label_html):
    safe = _safe_url(url)
    if safe is None:
        return label_html
    href, external = safe
    extra = ' target="_blank" rel="noopener noreferrer"' if external else ""
    return f'<a href="{_escape(href)}"{extra}>{label_html}</a>'


def _link(text, i, links):
    image = text[i] == "!"
    start = i + 1 if image else i
    close = _bracket_end(text, start)
    if close is None or close + 1 >= len(text) or text[close + 1] != "(":
        return None
    parsed = _destination(text, close + 2)
    if parsed is None:
        return None
    url, end = parsed
    label = _inline(text[start + 1:close], links=False)
    if image or not links:
        return label, end
    return _anchor(url, label), end


def _bare_url(text, i):
    if text[i:i + 8].lower().startswith(("http://", "https://")) and (i == 0 or not text[i - 1].isalnum()):
        url = URL_RE.match(text, i).group(0)
        while url and (url[-1] in ".,;:!?'\"" or (url[-1] == ")" and url.count("(") < url.count(")"))):
            url = url[:-1]
        if len(url) > 8:
            return _anchor(url, _escape(url)), i + len(url)
    return None


def _flanking(prev, nxt, size):
    prev_space, next_space = not prev or prev.isspace(), not nxt or nxt.isspace()
    prev_punct = not prev_space and unicodedata.category(prev)[0] in "PS"
    next_punct = not next_space and unicodedata.category(nxt)[0] in "PS"
    opens = not next_space and (not next_punct or prev_space or prev_punct)
    closes = not prev_space and (not prev_punct or next_space or next_punct)
    if size == 1:
        opens, closes = opens and not prev.isalnum(), closes and not nxt.isalnum()
    return opens, closes


def _emphasis(text):
    parts = re.split(r"(\*+)", text)
    nodes = []
    for k, part in enumerate(parts):
        if k % 2 == 0:
            if part:
                nodes.append(part)
        else:
            opens, closes = _flanking(parts[k - 1][-1:], parts[k + 1][:1] if k + 1 < len(parts) else "", len(part))
            nodes.append([len(part), opens, closes, len(part)])

    def flat(items):
        return "".join("*" * n[0] if isinstance(n, list) else n for n in items)

    stack, i = [], 0
    while i < len(nodes):
        node = nodes[i]
        if not isinstance(node, list) or node[0] == 0:
            i += 1
            continue
        match = None
        if node[2]:
            for j in range(len(stack) - 1, -1, -1):
                opener = nodes[stack[j]]
                if (opener[2] or node[1]) and (opener[3] + node[3]) % 3 == 0 and not (opener[3] % 3 == 0 and node[3] % 3 == 0):
                    continue
                match = j
                break
        if match is None:
            if node[1]:
                stack.append(i)
            i += 1
            continue
        at = stack[match]
        opener = nodes[at]
        use = 2 if opener[0] >= 2 and node[0] >= 2 else 1
        tag = "strong" if use == 2 else "em"
        opener[0] -= use
        node[0] -= use
        nodes[at + 1:i] = [f"<{tag}>{flat(nodes[at + 1:i])}</{tag}>"]
        i = at + 2
        del stack[match + 1:]
        if opener[0] == 0:
            del stack[match]
    return flat(nodes)


def _inline(text, links=True):
    text = re.sub("[\ue000\ue001]", "", text)
    held, out = [], []

    def hold(markup):
        held.append(markup)
        return f"\ue000{len(held) - 1}\ue001"

    i, n = 0, len(text)
    while i < n:
        c = text[i]
        found = None
        if c == "\\" and i + 1 < n and text[i + 1] in ESCAPABLE:
            found = (_escape(text[i + 1]), i + 2)
        elif c == "`":
            found = _code_span(text, i)
            if found is None:
                run = len(text[i:]) - len(text[i:].lstrip("`"))
                out.append("`" * run)
                i += run
                continue
        elif c == "[" or text.startswith("![", i):
            found = _link(text, i, links)
        elif links and c in "hH":
            found = _bare_url(text, i)
        if found:
            out.append(hold(found[0]))
            i = found[1]
        else:
            out.append(c)
            i += 1
    result = _emphasis(_escape("".join(out)))
    result = re.sub(r"( {2,}|\\)\n", "<br>\n", result)
    result = re.sub(r"[ \t]+\n", "\n", result)
    return HOLD_RE.sub(lambda m: held[int(m.group(1))], result)


def _split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    cells, cur, i, after_separator = [], [], 0, False
    while i < len(s):
        c = s[i]
        after_separator = False
        if c == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if c == "|":
            cells.append("".join(cur).strip())
            cur, after_separator = [], True
        else:
            cur.append(c)
        i += 1
    if cur or not after_separator:
        cells.append("".join(cur).strip())
    return cells


def _table_start(lines, i):
    if i + 1 >= len(lines) or "|" not in lines[i]:
        return False
    delimiter = _split_row(lines[i + 1])
    if not all(DELIMITER_CELL_RE.match(cell) for cell in delimiter):
        return False
    return len(delimiter) == len(_split_row(lines[i]))


def _table(lines, i, out):
    header = _split_row(lines[i])
    aligns = []
    for cell in _split_row(lines[i + 1]):
        left, right = cell.strip().startswith(":"), cell.strip().endswith(":")
        aligns.append("center" if left and right else "left" if left else "right" if right else "")
    i += 2
    body = []
    while i < len(lines) and lines[i].strip() and "|" in lines[i]:
        body.append(_split_row(lines[i]))
        i += 1

    def cell(tag, text, index):
        style = f' style="text-align:{aligns[index]}"' if aligns[index] else ""
        scope = ' scope="col"' if tag == "th" else ""
        return f"<{tag}{scope}{style}>{_inline(text)}</{tag}>"

    width = len(header)
    rows = [f"<tr>{''.join(cell('td', (row + [''] * width)[k], k) for k in range(width))}</tr>" for row in body]
    out.append(
        '<div class="table-wrap"><table>\n'
        f"<thead><tr>{''.join(cell('th', header[k], k) for k in range(width))}</tr></thead>\n"
        f"<tbody>\n{chr(10).join(rows)}\n</tbody>\n</table></div>")
    return i


def _starts_block(lines, i):
    line = lines[i]
    if FENCE_RE.match(line) or HEADING_RE.match(line) or HR_RE.match(line) or QUOTE_RE.match(line):
        return True
    if COMMENT_RE.match(line):
        return True
    item = ITEM_RE.match(line)
    if item and (not item.group(2)[0].isdigit() or int(item.group(2)[:-1]) == 1):
        return True
    return _table_start(lines, i)


def _fence(lines, i, match, out):
    mark, info = match.group(1), match.group(2)
    closing = re.compile("^ {0,3}" + re.escape(mark[0]) + "{" + str(len(mark)) + ",}[ \t]*$")
    indent = _indent(lines[i])
    body = []
    i += 1
    while i < len(lines):
        if closing.match(lines[i]):
            i += 1
            break
        line = lines[i]
        body.append(line[indent:] if not line[:indent].strip() else line.lstrip())
        i += 1
    lang = f' class="language-{_escape(info)}"' if re.fullmatch(r"[\w+#.-]{1,30}", info) else ""
    out.append(f"<pre><code{lang}>{_escape(chr(10).join(body))}</code></pre>")
    return i


def _skip_comment(lines, i):
    while i < len(lines):
        done = "-->" in lines[i][lines[i].index("<!--") + 4:] if "<!--" in lines[i] else "-->" in lines[i]
        i += 1
        if done:
            break
    return i


def _quote(lines, i, state, out):
    inner = []
    while i < len(lines) and QUOTE_RE.match(lines[i]):
        inner.append(re.sub(r"^ {0,3}> ?", "", lines[i], count=1))
        i += 1
    out.append('<blockquote class="callout">\n' + "\n".join(_blocks(inner, state)) + "\n</blockquote>")
    return i


def _dedent(lines):
    margin = min((_indent(line) for line in lines if line.strip()), default=0)
    return [line[margin:] if line.strip() else "" for line in lines]


def _item_markup(item, state):
    text, rest = item["text"], _dedent(item["rest"])
    k = 0
    while k < len(rest) and rest[k].strip() and not _starts_block(rest, k):
        text += "\n" + rest[k].strip()
        k += 1
    nested = "\n".join(_blocks(rest[k:], state))
    task = TASK_RE.match(text)
    if task:
        done = task.group(1) != " "
        box = ('<span class="task-box" role="img" aria-label="'
               + ("выполнено" if done else "не выполнено") + '">' + ("☑" if done else "☐") + "</span>")
        attrs, content = ' class="task"', f"{box} {_inline(task.group(2))}"
    else:
        attrs, content = "", _inline(text)
    return attrs, content + ("\n" + nested if nested else "")


def _render_items(items, state):
    stops, levels = [], []
    for item in items:
        while len(stops) > 1 and item["indent"] < stops[-1]:
            stops.pop()
        if not stops or item["indent"] >= stops[-1] + 2:
            stops.append(item["indent"])
        levels.append(min(len(stops) - 1, LIST_DEPTH - 1))
    out, open_lists = [], []
    for item, level in zip(items, levels):
        tag = "ol" if item["ordered"] else "ul"
        while open_lists and open_lists[-1][0] > level:
            out.append(f"</li></{open_lists.pop()[1]}>")
        if open_lists and open_lists[-1][0] == level:
            if open_lists[-1][1] != tag:
                out.append(f"</li></{open_lists[-1][1]}>")
                open_lists.pop()
            else:
                out.append("</li>")
        if not open_lists or open_lists[-1][0] < level:
            start = f' start="{item["number"]}"' if tag == "ol" and item["number"] != 1 else ""
            out.append(f"<{tag}{start}>")
            open_lists.append((level, tag))
        attrs, content = _item_markup(item, state)
        out.append(f"<li{attrs}>{content}")
    while open_lists:
        out.append(f"</li></{open_lists.pop()[1]}>")
    return re.sub(r"(<[uo]l(?: [^>]*)?>|</li>|</[uo]l>)", r"\1\n", "".join(out)).rstrip("\n")


def _list(lines, i, state, out):
    items, blank = [], False
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            blank = True
            i += 1
            continue
        match = ITEM_RE.match(line)
        if match and not HR_RE.match(line):
            marker = match.group(2)
            items.append({"indent": len(match.group(1)), "ordered": marker[0].isdigit(),
                          "number": int(marker[:-1]) if marker[0].isdigit() else 1, "text": match.group(3), "rest": []})
        elif items and (_indent(line) >= 2 or (not blank and not _starts_block(lines, i))):
            if blank:
                items[-1]["rest"].append("")
            items[-1]["rest"].append(line)
            fence = FENCE_RE.match(line.lstrip())
            if fence:
                closing = re.compile("^ *" + re.escape(fence.group(1)[0]) + "{" + str(len(fence.group(1))) + ",} *$")
                while i + 1 < len(lines):
                    i += 1
                    items[-1]["rest"].append(lines[i])
                    if closing.match(lines[i]):
                        break
        else:
            break
        blank = False
        i += 1
    out.append(_render_items(items, state))
    return i


def _paragraph(lines, i, out):
    j = i + 1
    while j < len(lines) and lines[j].strip() and not _starts_block(lines, j):
        j += 1
    out.append("<p>" + _inline("\n".join(line.lstrip() for line in lines[i:j]).rstrip()) + "</p>")
    return j


def _blocks(lines, state):
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        fence = FENCE_RE.match(line)
        if fence:
            i = _fence(lines, i, fence, out)
            continue
        marker = MARKER_LINE_RE.match(line)
        if marker:
            out.append(f"<!-- chart:{marker.group(1)} -->")
            i += 1
            continue
        if COMMENT_RE.match(line):
            i = _skip_comment(lines, i)
            continue
        heading = HEADING_RE.match(line)
        if heading:
            state.headings += 1
            level = min(len(heading.group(1)), 4)
            out.append(f'<h{level} id="s-{state.headings}">{_inline(heading.group(2).strip())}</h{level}>')
            i += 1
            continue
        if HR_RE.match(line):
            out.append("<hr>")
            i += 1
            continue
        if QUOTE_RE.match(line):
            i = _quote(lines, i, state, out)
            continue
        if ITEM_RE.match(line):
            i = _list(lines, i, state, out)
            continue
        if _table_start(lines, i):
            i = _table(lines, i, out)
            continue
        i = _paragraph(lines, i, out)
    return out


def markdown_to_html(md: str) -> str:
    """Подмножество Markdown → HTML: весь текст экранируется, сырой HTML не пропускается."""
    text = md.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    return "\n".join(_blocks(text.split("\n"), _State()))


VB_W = 420
PAD = 14
BAR_H = 10
SMALL = 11
BASE_FONT = 12.5
RADIUS_MAX = 88
RADIUS_MIN = 7
RADIUS_STEP = 8
MAX_SEGMENTS = 8
PLOT_H = 170
GOAL_SCALE_LIMIT = 4

NARROW_CHARS = frozenset("iltIjfr.,:;!|'’ ()[]/\u00a0·")
WIDE_CHARS = frozenset("mwMWЖШЩЮЫФЪжшщюыф—…%×−+=")
SCENARIO_CLASSES = {"base": "sc-base", "pessimistic": "sc-pess", "optimistic": "sc-opt"}
SCENARIO_LABELS = {key: label for key, label, _ in SCENARIOS}
LEGEND_ORDER = ("optimistic", "base", "pessimistic")
VERDICT_ICONS = {"go": "circle", "check": "triangle", "shelve": "square"}
PARAM_MONEY = ("price", "marketing_month")


def _n(x):
    text = f"{x:.1f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text_width(text, size=BASE_FONT, bold=False):
    total = 0.0
    for ch in text:
        if ch in NARROW_CHARS:
            total += 0.36
        elif ch in WIDE_CHARS:
            total += 0.95
        elif ch.isupper():
            total += 0.74
        elif ch.isdigit():
            total += 0.64
        else:
            total += 0.62
    return total * size * (1.07 if bold else 1.0) * 1.04


def _fit(text, width, size=BASE_FONT, bold=False):
    if _text_width(text, size, bold) <= width:
        return text
    while text and _text_width(text + "…", size, bold) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _wrap(text, width, size=BASE_FONT, bold=False, max_lines=3):
    lines, current = [], ""
    for word in (w for w in text.split(" ") if w):
        trial = f"{current} {word}" if current else word
        if current and _text_width(trial, size, bold) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    lines.append(current)
    lines = [_fit(line, width, size, bold) for line in lines]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _fit(lines[-1] + "…", width, size, bold)
    return lines


def _text(x, y, content, cls="", anchor=""):
    attrs = (f' class="{cls}"' if cls else "") + (f' text-anchor="{anchor}"' if anchor else "")
    return f'<text x="{_n(x)}" y="{_n(y)}"{attrs}>{_escape(content)}</text>'


def _line(x1, y1, x2, y2, cls):
    return f'<line class="{cls}" x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}"/>'


def _group(title, *parts):
    return f"<g><title>{_escape(title)}</title>{''.join(parts)}</g>"


def _bar(x, y, w, h, cls, direction=1):
    if w <= 0:
        return ""
    r = min(4.0, h / 2)
    if w < r * 2:
        left = x if direction > 0 else x - w
        return f'<rect class="{cls}" x="{_n(left)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}"/>'
    s = direction
    d = (f"M{_n(x)},{_n(y)}h{_n(s * (w - r))}a{_n(r)},{_n(r)} 0 0 {1 if s > 0 else 0} {_n(s * r)},{_n(r)}"
         f"v{_n(h - 2 * r)}a{_n(r)},{_n(r)} 0 0 {1 if s > 0 else 0} {_n(-s * r)},{_n(r)}h{_n(-s * (w - r))}z")
    return f'<path class="{cls}" d="{d}"/>'


def _track(x, y, w, h):
    return f'<rect class="ch-track" x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" rx="4"/>'


def _svg(name, height, title, desc, body):
    return (f'<svg class="chart-svg chart-{name}" xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {VB_W} {_n(height)}" width="100%" role="img">'
            f"<title>{_escape(title)}</title><desc>{_escape(desc)}</desc>{body}</svg>")


def _value_col(values):
    return max(30.0, max(_text_width(v, bold=True) for v in values) + 10)


def _score_text(x):
    return fmt_num(x, 0 if float(x).is_integer() else 1)


def _pct(x):
    scaled = x * 100
    for digits in (0, 1, 2):
        if abs(scaled - round(scaled, digits)) < 1e-7:
            return fmt_pct(x, digits)
    return fmt_pct(x, 2)


def _number_auto(x):
    return fmt_num(x, 0 if abs(x - round(x)) < 0.05 or abs(x) >= 100 else 1)


def _short_number(n):
    text = fmt_num(n, 0 if abs(n) >= 10 else 1)
    return text[:-2] if text.endswith(",0") else text


def _currency_symbol(currency):
    return {"RUB": "₽", "EUR": "€", "USD": "$"}.get(currency, currency)


def _with_currency(number, unit, currency, negative=False):
    sign = MINUS if negative else ""
    if currency == "USD":
        return f"{sign}${number}" + (NBSP + unit if unit else "")
    return f"{sign}{number}" + (NBSP + unit if unit else "") + NBSP + _currency_symbol(currency)


def _money_short(x, currency):
    if abs(x) < 1e4:
        return fmt_money(x, currency)
    for divisor, unit in ((1e3, "тыс."), (1e6, "млн"), (1e9, "млрд")):
        if round(abs(x) / divisor) < 1000 or unit == "млрд":
            break
    return _with_currency(_short_number(abs(x) / divisor), unit, currency, x < 0)


def _nice_ticks(lo, hi, target=4):
    if hi <= lo:
        hi = lo + 1
    span = hi - lo
    magnitude = 10 ** math.floor(math.log10(span / target))
    step = magnitude
    for factor in (1, 2, 2.5, 5, 10):
        step = factor * magnitude
        if span / step <= target:
            break
    first = math.floor(round(lo / step, 9))
    last = math.ceil(round(hi / step, 9))
    return [round(k * step, 9) for k in range(first, last + 1)], step


def _month_ticks(last):
    for step in (1, 2, 3, 6, 12, 24, 36):
        if last / step <= 6:
            return list(range(step, last + 1, step)) or [last]
    return [last]


def _chart_scorecard(calc, currency):
    sc = calc.get("scorecard")
    if not isinstance(sc, dict) or not isinstance(sc.get("criteria"), dict):
        return ""
    rows = [(key, title) for key, title, _ in CRITERIA if isinstance(sc["criteria"].get(key), dict)]
    if not rows:
        return ""
    value_w = _value_col([_score_text(sc["criteria"][key]["score"]) for key, _ in rows] + [fmt_num(sc["total"], 1)])
    track_w = VB_W - 2 * PAD - value_w
    value_x = VB_W - PAD
    scale = track_w / 10
    parts = [_text(PAD, 20, "Оценка идеи, баллы из 10", "ch-title")]
    y = 36
    summary = []
    for key, title in rows:
        item = sc["criteria"][key]
        score = item["score"]
        raw = item.get("raw_score", score)
        label = f"{title} · вес {_pct(item['weight'])}"
        flag = "только допущения" if item.get("evidence") == "assumption" else ""
        crowded = _text_width(label) + _text_width(flag, SMALL) + 14 > VB_W - 2 * PAD
        row = [_text(PAD, y + 11, label, "ch-l")]
        if flag:
            row.append(_text(VB_W - PAD, y + (24 if crowded else 11), flag, "ch-2 ch-sm", "end"))
        bar_y = y + (31 if crowded else 18)
        row.append(_track(PAD, bar_y, track_w, BAR_H))
        row.append(_bar(PAD, bar_y, score * scale, BAR_H, "ch-bar-dim" if flag else "ch-bar"))
        if raw > score:
            row.append(f'<rect class="ch-ghost" x="{_n(PAD + score * scale)}" y="{_n(bar_y)}" '
                       f'width="{_n((raw - score) * scale)}" height="{BAR_H}" rx="4"/>')
        row.append(_text(value_x, bar_y + 9, _score_text(score), "ch-v ch-num", "end"))
        hint = f"{title}: {_score_text(score)}"
        if raw > score:
            hint += f" (указано {_score_text(raw)}, ограничено допущением)"
        parts.append(_group(hint, *row))
        summary.append(f"{title} — {_score_text(score)}")
        y = bar_y + BAR_H + 12
    total, verdict = sc["total"], sc["verdict"]
    parts.append(_line(PAD, y - 4, VB_W - PAD, y - 4, "ch-grid"))
    y += 18
    verdict_label = VERDICT_LABELS[verdict]
    label_w = _text_width(verdict_label, bold=True)
    icon_x, icon_y = VB_W - PAD - label_w - 16, y - 4
    parts.append(_text(PAD, y, "Итог", "ch-l ch-b"))
    parts.append(_verdict_icon(verdict, icon_x, icon_y))
    parts.append(_text(VB_W - PAD, y, verdict_label, "ch-l ch-b", "end"))
    bar_y = y + 10
    total_h = 14
    parts.append(_track(PAD, bar_y, track_w, total_h))
    parts.append(_bar(PAD, bar_y, total * scale, total_h, f"ch-bar-status st-{verdict}"))
    low, high = float(VERDICT_CHECK_FROM), float(VERDICT_GO_ABOVE)
    for mark in (low, high):
        x = PAD + mark * scale
        parts.append(_line(x, bar_y - 3, x, bar_y + total_h + 3, "ch-tick-mark"))
        parts.append(_text(x, bar_y + total_h + 16, _score_text(mark), "ch-2 ch-sm", "middle"))
    parts.append(_text(value_x, bar_y + 12, fmt_num(total, 1), "ch-v ch-b ch-num", "end"))
    y = bar_y + total_h + 36
    notes = [f"Ниже {_score_text(low)} — отложить, выше {_score_text(high)} — делать"]
    if sc.get("gates"):
        reasons = "; ".join(GATE_LABELS[g] for g in sc["gates"])
        word = "Ограничитель" if len(sc["gates"]) == 1 else "Ограничители"
        notes.append(f"{word}: {reasons}. Вердикт не выше «{VERDICT_LABELS['check']}».")
    notes.append(f"Надёжность: {CONFIDENCE_LABELS[sc['confidence_label']]} ({fmt_pct(sc['confidence'])})")
    for note in notes:
        for line in _wrap(note, VB_W - 2 * PAD, SMALL):
            parts.append(_text(PAD, y, line, "ch-2 ch-sm"))
            y += 14
    desc = f"Итог {fmt_num(total, 1)} из 10, вердикт: {verdict_label}. " + "; ".join(summary)
    return _svg("scorecard", y + 4, "Оценка идеи", desc, "".join(parts))


def _verdict_icon(verdict, x, y):
    shape = VERDICT_ICONS[verdict]
    if shape == "circle":
        return f'<circle class="st-icon st-{verdict}" cx="{_n(x)}" cy="{_n(y)}" r="5"/>'
    if shape == "triangle":
        return f'<path class="st-icon st-{verdict}" d="M{_n(x)},{_n(y - 5.5)}l5.5,10h-11z"/>'
    return f'<rect class="st-icon st-{verdict}" x="{_n(x - 4.5)}" y="{_n(y - 4.5)}" width="9" height="9"/>'


def _chart_segments(calc, currency):
    rows = calc.get("segments")
    if not isinstance(rows, list) or not rows:
        return ""
    shown = rows[:MAX_SEGMENTS]
    track_w = VB_W - 2 * PAD - _value_col([fmt_num(row["total"], 1) for row in shown])
    scale = track_w / 10
    line_x = PAD + SEGMENT_PICK_ABOVE * scale
    parts = [_text(PAD, 20, "Сегменты: итоговая оценка из 10", "ch-title")]
    bars, texts, summary = [], [], []
    y = 36
    for row in shown:
        tag = ""
        if row.get("pick"):
            tag = "начинать с него" if row.get("meets_threshold") else f"лучший, но ниже {SEGMENT_PICK_ABOVE}"
        tag_w = _text_width(tag, SMALL) + 12 if tag else 0
        names = _wrap(row["name"], VB_W - 2 * PAD - tag_w, bold=bool(row.get("pick")), max_lines=2)
        name_cls = "ch-l ch-b ch-halo" if row.get("pick") else "ch-l ch-halo"
        texts.extend(_text(PAD, y + 11 + 14 * k, line, name_cls) for k, line in enumerate(names))
        if tag:
            texts.append(_text(VB_W - PAD, y + 11, tag, "ch-2 ch-sm ch-halo", "end"))
        bar_y = y + 18 + 14 * (len(names) - 1)
        total = row["total"]
        bars.append(_track(PAD, bar_y, track_w, BAR_H))
        bars.append(_bar(PAD, bar_y, total * scale, BAR_H, "ch-bar" if row.get("meets_threshold") else "ch-bar-dim"))
        bars.append(_line(line_x, bar_y - 3, line_x, bar_y + BAR_H + 3, "ch-tick-mark"))
        texts.append(_text(VB_W - PAD, bar_y + 9, fmt_num(total, 1), "ch-v ch-num", "end"))
        summary.append(f"{row['name']} — {fmt_num(total, 1)}")
        y = bar_y + BAR_H + 15
    parts.append(_line(line_x, 30, line_x, y - 8, "ch-ref"))
    parts.extend(bars)
    parts.extend(texts)
    parts.append(_text(line_x, y + 6, f"порог {SEGMENT_PICK_ABOVE}", "ch-2 ch-sm", "middle"))
    y += 22
    if len(rows) > len(shown):
        parts.append(_text(PAD, y + 6, f"Ещё {len(rows) - len(shown)} — в таблице разбора", "ch-2 ch-sm"))
        y += 18
    return _svg("segments", y + 4, "Сегменты", "Итоговая оценка сегментов из 10: " + "; ".join(summary), "".join(parts))


def _chart_market(calc, currency):
    mk = calc.get("market")
    if not isinstance(mk, dict):
        return ""
    named = (("tam", "TAM — весь рынок"), ("sam", "SAM — доступный рынок"), ("som", "SOM — реально занять"))
    items = [(key, label, mk[key]) for key, label in named if _finite(mk.get(key)) and mk[key] > 0]
    if len(items) < 2:
        return ""
    top = items[0][2]
    raw = [RADIUS_MAX * math.sqrt(min(value, top) / top) for _, _, value in items]
    radii = [float(RADIUS_MAX)] + [max(r, RADIUS_MIN) for r in raw[1:]]
    for i in range(len(radii) - 2, -1, -1):
        radii[i] = max(radii[i], radii[i + 1] + RADIUS_STEP)
    radii[0] = float(RADIUS_MAX)
    for i in range(1, len(radii)):
        radii[i] = min(radii[i], radii[i - 1] - RADIUS_STEP)
    to_scale = all(abs(r - w) < 1 for r, w in zip(radii, raw))
    cx, top_y = PAD + RADIUS_MAX, 34
    base = top_y + 2 * RADIUS_MAX
    label_x = cx + RADIUS_MAX + 22
    elbow_x = cx + RADIUS_MAX + 10
    label_w = VB_W - PAD - label_x
    parts = [_text(PAD, 20, "Рынок: TAM, SAM, SOM", "ch-title")]
    for k, r in enumerate(radii):
        parts.append(f'<circle class="mk-circle mk-{k}" cx="{_n(cx)}" cy="{_n(base - r)}" r="{_n(r)}"/>')
    blocks = []
    for k, ((key, label, value), r) in enumerate(zip(items, radii)):
        lines = [(label_line, "ch-l ch-b") for label_line in _wrap(label, label_w, bold=True, max_lines=2)]
        lines.append((_money_short(value, currency) + " в год", "ch-v"))
        if k > 0:
            parent = items[k - 1]
            lines.append((f"{_pct(value / parent[2])} от {parent[0].upper()}", "ch-2 ch-sm"))
        if key == "som" and mk.get("som_basis") == "capacity":
            lines.append(("потолок по воронке", "ch-2 ch-sm"))
        elif key == "som" and mk.get("som_basis") == "share" and _finite(mk.get("som_share_year3")):
            lines.append((f"доля {_pct(mk['som_share_year3'])} SAM за 3 года", "ch-2 ch-sm"))
        blocks.append({"anchor": base - 2 * r, "lines": lines, "height": 15 * len(lines) + 2})
    cursor = 0.0
    for block in blocks:
        block["y"] = max(block["anchor"] - 6, cursor, top_y - 6)
        cursor = block["y"] + block["height"] + 8
    for block in blocks:
        mid = block["y"] + 8
        parts.append(f'<path class="ch-leader" d="M{_n(cx)},{_n(block["anchor"])}H{_n(elbow_x)}V{_n(mid)}H{_n(label_x - 4)}"/>')
        parts.append(f'<circle class="mk-dot" cx="{_n(cx)}" cy="{_n(block["anchor"])}" r="2.5"/>')
        for k, (content, cls) in enumerate(block["lines"]):
            parts.append(_text(label_x, block["y"] + 12 + 15 * k, content, cls))
    y = max(base, cursor) + 20
    note = ("Площадь круга пропорциональна сумме." if to_scale
            else "Малые круги увеличены, чтобы их было видно: по размеру сравнивать нельзя.")
    for line in _wrap(note, VB_W - 2 * PAD, SMALL):
        parts.append(_text(PAD, y, line, "ch-2 ch-sm"))
        y += 14
    desc = "; ".join(f"{label}: {_money_short(value, currency)} в год" for _, label, value in items)
    return _svg("market", y + 2, "Рынок: TAM, SAM, SOM", desc, "".join(parts))


def _chart_funnel(calc, currency):
    fn = calc.get("funnel")
    if not isinstance(fn, dict) or not all(_finite(fn.get(k)) for k in ("visits", "signups", "paid")):
        return ""
    one_time = (calc.get("unit_economics") or {}).get("model") == "one_time"
    visits, signups, paid = fn["visits"], fn["signups"], fn["paid"]
    first = fn["cr_visit_signup"] if _finite(fn.get("cr_visit_signup")) else (signups / visits if visits else 0)
    second = fn["cr_signup_paid"] if _finite(fn.get("cr_signup_paid")) else (paid / signups if signups else 0)
    stages = [
        ("Визиты", visits, ""),
        ("Регистрации", signups, f"{_pct(first)} от визитов"),
        ("Покупки" if one_time else "Оплаты", paid, f"{_pct(second)} от регистраций"),
    ]
    track_w = VB_W - 2 * PAD - _value_col([_number_auto(v) for _, v, _ in stages])
    peak = max(visits, 1e-9)
    parts = [_text(PAD, 20, "Воронка за первый месяц", "ch-title")]
    y = 36
    for k, (label, value, note) in enumerate(stages):
        width = track_w * min(value / peak, 1.0)
        if value > 0:
            width = max(width, 3.0)
        cells = [_text(PAD, y + 11, label, "ch-l")]
        if note:
            cells.append(_text(VB_W - PAD, y + 11, note, "ch-2 ch-sm", "end"))
        cells.append(_track(PAD, y + 18, track_w, BAR_H))
        cells.append(_bar(PAD, y + 18, width, BAR_H, f"ch-bar ch-ord-{k + 1}"))
        cells.append(_text(VB_W - PAD, y + 27, _number_auto(value), "ch-v ch-num", "end"))
        parts.append(_group(f"{label}: {_number_auto(value)}", *cells))
        y += 40
    overall = paid / visits if visits > 0 else 0
    word = "покупку" if one_time else "оплату"
    parts.append(_text(PAD, y + 4, f"Из визита в {word}: {_pct(overall)}", "ch-2 ch-sm"))
    desc = "; ".join(f"{label}: {_number_auto(value)}" for label, value, _ in stages)
    return _svg("funnel", y + 14, "Воронка за первый месяц", desc, "".join(parts))


def _scenario_series(calc, key):
    forecast = calc.get("forecast")
    if not isinstance(forecast, dict):
        return {}
    found = {}
    for name in SCENARIO_CLASSES:
        data = forecast.get(name)
        months = data.get("months") if isinstance(data, dict) else None
        points = [(row["m"], row[key]) for row in months or [] if _finite(row.get(key))]
        if points:
            found[name] = points
    return found


def _axis_unit(peak):
    if peak >= 2e6:
        return 1e6, "млн"
    if peak >= 2e3:
        return 1e3, "тыс."
    return 1.0, ""


def _unit_caption(word, currency):
    symbol = _currency_symbol(currency)
    return f"{word} {symbol}".strip() if currency == "USD" else (f"{word}{NBSP}{symbol}" if word else symbol)


def _line_chart(title, caption, series, legend, currency, lo, hi, goal=None, zero=False, dots=()):
    ticks, step = _nice_ticks(lo, hi)
    lo, hi = ticks[0], ticks[-1]
    unit, unit_word = _axis_unit(max(abs(lo), abs(hi)))
    digits = 0 if abs(step / unit - round(step / unit)) < 1e-9 else 1
    months = [m for points in series.values() for m, _ in points]
    x_lo, x_hi = min(months), max(months)
    tick_labels = [fmt_num(tick / unit, digits) for tick in ticks]
    left, right = max(44.0, max(_text_width(label, SMALL) for label in tick_labels) + 12), VB_W - PAD
    top = 40 + 18 * len(legend) + 10
    bottom = top + PLOT_H

    def x_of(m):
        return (left + right) / 2 if x_hi == x_lo else left + (m - x_lo) / (x_hi - x_lo) * (right - left)

    def y_of(v):
        return bottom - (v - lo) / (hi - lo) * PLOT_H

    parts = [_text(PAD, 20, title, "ch-title")]
    if caption:
        parts.append(_text(VB_W - PAD, 20, caption, "ch-2 ch-sm", "end"))
    for k, (label, value, key_cls) in enumerate(legend):
        y = 40 + 18 * k
        parts.append(_line(PAD, y - 4, PAD + 18, y - 4, key_cls))
        parts.append(_text(PAD + 26, y, label, "ch-l"))
        parts.append(_text(VB_W - PAD, y, value, "ch-v", "end"))
    parts.append(_text(PAD, top - 10, _unit_caption(unit_word, currency), "ch-2 ch-sm"))
    for tick, label in zip(ticks, tick_labels):
        y = y_of(tick)
        cls = "ch-zero" if zero and abs(tick) < 1e-9 else "ch-axis" if abs(tick - lo) < 1e-9 else "ch-grid"
        parts.append(_line(left, y, right, y, cls))
        parts.append(_text(left - 6, y + 4, label, "ch-2 ch-sm ch-num", "end"))
    if goal is not None:
        y = y_of(goal)
        parts.append(_line(left, y, right, y, "ch-goal-line"))
        parts.append(_text(right, y - 5, f"Цель {fmt_money(goal, currency)}", "ch-2 ch-sm ch-halo", "end"))
    for m in _month_ticks(int(x_hi)):
        if x_lo <= m <= x_hi:
            parts.append(_text(x_of(m), bottom + 16, str(m), "ch-2 ch-sm ch-num", "middle"))
    parts.append(_text(left - 6, bottom + 16, "мес.", "ch-2 ch-sm", "end"))
    for scenario in LEGEND_ORDER[::-1]:
        points = series.get(scenario)
        if not points:
            continue
        path = " ".join(f"{'M' if k == 0 else 'L'}{_n(x_of(m))},{_n(y_of(v))}" for k, (m, v) in enumerate(points))
        base = "ch-line ch-line-base" if scenario == "base" else "ch-line"
        parts.append(f'<path class="{base} {SCENARIO_CLASSES[scenario]}" d="{path}"/>')
    for scenario, month, value, hint in dots:
        parts.append(f'<circle class="ch-dot {SCENARIO_CLASSES[scenario]}" cx="{_n(x_of(month))}" '
                     f'cy="{_n(y_of(value))}" r="4"><title>{_escape(hint)}</title></circle>')
    return parts, bottom + 26


def _legend_entry(scenario, value):
    return SCENARIO_LABELS[scenario], value, f"ch-key {SCENARIO_CLASSES[scenario]}"


def _chart_forecast(calc, currency):
    forecast = calc.get("forecast")
    if not isinstance(forecast, dict):
        return ""
    base_months = (forecast.get("base") or {}).get("months") or []
    if not base_months:
        return ""
    key = "mrr" if _finite(base_months[0].get("mrr")) else "revenue"
    series = _scenario_series(calc, key)
    if not series:
        return ""
    peak = max(v for points in series.values() for _, v in points)
    peak = max(peak, 0.0)
    goal = (calc.get("goal") or {}).get("goal_mrr")
    goal = goal if _finite(goal) and goal > 0 else None
    drawn = goal if goal is not None and goal <= GOAL_SCALE_LIMIT * max(peak, 1e-9) else None
    hi = max(peak, drawn or 0.0)
    horizon = max(m for points in series.values() for m, _ in points)
    legend, dots = [], []
    for scenario in LEGEND_ORDER:
        if scenario in series:
            month, value = series[scenario][-1]
            legend.append(_legend_entry(scenario, _money_short(value, currency)))
            dots.append((scenario, month, value, f"{SCENARIO_LABELS[scenario]}: {_money_short(value, currency)}"))
    if goal is not None:
        suffix = "" if drawn is not None else " — выше шкалы"
        legend.append(("Цель", fmt_money(goal, currency) + suffix, "ch-key ch-goal-key"))
    title = "MRR по месяцам" if key == "mrr" else "Выручка по месяцам"
    parts, height = _line_chart(title, f"значения на {horizon}-м мес.", series, legend, currency, 0.0, hi,
                                goal=drawn, dots=dots)
    desc = f"{title}: " + "; ".join(f"{label} — {value}" for label, value, _ in legend)
    return _svg("forecast", height, title, desc, "".join(parts))


def _chart_cash(calc, currency):
    forecast = calc.get("forecast")
    if not isinstance(forecast, dict):
        return ""
    series = _scenario_series(calc, "cash")
    if not series:
        return ""
    known = bool((forecast.get("base") or {}).get("cash_known"))
    values = [v for points in series.values() for _, v in points]
    lo, hi = min(0.0, min(values)), max(0.0, max(values))
    legend, dots = [], []
    for scenario in LEGEND_ORDER:
        if scenario not in series:
            continue
        data = forecast[scenario]
        points = series[scenario]
        if known:
            out = data.get("cash_out_month")
            fact = "не кончатся" if out is None else f"кончатся в мес. {out}"
            hit = None if out is None else next((p for p in points if p[0] == out), None)
        else:
            needed = data.get("cash_needed")
            if not _finite(needed) or needed <= 0:
                fact, hit = "вкладывать не нужно", None
            else:
                more = "больше " if data.get("breakeven_month") is None else ""
                fact = f"нужно {more}{_money_short(needed, currency)}"
                hit = min(points, key=lambda p: p[1])
        legend.append(_legend_entry(scenario, fact))
        if hit is not None:
            dots.append((scenario, hit[0], hit[1], f"{SCENARIO_LABELS[scenario]}: {fact}"))
    title = "Деньги на счёте" if known else "Прибыль нарастающим итогом"
    parts, height = _line_chart(title, "", series, legend, currency, lo, hi, zero=True, dots=dots)
    desc = f"{title}: " + "; ".join(f"{label} — {value}" for label, value, _ in legend)
    return _svg("cash", height, title, desc, "".join(parts))


def _param_text(param, value, currency):
    if param in PARAM_MONEY:
        return fmt_money(value, currency)
    if param == "traffic_month":
        return fmt_num(value, 0)
    return _pct(value)


def _tornado_domain(values):
    low, high = min(values), max(values)
    span = max(high - low, 0.2)
    lo, hi = max(low - 0.18 * span, 0.0), high + 0.18 * span
    for ref, *_ in LTV_CAC_BANDS[:2]:
        if ref < lo and lo - ref <= 0.8 * span:
            lo = max(ref - 0.05 * span, 0.0)
        elif ref > hi and ref - hi <= 0.8 * span:
            hi = ref + 0.05 * span
    return lo, hi


def _chart_sensitivity(calc, currency):
    rows = [r for r in calc.get("sensitivity") or []
            if isinstance(r, dict) and _finite(r.get("ltv_cac_low")) and _finite(r.get("ltv_cac_high"))]
    base = (calc.get("unit_economics") or {}).get("ltv_cac")
    if not rows or not _finite(base):
        return ""
    lo, hi = _tornado_domain([base] + [r[k] for r in rows for k in ("ltv_cac_low", "ltv_cac_high")])
    left, right = 42, VB_W - 42
    scale = (right - left) / (hi - lo)

    def x_of(v):
        return left + (v - lo) * scale

    step = fmt_pct(SENSITIVITY_STEP)
    parts = [_text(PAD, 20, f"Чувствительность LTV / CAC: параметр ±{step}", "ch-title")]
    for k, (cls, label) in enumerate((("ch-var-low", f"{MINUS}{step} к параметру"), ("ch-var-high", f"+{step} к параметру"))):
        x = PAD + k * 170
        parts.append(f'<rect class="ch-swatch {cls}" x="{_n(x)}" y="30" width="10" height="10" rx="2"/>')
        parts.append(_text(x + 16, 39, label, "ch-2 ch-sm"))
    y = 66
    plot_top = y - 8
    bars, texts = [], []
    for row in rows:
        param = row["param"]
        label = SENSITIVITY_LABELS.get(param, param)
        rng = f"{_param_text(param, row['low_value'], currency)} … {_param_text(param, row['high_value'], currency)}"
        names = _wrap(label, VB_W - 2 * PAD, max_lines=2)
        texts.extend(_text(PAD, y + 11 + 14 * k, line, "ch-l ch-halo") for k, line in enumerate(names))
        if len(names) == 1 and _text_width(names[0]) + _text_width(rng, SMALL) + 14 <= VB_W - 2 * PAD:
            texts.append(_text(VB_W - PAD, y + 11, rng, "ch-2 ch-sm ch-halo", "end"))
            line_count = 1
        else:
            texts.append(_text(PAD, y + 11 + 14 * len(names), rng, "ch-2 ch-sm ch-halo"))
            line_count = len(names) + 1
        bar_y = y + 18 + 14 * (line_count - 1)
        for variant, cls in (("low", "ch-var-low"), ("high", "ch-var-high")):
            value = row[f"ltv_cac_{variant}"]
            direction = 1 if value >= base else -1
            bars.append(_bar(x_of(base), bar_y, abs(x_of(value) - x_of(base)), BAR_H, cls, direction))
            anchor = "start" if direction > 0 else "end"
            texts.append(_text(x_of(value) + 5 * direction, bar_y + 9, fmt_num(value, 2), "ch-v ch-sm ch-num ch-halo", anchor))
        y = bar_y + BAR_H + 14
    plot_bottom = y - 8
    loss, healthy = (band[0] for band in LTV_CAC_BANDS[:2])
    refs = ((loss, f"{loss} — граница убытка"), (healthy, f"{healthy} — здоровая экономика"))
    shown_refs = [(value, text) for value, text in refs if lo <= value <= hi]
    for value, text in shown_refs:
        parts.append(_line(x_of(value), plot_top, x_of(value), plot_bottom, "ch-ref"))
        parts.append(_text(x_of(value), plot_top - 3, str(value), "ch-2 ch-sm", "middle"))
    parts.extend(bars)
    parts.append(_line(x_of(base), plot_top, x_of(base), plot_bottom, "ch-base-line"))
    parts.extend(texts)
    parts.append(_text(x_of(base), plot_bottom + 13, f"сейчас {fmt_num(base, 2)}", "ch-v ch-sm ch-num", "middle"))
    y = plot_bottom + 32
    if shown_refs:
        for line in _wrap("Линии: " + ", ".join(text for _, text in shown_refs), VB_W - 2 * PAD, SMALL):
            parts.append(_text(PAD, y, line, "ch-2 ch-sm"))
            y += 14
    desc = f"LTV/CAC сейчас {fmt_num(base, 2)}. " + "; ".join(
        f"{SENSITIVITY_LABELS.get(r['param'], r['param'])}: {fmt_num(r['ltv_cac_low'], 2)} … {fmt_num(r['ltv_cac_high'], 2)}"
        for r in rows)
    return _svg("sensitivity", y + 2, "Чувствительность LTV / CAC", desc, "".join(parts))


CHART_BUILDERS = {
    "scorecard": _chart_scorecard,
    "segments": _chart_segments,
    "market": _chart_market,
    "funnel": _chart_funnel,
    "forecast": _chart_forecast,
    "cash": _chart_cash,
    "sensitivity": _chart_sensitivity,
}


def render_chart(name: str, calc: dict, currency: str = "RUB") -> str:
    """Inline SVG графика по calc.json или пустая строка, если графика нет или данных для него нет."""
    builder = CHART_BUILDERS.get(name)
    if builder is None or not isinstance(calc, dict) or not calc:
        return ""
    return builder(calc, currency)


def _plain(markup):
    return re.sub(r"<[^>]+>", "", markup).strip()


def _read_css():
    with open(CSS_PATH, encoding="utf-8") as fh:
        return fh.read().strip()


def _toc(body):
    entries = re.findall(r'<h2 id="(s-\d+)">(.*?)</h2>', body, re.S)
    if not entries:
        return ""
    items = "\n".join(f'<li><a href="#{anchor}">{_plain(text)}</a></li>' for anchor, text in entries)
    return ('<nav class="toc" aria-label="Содержание">\n<details id="toc" open>\n<summary>Содержание</summary>\n'
            f"<ul>\n{items}\n</ul>\n</details>\n</nav>")


def _page_title(body, calc, title):
    if title:
        return _escape(title)
    heading = re.search(r"<h1 id=\"s-\d+\">(.*?)</h1>", body, re.S)
    if heading and _plain(heading.group(1)):
        return _plain(heading.group(1))
    name = ((calc or {}).get("project") or {}).get("name")
    return _escape(name) if name else "Отчёт"


def assemble(md, calc, title=None):
    """Страница и список пропущенных графиков: [(имя, причина)]."""
    calc = calc if isinstance(calc, dict) else None
    currency = ((calc or {}).get("project") or {}).get("currency") or "RUB"
    skipped = {}
    given = calc is not None

    def replace(match):
        name = match.group(1)
        if name not in CHART_BUILDERS:
            skipped.setdefault(name, "неизвестное имя графика")
            return ""
        try:
            svg = render_chart(name, calc, currency)
        except (LookupError, TypeError, ValueError, ArithmeticError, AttributeError) as exc:
            skipped.setdefault(name, f"ошибка построения ({type(exc).__name__}: {exc})")
            return ""
        if not svg:
            skipped.setdefault(name, "нет данных в calc.json" if given else "calc.json не передан")
            return ""
        return f'<figure class="chart chart-{name}">\n{svg}\n</figure>'

    body = MARKER_RE.sub(replace, markdown_to_html(md))
    toc = _toc(body)
    if toc:
        at = body.find("<h2 ")
        body = body[:at] + toc + "\n" + body[at:]
    page = (
        '<!doctype html>\n<html lang="ru">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f"<title>{_page_title(body, calc, title)}</title>\n<style>\n{_read_css()}\n</style>\n"
        f"<script>\n{THEME_JS}\n</script>\n</head>\n<body>\n<div class=\"report\">\n"
        '<button class="theme-toggle" id="theme-toggle" type="button" aria-label="Сменить тему оформления" '
        'title="Сменить тему">◐</button>\n'
        f"{body}\n</div>\n</body>\n</html>\n")
    return page, sorted(skipped.items())


def build_page(md: str, calc: dict | None, title: str | None = None) -> str:
    """Самодостаточная HTML-страница отчёта: CSS внутри, графики вместо маркеров."""
    return assemble(md, calc, title)[0]


def _load_json(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise ValueError(f"{path}: не удалось прочитать файл ({exc.strerror or exc})")
    except UnicodeDecodeError:
        raise ValueError(f"{path}: файл должен быть в кодировке UTF-8")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: некорректный JSON (строка {exc.lineno}, столбец {exc.colno}): {exc.msg}")
    if not isinstance(data, dict):
        raise ValueError(f"{path}: корень JSON должен быть объектом")
    return data


def main(argv: list[str] | None = None) -> int:
    """CLI: report.md (+ calc.json) → report.html; 2 — ошибка во входе, 1 — не удалось записать."""
    parser = argparse.ArgumentParser(
        prog="render_html.py", description="Собирает report.html из report.md и calc.json.")
    parser.add_argument("report", metavar="REPORT_MD", help="отчёт в Markdown")
    parser.add_argument("--calc", metavar="CALC_JSON", help="результат calc.py; без него графики не строятся")
    parser.add_argument("-o", "--out", metavar="OUT_HTML", help="куда записать страницу (по умолчанию report.html рядом с отчётом)")
    parser.add_argument("--title", metavar="T", help="заголовок страницы")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    try:
        with open(args.report, encoding="utf-8-sig") as fh:
            md = fh.read()
    except OSError as exc:
        print(f"{args.report}: не удалось прочитать файл ({exc.strerror or exc})", file=sys.stderr)
        return 2
    except UnicodeDecodeError:
        print(f"{args.report}: файл должен быть в кодировке UTF-8", file=sys.stderr)
        return 2
    try:
        calc = _load_json(args.calc) if args.calc else None
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        page, skipped = assemble(md, calc, args.title)
    except OSError as exc:
        print(f"Не найден стиль отчёта {os.path.normpath(CSS_PATH)}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    out_path = args.out or os.path.join(os.path.dirname(os.path.abspath(args.report)), "report.html")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(page)
    except OSError as exc:
        print(f"Не удалось записать файл: {exc.strerror or exc}", file=sys.stderr)
        return 1
    print(f"Записано: {out_path}")
    for name, reason in skipped:
        print(f"Предупреждение: график «{name}» пропущен: {reason}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
