import copy
import glob
import html.parser
import io
import json
import math
import os
import random
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "startup-launch-audit", "scripts")
ASSETS = os.path.join(ROOT, "skills", "startup-launch-audit", "assets")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, SCRIPTS)

import calc  # noqa: E402
import render_html as rh  # noqa: E402

NBSP = "\u00a0"
SVG = "{http://www.w3.org/2000/svg}"
CHARTS = ("scorecard", "segments", "market", "funnel", "forecast", "cash", "sensitivity")
ALL_MARKERS = "\n\n".join(f"<!-- chart:{name} -->" for name in CHARTS)

BASE = dict(price=990, variable_cost_pct=0.4, churn_month=0.15, traffic_month=1000,
            cr_visit_signup=0.05, cr_signup_paid=0.10, marketing_month=10000)
CARD_KEYS = ("pain", "som", "growth", "ability_to_pay", "build_ease", "advantage")


def make_card(scores=8, evidence="fact", someone_pays=True):
    card = {key: {"score": scores, "evidence": evidence, "note": ""} for key in CARD_KEYS}
    if someone_pays is not None:
        card["someone_pays"] = someone_pays
    return card


PLAN_DATA = {
    "project": {"name": "Тест", "currency": "RUB", "stage": "mvp"},
    "scorecard": make_card(8),
    "segments": [
        {"name": "Салоны красоты", "size": 50000, "size_unit": "салонов", "pain": 9, "wtp": 9, "ease": 9},
        {"name": "Барбершопы", "pain": 5, "wtp": 6, "ease": 3},
    ],
    "market": {"unit": "клиентов", "tam_count": 15000000, "tam_price_year": 10000,
               "sam_count": 60000, "price_year": 50000},
    "economics": {**BASE, "cash_now": 100000, "goal_mrr": 100000},
}


def load_demo():
    with open(os.path.join(FIXTURES, "full-demo.json"), encoding="utf-8") as fh:
        return json.load(fh)


def plan_calc():
    return calc.compute(copy.deepcopy(PLAN_DATA))


def demo_calc():
    return calc.compute(load_demo())


def with_economics(**changes):
    data = copy.deepcopy(PLAN_DATA)
    data["economics"].update(changes)
    return calc.compute(data)


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = rh.main(argv)
    return code, out.getvalue(), err.getvalue()


class Balance(html.parser.HTMLParser):
    VOID = {"meta", "link", "br", "hr", "img", "input", "area", "base", "col", "embed", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        return None

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"лишний </{tag}>, открыт {self.stack[-1:]}")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
            return
        self.stack.pop()


def check_balanced(case, page):
    parser = Balance()
    parser.feed(page)
    parser.close()
    case.assertEqual(parser.errors, [])
    case.assertEqual(parser.stack, [])


def svg_numbers(root):
    for element in root.iter():
        for name in ("x", "y", "cx", "cy", "r", "x1", "x2", "y1", "y2", "width", "height", "rx"):
            value = element.get(name)
            if value is not None and re.fullmatch(r"-?[\d.]+(e-?\d+)?", value):
                yield element, name, float(value)


def assert_svg_sane(case, svg):
    root = ET.fromstring(svg)
    case.assertEqual(root.tag, SVG + "svg")
    case.assertEqual(root.get("width"), "100%")
    case.assertTrue(re.fullmatch(r"0 0 [\d.]+ [\d.]+", root.get("viewBox") or ""), root.get("viewBox"))
    case.assertEqual(root.get("role"), "img")
    title = root.find(SVG + "title")
    case.assertIsNotNone(title)
    case.assertTrue((title.text or "").strip())
    case.assertIsNone(re.search(r"\b(nan|inf|None)\b", svg, re.I))
    _, _, vb_w, vb_h = [float(part) for part in root.get("viewBox").split()]
    for element, name, value in svg_numbers(root):
        case.assertTrue(math.isfinite(value), (element.tag, name, value))
        if name in ("width", "height", "r") and element is not root:
            case.assertGreaterEqual(value, 0, (element.tag, name, value))
    for text in root.iter(SVG + "text"):
        x, y = float(text.get("x")), float(text.get("y"))
        case.assertTrue(0 <= x <= vb_w and 0 <= y <= vb_h, (text.text, x, y, vb_w, vb_h))
    return root


def texts(svg):
    root = ET.fromstring(svg)
    return [" ".join(part.strip() for part in t.itertext() if part.strip()) for t in root.iter(SVG + "text")]


def alltext(svg):
    return " ".join(texts(svg))


def classed(svg, tag, name):
    return [e for e in ET.fromstring(svg).iter(SVG + tag) if name in (e.get("class") or "").split()]


class MarkdownTests(unittest.TestCase):
    def test_heading_ids_count_all_headings(self):
        out = rh.markdown_to_html("# Разбор\n\n## 1. Коротко\n\n### Подраздел\n\n#### Глубже\n\n## 2. Дальше")
        self.assertIn('<h1 id="s-1">Разбор</h1>', out)
        self.assertIn('<h2 id="s-2">1. Коротко</h2>', out)
        self.assertIn('<h3 id="s-3">Подраздел</h3>', out)
        self.assertIn('<h4 id="s-4">Глубже</h4>', out)
        self.assertIn('<h2 id="s-5">2. Дальше</h2>', out)

    def test_heading_two_only(self):
        self.assertIn('<h2 id="s-1">Тема</h2>', rh.markdown_to_html("## Тема"))

    def test_inline_formatting(self):
        out = rh.markdown_to_html("Это **жирный**, *наклонный* и `код <b>` текст.")
        self.assertIn("<strong>жирный</strong>", out)
        self.assertIn("<em>наклонный</em>", out)
        self.assertIn("<code>код &lt;b&gt;</code>", out)

    def test_emphasis_nests_properly(self):
        cases = {
            "***оба***": "<em><strong>оба</strong></em>",
            "*вложенный **жирный** внутри*": "<em>вложенный <strong>жирный</strong> внутри</em>",
            "***a** b*": "<em><strong>a</strong> b</em>",
            "**незакрытый": "**незакрытый",
            "Текст ** пробелы ** тут": "Текст ** пробелы ** тут",
            "a*b*c и 2*3 = 6": "a*b*c и 2*3 = 6",
        }
        for source, expected in cases.items():
            self.assertEqual(rh.markdown_to_html(source), f"<p>{expected}</p>", source)

    def test_bold_wraps_link_and_code(self):
        out = rh.markdown_to_html("**[Источник](https://example.com/a)** и **`x`**")
        self.assertIn('<strong><a href="https://example.com/a"', out)
        self.assertIn("<strong><code>x</code></strong>", out)

    def test_underscores_in_identifiers_stay(self):
        out = rh.markdown_to_html("Поля churn_month и cr_signup_paid, а также 2*3 = 6.")
        self.assertIn("churn_month и cr_signup_paid", out)
        self.assertNotIn("<em>", out)

    def test_table_alignment_and_wrapper(self):
        out = rh.markdown_to_html("| a | b | c |\n|:---|:---:|---:|\n| 1 | 2 | 3 |")
        self.assertEqual(out.count('<div class="table-wrap">'), 1)
        self.assertIn("text-align:center", out)
        self.assertIn("text-align:right", out)
        self.assertIn("text-align:left", out)
        self.assertIn("<thead>", out)
        self.assertIn("<tbody>", out)

    def test_table_without_alignment_has_no_style(self):
        out = rh.markdown_to_html("| a | b |\n|---|---|\n| 1 | 2 |")
        self.assertNotIn("text-align", out)

    def test_table_escaped_pipe_stays_a_character(self):
        out = rh.markdown_to_html("| Имя | Значение |\n|---|---|\n| a \\| b | c |")
        self.assertIn("<td>a | b</td>", out)
        self.assertEqual(out.count("<td>"), 2)

    def test_table_ragged_rows(self):
        out = rh.markdown_to_html("| a | b |\n|---|---|\n| 1 |\n| 1 | 2 | 3 |")
        self.assertEqual(out.count("<td"), 4)
        self.assertEqual(out.count("<tr>"), 3)

    def test_table_cells_escape_html(self):
        out = rh.markdown_to_html("| a |\n|---|\n| <script>alert(1)</script> |")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", out)
        self.assertNotIn("<script", out)

    def test_pipes_without_delimiter_row_are_plain_text(self):
        out = rh.markdown_to_html("a | b\nпросто строка")
        self.assertNotIn("<table", out)
        self.assertIn("<p>", out)

    def test_nested_list(self):
        out = rh.markdown_to_html("- один\n  - вложенный\n  - ещё\n- два")
        self.assertEqual(out.count("<ul>"), 2)
        self.assertRegex(out, r"<li>один\s*<ul>\s*<li>вложенный</li>\s*<li>ещё</li>\s*</ul>\s*</li>\s*<li>два</li>")

    def test_ordered_and_star_lists(self):
        out = rh.markdown_to_html("1. первый\n2. второй\n\n* звёздочка\n* ещё")
        self.assertEqual(out.count("<ol>"), 1)
        self.assertEqual(out.count("<ul>"), 1)
        self.assertEqual(out.count("<li>"), 4)

    def test_ordered_list_keeps_start_number(self):
        self.assertIn('<ol start="3">', rh.markdown_to_html("3. три\n4. четыре"))

    def test_list_depth_is_capped_at_two(self):
        out = rh.markdown_to_html("- a\n  - b\n    - c\n      - d")
        self.assertEqual(out.count("<ul>"), 2)
        for word in "abcd":
            self.assertIn(f">{word}", out)

    def test_task_list(self):
        out = rh.markdown_to_html("- [ ] сделать\n- [x] готово\n- [X] тоже готово")
        self.assertIn("☐", out)
        self.assertEqual(out.count("☑"), 2)
        self.assertIn("сделать", out)
        self.assertNotIn("[ ]", out)

    def test_table_inside_list_item(self):
        out = rh.markdown_to_html("1. Шаг:\n   | a | b |\n   |---|---|\n   | 1 | 2 |\n2. Дальше")
        self.assertEqual(out.count("<table"), 1)
        self.assertEqual(out.count("<li"), 2)
        self.assertNotIn("| a |", out)

    def test_fenced_code_inside_list_item_keeps_dashes(self):
        out = rh.markdown_to_html("1. Команда:\n   ```bash\n   - не пункт\n   ```\n2. Дальше")
        self.assertIn("<pre><code", out)
        self.assertIn("- не пункт", out)
        self.assertEqual(out.count("<li"), 2)

    def test_blockquote_and_paragraph_inside_list_item(self):
        out = rh.markdown_to_html("- Пункт\n  > цитата\n- Другой\n\n  Второй абзац пункта.")
        self.assertRegex(out, r"<li>Пункт\s*<blockquote")
        self.assertIn("<p>Второй абзац пункта.</p>", out)

    def test_list_continuation_line(self):
        out = rh.markdown_to_html("- первая строка\n  продолжение\n- вторая")
        self.assertIn("первая строка", out)
        self.assertIn("продолжение", out)
        self.assertEqual(out.count("<li"), 2)

    def test_javascript_links_are_text(self):
        for url in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", " javascript:alert(1)", "data:text/html,x",
                    "vbscript:x", "//example.com/x", "/etc/passwd", "ссылка"):
            out = rh.markdown_to_html(f"[a]({url})")
            self.assertNotIn("href=", out, url)
            self.assertIn("<p>a</p>", out, url)

    def test_allowed_links(self):
        out = rh.markdown_to_html("[сайт](https://example.com/a?b=1&c=2) [почта](mailto:a@b.ru) [вверх](#s-2)")
        self.assertIn('href="https://example.com/a?b=1&amp;c=2"', out)
        self.assertIn('href="mailto:a@b.ru"', out)
        self.assertIn('<a href="#s-2">вверх</a>', out)
        self.assertIn('rel="noopener noreferrer"', out)

    def test_link_with_balanced_parentheses_and_title(self):
        out = rh.markdown_to_html('[w](https://ru.wikipedia.org/wiki/Foo_(bar) "подсказка") после')
        self.assertIn('href="https://ru.wikipedia.org/wiki/Foo_(bar)"', out)
        self.assertTrue(out.rstrip().endswith("после</p>"))

    def test_javascript_link_with_parentheses_leaves_no_href(self):
        out = rh.markdown_to_html("[a](javascript:alert(1))")
        self.assertNotIn('href="javascript', out)
        self.assertNotIn("href=", out)

    def test_quotes_in_url_cannot_break_attribute(self):
        out = rh.markdown_to_html('[a](https://x.ru/"onmouseover="alert(1))')
        self.assertNotIn('onmouseover="alert', out)

    def test_bare_url_becomes_link(self):
        out = rh.markdown_to_html("Смотри https://example.com/path, там всё.")
        self.assertIn('<a href="https://example.com/path"', out)
        self.assertIn("</a>, там всё.", out)

    def test_bracketed_labels_stay_text(self):
        out = rh.markdown_to_html("Цифра 15 % [оценка], ещё [допущение].")
        self.assertIn("[оценка]", out)
        self.assertIn("[допущение]", out)

    def test_images_are_not_loaded(self):
        out = rh.markdown_to_html("![схема](https://example.com/a.png)")
        self.assertNotIn("<img", out)
        self.assertNotIn("a.png", out)
        self.assertIn("схема", out)

    def test_raw_html_is_escaped(self):
        out = rh.markdown_to_html("<script>alert(1)</script>\n\nтекст <img src=x onerror=alert(1)> <b>x</b>")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", out)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", out)
        self.assertNotIn("<script", out)
        self.assertNotIn("<img", out)
        self.assertNotIn("<b>", out)

    def test_ampersand_and_quotes_escaped(self):
        out = rh.markdown_to_html('Тарифы A&B: "один" и \'два\'')
        self.assertIn("A&amp;B", out)
        self.assertNotIn("<p>Тарифы A&B", out)

    def test_blockquote_is_callout(self):
        out = rh.markdown_to_html("> **Вердикт: Делать** — итог 8 из 10.\n> Вторая строка.")
        self.assertIn("<blockquote", out)
        self.assertIn("<strong>Вердикт: Делать</strong>", out)
        self.assertIn("Вторая строка.", out)

    def test_blockquote_holds_list(self):
        out = rh.markdown_to_html("> - раз\n> - два")
        self.assertRegex(out, r"<blockquote[^>]*>\s*<ul>")

    def test_hr(self):
        for source in ("---", "***", "___", "- - -"):
            self.assertIn("<hr>", rh.markdown_to_html(f"до\n\n{source}\n\nпосле"), source)

    def test_fenced_code_is_escaped_and_verbatim(self):
        out = rh.markdown_to_html("```python\nif a < b:\n    print('**не жирный**')\n```\nпосле")
        self.assertIn("<pre><code", out)
        self.assertIn("if a &lt; b:", out)
        self.assertIn("    print(", out)
        self.assertNotIn("<strong>", out)
        self.assertIn("<p>после</p>", out)

    def test_unclosed_fence_runs_to_end(self):
        out = rh.markdown_to_html("```\nкод\nещё")
        self.assertIn("<pre><code>", out)
        self.assertIn("ещё", out)

    def test_chart_marker_survives_as_comment_only_on_its_own_line(self):
        out = rh.markdown_to_html("текст\n\n<!-- chart:forecast -->\n\nи в тексте <!-- chart:cash --> тоже")
        self.assertEqual(out.count("<!-- chart:forecast -->"), 1)
        self.assertNotIn("<!-- chart:cash -->", out)
        self.assertIn("&lt;!-- chart:cash --&gt;", out)

    def test_other_html_comments_are_dropped(self):
        out = rh.markdown_to_html("до\n\n<!-- заметка автора -->\n\n<!--\nмного\nстрок\n-->\n\nпосле")
        self.assertNotIn("заметка", out)
        self.assertNotIn("много", out)
        self.assertIn("<p>до</p>", out)
        self.assertIn("<p>после</p>", out)

    def test_paragraph_breaks(self):
        out = rh.markdown_to_html("строка один  \nстрока два\\\nстрока три\nи ещё")
        self.assertEqual(out.count("<br>"), 2)

    def test_empty_input(self):
        self.assertEqual(rh.markdown_to_html("").strip(), "")

    def test_nbsp_and_minus_preserved(self):
        out = rh.markdown_to_html(f"Выручка 20{NBSP}554{NBSP}₽ и −1{NBSP}500")
        self.assertIn(f"20{NBSP}554{NBSP}₽", out)
        self.assertIn("−1" + NBSP + "500", out)


class FuzzTests(unittest.TestCase):
    TOKENS = ("# ", "## ", "- ", "* ", "1. ", "2) ", "> ", "- [ ] ", "- [x] ", "| a | b |", "|---|:---:|", "|", "\\|", "---",
              "***", "```", "~~~", "`", "``", "**", "*", "[", "]", "(", ")", "![", "](", "](javascript:alert(1))",
              "](https://x.ru/a_(b))", "https://ex.com/p?q=1&r=2", "<script>alert(1)</script>", "<!-- chart:forecast -->",
              "<!-- x", "-->", "<", ">", "&", "\"", "'", "\\", "  \n", "\n", "\n\n", "   ", "    ", "\t", "слово", " ",
              "а_б_в", "\ue000", "\ue001", "\ufeff", "\r", "*a*", "**b**", "_")

    def test_random_documents_stay_balanced_and_inert(self):
        rnd = random.Random(20261030)
        for _ in range(1500):
            doc = "".join(rnd.choice(self.TOKENS) for _ in range(rnd.randint(1, 50)))
            page = rh.build_page(doc, None, None)
            check_balanced(self, rh.markdown_to_html(doc))
            self.assertEqual(len(re.findall(r"<script\b", page)), 1, repr(doc))
            self.assertIsNone(re.search(r"<img\b|href=\"(?!https?://|mailto:|#)", page), repr(doc))


class RepoMarkdownTests(unittest.TestCase):
    def test_repo_markdown_renders_balanced(self):
        paths = sorted(glob.glob(os.path.join(ROOT, "skills", "**", "*.md"), recursive=True)
                       + glob.glob(os.path.join(ROOT, "tests", "scenarios", "*.md")))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=os.path.relpath(path, ROOT)):
                with open(path, encoding="utf-8") as fh:
                    out = rh.markdown_to_html(fh.read())
                check_balanced(self, out)
                self.assertNotIn("<script", out)
                prose = re.sub(r"<pre>.*?</pre>", "", out, flags=re.S)
                self.assertIsNone(re.search(r"(?m)^\|?\s*:?-{3,}:?\s*\|", prose))


class ChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = plan_calc()
        cls.demo = demo_calc()

    def test_seven_markers_make_seven_svgs(self):
        page = rh.build_page("# Разбор\n\n## 1. Коротко\n\n" + ALL_MARKERS, self.plan, None)
        self.assertEqual(page.count("<svg"), 7)
        self.assertNotIn("chart:", page)

    def test_every_chart_is_sane_svg(self):
        for name in CHARTS:
            for source in (self.plan, self.demo):
                with self.subTest(chart=name, project=source["project"]["name"]):
                    svg = rh.render_chart(name, source, "RUB")
                    self.assertTrue(svg.startswith("<svg"), name)
                    assert_svg_sane(self, svg)

    def test_unknown_chart_and_missing_calc(self):
        self.assertEqual(rh.render_chart("pie", self.plan, "RUB"), "")
        self.assertEqual(rh.render_chart("forecast", None, "RUB"), "")
        self.assertEqual(rh.render_chart("forecast", {}, "RUB"), "")

    def test_no_calc_removes_markers_without_error(self):
        for source in (None, {}):
            page = rh.build_page("# Т\n\n## Р\n\n" + ALL_MARKERS + "\n\nконец", source, None)
            self.assertEqual(page.count("<svg"), 0)
            self.assertNotIn("chart:", page)
            self.assertIn("конец", page)

    def test_partial_data_draws_only_what_exists(self):
        only_card = calc.compute({"scorecard": make_card(7)})
        drawn = [name for name in CHARTS if rh.render_chart(name, only_card, "RUB")]
        self.assertEqual(drawn, ["scorecard"])
        no_card = calc.compute({"economics": PLAN_DATA["economics"]})
        drawn = [name for name in CHARTS if rh.render_chart(name, no_card, "RUB")]
        self.assertEqual(drawn, ["funnel", "forecast", "cash", "sensitivity"])
        self.assertEqual(rh.render_chart("scorecard", calc.compute({}), "RUB"), "")

    def test_scorecard_content(self):
        svg = rh.render_chart("scorecard", self.demo, "RUB")
        text = alltext(svg)
        for _key, title, weight in calc.CRITERIA:
            self.assertIn(title, text)
            self.assertIn(calc.fmt_pct(weight / 100), text)
        self.assertIn(calc.fmt_num(self.demo["scorecard"]["total"], 1), text)
        self.assertIn("только допущения", text)
        self.assertEqual(text.count("только допущения"), 1)
        self.assertIn("Итог", text)
        self.assertIn(calc.VERDICT_LABELS["check"], text)
        self.assertIn("7,5", text)

    def test_scorecard_marks_assumption_without_cap_and_gates(self):
        card = make_card(9, someone_pays=False)
        card["pain"] = {"score": 4, "evidence": "assumption", "note": ""}
        result = calc.compute({"scorecard": card})
        text = alltext(rh.render_chart("scorecard", result, "RUB"))
        self.assertIn("только допущения", text)
        self.assertIn(calc.GATE_LABELS["nobody_pays"], text)
        self.assertIn(calc.GATE_LABELS["pain_unproven"], text)

    def test_scorecard_verdict_classes(self):
        for score, evidence, verdict in ((9, "fact", "go"), (7, "fact", "check"), (4, "fact", "shelve")):
            result = calc.compute({"scorecard": make_card(score, evidence)})
            svg = rh.render_chart("scorecard", result, "RUB")
            self.assertEqual(result["scorecard"]["verdict"], verdict)
            self.assertIn(f"st-{verdict}", svg)
            self.assertIn(calc.VERDICT_LABELS[verdict], svg)

    def test_segments_content(self):
        svg = rh.render_chart("segments", self.demo, "RUB")
        assert_svg_sane(self, svg)
        text = alltext(svg)
        self.assertIn("Блогеры и авторы каналов", text)
        self.assertIn("7,3", text)
        self.assertIn("порог 7", text)
        self.assertIn("начинать с него", text)
        self.assertEqual(len(classed(svg, "line", "ch-ref")), 1)
        self.assertEqual(len(classed(svg, "line", "ch-tick-mark")), len(self.demo["segments"]))
        long_name = [row["name"] for row in self.demo["segments"] if len(row["name"]) > 60][0]
        words = long_name.replace(":", "").split()
        for word in (words[0], words[-1]):
            self.assertIn(word, text)

    def test_segments_best_below_threshold(self):
        result = calc.compute({"segments": [{"name": "Один", "pain": 5, "wtp": 5, "ease": 5}]})
        svg = rh.render_chart("segments", result, "RUB")
        self.assertIn("лучший, но ниже 7", svg)

    def test_segments_many_rows_are_capped(self):
        rows = [{"name": f"Сегмент {i}", "pain": 5, "wtp": 5, "ease": 5} for i in range(15)]
        svg = rh.render_chart("segments", calc.compute({"segments": rows}), "RUB")
        assert_svg_sane(self, svg)
        self.assertIn("Сегмент 7", svg)
        self.assertNotIn("Сегмент 14", svg)

    def test_market_areas_are_proportional(self):
        data = {"market": {"tam_count": 4e9, "sam_count": 1e9, "price_year": 1, "som_share_year3": 0.25}}
        svg = rh.render_chart("market", calc.compute(data), "RUB")
        radii = [float(c.get("r")) for c in ET.fromstring(svg).iter(SVG + "circle") if "mk-circle" in (c.get("class") or "")]
        self.assertEqual(len(radii), 3)
        self.assertAlmostEqual(radii[1] / radii[0], 0.5, places=2)
        self.assertAlmostEqual(radii[2] / radii[0], 0.25, places=2)
        self.assertNotIn("увеличены", svg)

    def test_market_small_circles_stay_visible_and_nested(self):
        svg = rh.render_chart("market", self.demo, "RUB")
        root = assert_svg_sane(self, svg)
        circles = [c for c in root.iter(SVG + "circle") if "mk-circle" in (c.get("class") or "")]
        radii = [float(c.get("r")) for c in circles]
        self.assertEqual(len(radii), 3)
        self.assertGreaterEqual(min(radii), 6)
        self.assertTrue(radii[0] > radii[1] > radii[2])
        bottoms = {round(float(c.get("cy")) + float(c.get("r")), 1) for c in circles}
        self.assertEqual(len(bottoms), 1)
        self.assertIn("увеличены", svg)
        text = alltext(svg)
        self.assertIn("TAM", text)
        self.assertIn("SAM", text)
        self.assertIn("SOM", text)
        self.assertIn(f"70{NBSP}млрд{NBSP}₽", text)

    def test_market_needs_two_values(self):
        one = calc.compute({"market": {"sam_count": 1000, "price_year": 100, "som_share_year3": 0}})
        self.assertEqual(rh.render_chart("market", one, "RUB"), "")
        two = calc.compute({"market": {"sam_count": 1000, "price_year": 100}})
        self.assertTrue(rh.render_chart("market", two, "RUB"))

    def test_funnel_texts(self):
        svg = rh.render_chart("funnel", self.demo, "RUB")
        text = alltext(svg)
        for word in ("Визиты", "Регистрации", "Оплаты", f"1{NBSP}500", "30", f"100{NBSP}%", f"2{NBSP}%"):
            self.assertIn(word, text)

    def test_funnel_one_time_says_purchases(self):
        result = calc.compute({"economics": dict(model="one_time", price=2000, variable_cost_pct=0.1, repeat_purchases=0.5,
                                                 traffic_month=500, cr_visit_signup=0.1, cr_signup_paid=0.1, marketing_month=10000)})
        svg = rh.render_chart("funnel", result, "RUB")
        self.assertIn("Покупки", svg)
        self.assertNotIn("Оплаты", svg)

    def test_forecast_title_and_goal(self):
        svg = rh.render_chart("forecast", self.demo, "RUB")
        text = alltext(svg)
        self.assertIn("MRR", text)
        for label in ("Пессимистичный", "Базовый", "Оптимистичный"):
            self.assertIn(label, text)
        self.assertIn("Цель", text)
        self.assertIn(calc.fmt_money(300000), text)

    def test_forecast_goal_inside_scale_is_drawn(self):
        result = calc.compute({"economics": {**BASE, "goal_mrr": 40000}})
        svg = rh.render_chart("forecast", result, "RUB")
        self.assertEqual(len(classed(svg, "line", "ch-goal-line")), 1)
        self.assertIn(calc.fmt_money(40000), alltext(svg))

    def test_forecast_far_goal_is_named_but_not_drawn(self):
        result = calc.compute({"economics": {**BASE, "goal_mrr": 5000000}})
        svg = rh.render_chart("forecast", result, "RUB")
        assert_svg_sane(self, svg)
        self.assertEqual(classed(svg, "line", "ch-goal-line"), [])
        text = alltext(svg)
        self.assertIn(calc.fmt_money(5000000), text)
        self.assertIn("выше шкалы", text)

    def test_forecast_without_goal_has_no_goal_row(self):
        result = calc.compute({"economics": BASE})
        self.assertNotIn("Цель", rh.render_chart("forecast", result, "RUB"))

    def test_forecast_one_time_shows_revenue(self):
        result = calc.compute({"economics": dict(model="one_time", price=2000, variable_cost_pct=0.1, repeat_purchases=0.5,
                                                 traffic_month=500, cr_visit_signup=0.1, cr_signup_paid=0.1, marketing_month=10000)})
        svg = rh.render_chart("forecast", result, "RUB")
        assert_svg_sane(self, svg)
        self.assertIn("Выручка", svg)
        self.assertNotIn("MRR", svg)

    def test_cash_title_follows_cash_known(self):
        known = rh.render_chart("cash", self.demo, "RUB")
        self.assertIn("Деньги на счёте", known)
        self.assertNotIn("Прибыль нарастающим итогом", known)
        data = load_demo()
        del data["economics"]["cash_now"]
        unknown = rh.render_chart("cash", calc.compute(data), "RUB")
        self.assertIn("Прибыль нарастающим итогом", unknown)
        self.assertNotIn("Деньги на счёте", unknown)

    def test_cash_facts_in_legend(self):
        text = alltext(rh.render_chart("cash", self.demo, "RUB"))
        self.assertIn("кончатся в мес. 4", text)
        data = load_demo()
        del data["economics"]["cash_now"]
        text = alltext(rh.render_chart("cash", calc.compute(data), "RUB"))
        self.assertIn("нужно", text)

    def test_cash_has_zero_line(self):
        self.assertEqual(len(classed(rh.render_chart("cash", self.demo, "RUB"), "line", "ch-zero")), 1)

    def test_sensitivity_reference_lines_follow_data(self):
        near = rh.render_chart("sensitivity", self.plan, "RUB")
        far = rh.render_chart("sensitivity", self.demo, "RUB")
        for svg in (near, far):
            assert_svg_sane(self, svg)
        self.assertEqual(len(classed(near, "line", "ch-ref")), 2)
        self.assertEqual(len(classed(far, "line", "ch-ref")), 1)

    def test_sensitivity_labels_and_base(self):
        text = alltext(rh.render_chart("sensitivity", self.demo, "RUB"))
        self.assertIn(calc.SENSITIVITY_LABELS["churn_month"], text)
        self.assertIn(calc.SENSITIVITY_LABELS["price"], text)
        self.assertIn(calc.fmt_num(self.demo["unit_economics"]["ltv_cac"], 2), text)

    def test_zero_and_extreme_inputs_do_not_crash(self):
        cases = [
            dict(churn_month=0),
            dict(marketing_month=0),
            dict(cr_signup_paid=0),
            dict(traffic_month=0),
            dict(traffic_month=0, marketing_month=0, fixed_costs_month=0),
            dict(price=1, traffic_month=1),
            dict(churn_month=1),
            dict(horizon_months=1),
            dict(horizon_months=2),
            dict(horizon_months=120),
            dict(cash_now=0),
            dict(start_customers=5000, traffic_growth_month=0.2),
            dict(goal_mrr=1),
            dict(goal_mrr=1e9),
            dict(variable_cost_pct=1),
            dict(variable_cost_pct=0),
            dict(cac=0),
            dict(model="one_time", repeat_purchases=0),
            dict(model="one_time", repeat_purchases=3, cac=100),
        ]
        for changes in cases:
            result = with_economics(**changes)
            for name in CHARTS:
                with self.subTest(changes=changes, chart=name):
                    svg = rh.render_chart(name, result, "RUB")
                    if svg:
                        assert_svg_sane(self, svg)
            page = rh.build_page("# Т\n\n## Р\n\n" + ALL_MARKERS, result, None)
            check_balanced(self, page)

    def test_all_currencies(self):
        for code, mark in (("RUB", "₽"), ("USD", "$"), ("EUR", "€")):
            data = load_demo()
            data["project"]["currency"] = code
            result = calc.compute(data)
            svg = rh.render_chart("forecast", result, code)
            self.assertIn(mark, svg)
            for other in {"₽", "$", "€"} - {mark}:
                self.assertNotIn(other, svg)

    def test_page_uses_project_currency(self):
        data = load_demo()
        data["project"]["currency"] = "USD"
        page = rh.build_page("## Р\n\n<!-- chart:forecast -->", calc.compute(data), None)
        self.assertIn("$", page)
        self.assertNotIn("₽", page)

    def test_numbers_use_decimal_comma_and_nbsp(self):
        svg = rh.render_chart("sensitivity", self.demo, "RUB")
        found = texts(svg)
        self.assertTrue(any(re.fullmatch(r"\d,\d\d", t) for t in found), found)
        self.assertFalse(any(re.search(r"\d\.\d", t) for t in found), found)

    def test_chart_text_is_escaped(self):
        data = load_demo()
        data["segments"][0]["name"] = "<script>alert(1)</script> & \"кавычки\""
        svg = rh.render_chart("segments", calc.compute(data), "RUB")
        self.assertNotIn("<script", svg)
        assert_svg_sane(self, svg)


class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = plan_calc()
        cls.md = "# Разбор: Бот — 30.10.2026\n\n> Стадия: идея\n\n## 1. Коротко\n\nТекст.\n\n## 2. Проблема\n\n### Детали\n\nЕщё.\n"
        cls.page = rh.build_page(cls.md, cls.plan, None)

    def test_basics(self):
        page = self.page
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn('<html lang="ru"', page)
        self.assertIn("<title>Разбор: Бот — 30.10.2026</title>", page)
        self.assertIn('name="viewport"', page)
        self.assertIn("prefers-color-scheme", page)
        self.assertIn("@media print", page)
        self.assertIn(':root[data-theme="dark"]', page)
        self.assertIn(':root:not([data-theme="light"])', page)

    def test_title_argument_wins_and_is_escaped(self):
        page = rh.build_page(self.md, None, "Свой <заголовок> & ко")
        self.assertIn("<title>Свой &lt;заголовок&gt; &amp; ко</title>", page)

    def test_title_fallbacks(self):
        self.assertIn("<title>Отчёт</title>", rh.build_page("Просто текст", None, None))
        named = calc.compute({"project": {"name": "Моя идея"}})
        self.assertIn("<title>Моя идея</title>", rh.build_page("Просто текст", named, None))

    def test_toc_lists_second_level_headings_only(self):
        page = self.page
        nav = re.search(r'<nav[^>]*class="toc"[^>]*>(.*?)</nav>', page, re.S).group(1)
        self.assertIn('href="#s-2"', nav)
        self.assertIn('href="#s-3"', nav)
        self.assertIn("1. Коротко", nav)
        self.assertIn("2. Проблема", nav)
        self.assertNotIn("Детали", nav)
        self.assertNotIn("Разбор", nav)
        self.assertLess(page.index('class="toc"'), page.index('<h2 id="s-2">'))
        self.assertGreater(page.index('class="toc"'), page.index("Стадия: идея"))

    def test_no_toc_without_second_level(self):
        self.assertNotIn('class="toc"', rh.build_page("# Только заголовок\n\nТекст", None, None))

    def test_charts_are_wrapped_in_figures(self):
        page = rh.build_page("## Р\n\n<!-- chart:scorecard -->", self.plan, None)
        self.assertRegex(page, r'<figure class="chart chart-scorecard">\s*<svg')

    def test_page_is_well_formed(self):
        for source in (self.plan, demo_calc(), None):
            page = rh.build_page(self.md + "\n" + ALL_MARKERS + "\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- [ ] x\n  - y\n", source, None)
            check_balanced(self, page)

    def test_no_external_requests(self):
        page = rh.build_page(self.md + ALL_MARKERS, demo_calc(), None)
        self.assertIsNone(re.search(r"<link\b", page))
        self.assertIsNone(re.search(r"<script[^>]+src=", page))
        self.assertIsNone(re.search(r"<img\b|<iframe\b|<object\b|<embed\b", page))
        self.assertIsNone(re.search(r"@import|url\(", page))

    def test_theme_script_is_short_and_guarded(self):
        scripts = re.findall(r"<script>(.*?)</script>", self.page, re.S)
        self.assertEqual(len(scripts), 1)
        lines = [line for line in scripts[0].strip().splitlines() if line.strip()]
        self.assertLessEqual(len(lines), 30)
        self.assertIn("localStorage", scripts[0])
        self.assertGreaterEqual(scripts[0].count("try"), 2)
        self.assertIn("data-theme", scripts[0])
        self.assertIn('class="theme-toggle"', self.page)

    def test_hostile_report_is_inert(self):
        hostile = (
            "# <script>alert('h1')</script>\n\n## <img src=x onerror=alert(2)> раздел\n\n"
            "[клик](javascript:alert(3)) и [ещё](JAVASCRIPT:alert(4))\n\n"
            "| <script>alert(5)</script> | b |\n|---|---|\n| x \\| y | <b onclick=alert(6)>z</b> |\n\n"
            "> <script>alert(7)</script>\n\n```\n<script>alert(8)</script>\n```\n\n"
            "<script>alert(9)</script>\n\n<!-- chart:<script> -->\n"
        )
        page = rh.build_page(hostile, self.plan, "<script>alert(10)</script>")
        self.assertEqual(len(re.findall(r"<script\b", page)), 1)
        self.assertEqual(len(re.findall(r"<img\b", page)), 0)
        self.assertNotIn("href=\"javascript", page.lower())
        self.assertNotIn("<b onclick", page)
        for n in range(1, 11):
            self.assertNotIn(f"alert({n})</script>", page)
        check_balanced(self, page)

    def test_css_is_inlined_from_assets(self):
        with open(os.path.join(ASSETS, "report.css"), encoding="utf-8") as fh:
            css = fh.read().strip()
        self.assertIn(css, self.page)


class CssTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ASSETS, "report.css"), encoding="utf-8") as fh:
            cls.css = re.sub(r"\s+", " ", fh.read())

    def test_theme_scopes(self):
        self.assertIn("@media (prefers-color-scheme: dark)", self.css)
        self.assertIn(':root:not([data-theme="light"])', self.css)
        self.assertIn(':root[data-theme="dark"]', self.css)

    def test_width_and_gutters(self):
        self.assertIn("max-width: 860px", self.css)
        self.assertRegex(self.css, r"padding:[^;}]*16px")

    def test_tables_scroll_inside_their_wrapper(self):
        self.assertRegex(self.css, r"\.table-wrap \{[^}]*overflow-x: auto")

    def test_print_rules(self):
        block = self.css[self.css.index("@media print"):]
        for needle in (".toc", ".theme-toggle", "display: none", "break-inside: avoid", "figure.chart", ".table-wrap"):
            self.assertIn(needle, block)
        self.assertIn(':root[data-theme="dark"]', block)

    def test_no_external_resources(self):
        self.assertNotIn("@import", self.css)
        self.assertNotIn("url(", self.css)
        self.assertNotIn("http", self.css)

    def test_every_chart_class_used_by_the_renderer_is_styled(self):
        no_cash = load_demo()
        del no_cash["economics"]["cash_now"]
        one_time = {"economics": dict(model="one_time", price=2000, variable_cost_pct=0.1, repeat_purchases=0.5,
                                      traffic_month=500, cr_visit_signup=0.1, cr_signup_paid=0.1,
                                      marketing_month=10000, goal_mrr=50000)}
        gated = make_card(9, someone_pays=False)
        gated["pain"] = {"score": 9, "evidence": "assumption", "note": ""}
        sources = [plan_calc(), demo_calc(), calc.compute(no_cash), calc.compute(one_time),
                   calc.compute({"scorecard": make_card(9)}), calc.compute({"scorecard": make_card(3)}),
                   calc.compute({"scorecard": gated}), with_economics(goal_mrr=5000000),
                   calc.compute({"segments": [{"name": "Один", "pain": 5, "wtp": 5, "ease": 5}]})]
        classes = set()
        for source in sources:
            for name in CHARTS:
                svg = rh.render_chart(name, source, "RUB")
                if svg:
                    for element in ET.fromstring(svg).iter():
                        classes.update((element.get("class") or "").split())
        classes -= {f"chart-{name}" for name in CHARTS}
        missing = sorted(c for c in classes if f".{c}" not in self.css)
        self.assertEqual(missing, [])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def write(self, name, text):
        with open(self.path(name), "w", encoding="utf-8") as fh:
            fh.write(text)
        return self.path(name)

    def test_main_writes_report_next_to_input(self):
        md = self.write("report.md", "# Отчёт\n\n## Раздел\n\nТекст\n")
        code, out, err = run_main([md])
        self.assertEqual(code, 0, err)
        target = self.path("report.html")
        self.assertTrue(os.path.exists(target))
        self.assertIn("report.html", out)
        with open(target, encoding="utf-8") as fh:
            self.assertIn("<h2 id=\"s-2\">Раздел</h2>", fh.read())

    def test_main_with_calc_out_and_title(self):
        md = self.write("r.md", "## Р\n\n" + ALL_MARKERS + "\n")
        calc_path = self.write("c.json", json.dumps(plan_calc(), ensure_ascii=False))
        target = self.path("sub/out.html")
        code, out, err = run_main([md, "--calc", calc_path, "-o", target, "--title", "Мой отчёт"])
        self.assertEqual(code, 0, err)
        with open(target, encoding="utf-8") as fh:
            page = fh.read()
        self.assertEqual(page.count("<svg"), 7)
        self.assertIn("<title>Мой отчёт</title>", page)
        self.assertEqual(out.count("Предупреждение"), 0)

    def test_main_reads_calc_json_written_by_calc_py(self):
        data = self.write("data.json", json.dumps(load_demo(), ensure_ascii=False))
        out_path = self.path("calc.json")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(calc.main([data, "--out", out_path]), 0)
        md = self.write("r.md", "## Р\n\n" + ALL_MARKERS + "\n")
        code, _, err = run_main([md, "--calc", out_path])
        self.assertEqual(code, 0, err)
        with open(self.path("report.html"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().count("<svg"), 7)

    def test_main_warns_about_charts_without_data(self):
        md = self.write("r.md", "## Р\n\n<!-- chart:forecast -->\n\n<!-- chart:scorecard -->\n\n<!-- chart:bogus -->\n")
        calc_path = self.write("c.json", json.dumps(calc.compute({"scorecard": make_card(7)})))
        code, out, err = run_main([md, "--calc", calc_path])
        self.assertEqual(code, 0, err)
        self.assertIn("Предупреждение", out)
        self.assertIn("forecast", out)
        self.assertIn("bogus", out)
        self.assertNotIn("scorecard", out)

    def test_main_without_calc_warns_once_per_chart(self):
        md = self.write("r.md", "## Р\n\n<!-- chart:forecast -->\n\n<!-- chart:forecast -->\n")
        code, out, _ = run_main([md])
        self.assertEqual(code, 0)
        self.assertEqual(out.count("forecast"), 1)

    def test_main_missing_input_is_exit_2(self):
        code, out, err = run_main([self.path("нет.md")])
        self.assertEqual(code, 2)
        self.assertIn("нет.md", err)
        self.assertEqual(out, "")

    def test_main_bad_calc_is_exit_2(self):
        md = self.write("r.md", "## Р\n")
        broken = self.write("broken.json", "{не json")
        code, _, err = run_main([md, "--calc", broken])
        self.assertEqual(code, 2)
        self.assertIn("broken.json", err)
        code, _, err = run_main([md, "--calc", self.path("missing.json")])
        self.assertEqual(code, 2)
        self.assertIn("missing.json", err)
        listing = self.write("list.json", "[1, 2]")
        code, _, err = run_main([md, "--calc", listing])
        self.assertEqual(code, 2)

    def test_main_reads_utf8_with_bom(self):
        md = self.path("bom.md")
        with open(md, "w", encoding="utf-8-sig") as fh:
            fh.write("# Заголовок\n\n## Раздел\n")
        code, _, err = run_main([md])
        self.assertEqual(code, 0, err)
        with open(self.path("report.html"), encoding="utf-8") as fh:
            self.assertIn('<h1 id="s-1">Заголовок</h1>', fh.read())

    def test_main_unwritable_target_is_exit_1(self):
        md = self.write("r.md", "## Р\n")
        blocker = self.write("file.txt", "x")
        code, _, err = run_main([md, "-o", os.path.join(blocker, "out.html")])
        self.assertEqual(code, 1)
        self.assertTrue(err.strip())


class AssetTests(unittest.TestCase):
    def test_dashboard_template(self):
        with open(os.path.join(ASSETS, "dashboard-template.csv"), "rb") as fh:
            raw = fh.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        rows = [line.split(";") for line in raw.decode("utf-8-sig").splitlines()]
        self.assertEqual(rows[0], ["Метрика", "Неделя 1", "Неделя 2", "Неделя 3", "Неделя 4", "Тренд"])
        self.assertEqual([row[0] for row in rows[1:]], [
            "Визиты на лендинг", "Регистрации", "Конверсия лендинг → регистрация", "Активации (Aha-момент)",
            "Новые платящие", "Оплаты, ₽", "MRR, ₽", "Отток, %", "Расходы на привлечение, ₽", "CAC, ₽"])
        for row in rows:
            self.assertEqual(len(row), 6, row)
        for row in rows[1:]:
            self.assertEqual(row[1:], [""] * 5)


if __name__ == "__main__":
    unittest.main()
