# startup-launch-audit

Скилл для ИИ-агентов (Claude Code, claude.ai, Codex, Cursor и другие), который честно разбирает идею или уже запущенный продукт: от проблемы и спроса до экономики, каналов и плана на неделю. На выходе — вердикт с указанием надёжности и отчёт в Markdown и HTML с графиками.

Пример: [отчёт](examples/salon-reminder-bot/report.md) и его [HTML-версия](examples/salon-reminder-bot/report.html) (скачайте файл и откройте в браузере) — разбор вымышленной идеи «Telegram-бот записи для салонов красоты».

## Что делает

- **Расспрашивает коротко и по делу.**
  - Сначала три выбора: стадия, рынок, режим.
  - Потом четыре ключевых вопроса — по одному, с нажимом на конкретику.
  - Потом уточнения блоками до пяти вопросов.

  То, что агент может найти сам, у пользователя не спрашивается.
- **Ищет в интернете** (полный режим): конкурентов, цены, отзывы, жалобы, данные о рынке, сообщества — со ссылками и датами.
- **Проверяет стоп-факторы:** сильный бесплатный аналог, известный провал, закон и лицензии, зависимость от платформы.
- **Считает скриптом, а не «в уме»:**
  - оценку идеи с ограничителями и надёжностью;
  - сегменты;
  - TAM / SAM / SOM снизу вверх со сверкой сверху вниз;
  - юнит-экономику от маржи;
  - прогноз выручки на 36 месяцев с оттоком в трёх сценариях;
  - чувствительность, запас денег, расчёт от цели.
- **Пишет отчёт из 14 разделов** — от проблемы и клиента до плана на 7 дней. Плюс приложения: сценарий интервью, шаблоны сообщений, черновик лендинга, контент-план, источники, допущения, глоссарий.
- **Помечает каждую цифру:** факт (со ссылкой), оценка или допущение. Не выдумывает отзывы, кейсы и сообщества.
- **Для запущенных продуктов** выносит вердикт «масштабировать / докручивать / менять сегмент или продукт» — по экономике, удержанию и тесту PMF.
- **Хранит карточку проекта.** Можно вернуться с результатами интервью или теста — разбор обновится.

## Установка

**Любой агент** (Claude Code, Codex, Cursor и другие) — через [skills.sh](https://skills.sh):

```bash
npx skills add swan4er/startup-launch-audit
```

**Claude Code как плагин:**

```
/plugin marketplace add swan4er/startup-launch-audit
/plugin install startup-launch-audit@startup-launch-audit
```

**Вручную:**

```bash
git clone https://github.com/swan4er/startup-launch-audit.git
cp -r startup-launch-audit/skills/startup-launch-audit ~/.claude/skills/
```

**claude.ai:**
1. Скачайте `startup-launch-audit.zip` со страницы [Releases](https://github.com/swan4er/startup-launch-audit/releases).
2. В настройках (Settings → Capabilities) включите выполнение кода и загрузите архив в разделе Skills.

## Как пользоваться

Напишите агенту обычными словами: «оцени мою идею: …», «разбери мой стартап», «посчитай юнит-экономику», «пора ли вкладываться в рекламу — вот цифры». В Claude Code скилл можно вызвать и напрямую: `/startup-launch-audit`.

Два режима:
- **полный** — агент сам ищет конкурентов и данные о рынке; дольше, но точнее;
- **быстрый** — только по ответам пользователя; подходит, если идею нельзя светить в поисковике.

Ориентир по нашим прогонам на Claude Sonnet, вместе с ответами пользователя: 30–35 минут и 0,4–0,5 млн токенов. Быстрый режим экономит в основном время на поиск; основной расход — сам разбор и отчёт.

Результаты лежат в папке `startup-audit/<проект>/`:
- `report.md` и `report.html` — отчёт;
- `card.md` — карточка проекта;
- `data.json`, `calc.json`, `tables.md` — входные данные и расчёты;
- `dashboard.csv` — еженедельный дашборд метрик.

## Требования

- **Python 3.9+** — для расчётов и HTML; нужна только стандартная библиотека. Без Python агент считает вручную по тем же формулам и предупреждает об этом.
- **Поиск в интернете** — для полного режима. Без него скилл работает в быстром.

## Как устроено

| Файл | Что внутри |
|---|---|
| `skills/startup-launch-audit/SKILL.md` | Ход работы, правила, развилки по стадии |
| `skills/startup-launch-audit/references/` | Темы разбора, пороги (`benchmarks.md`), вопросы, шаблон отчёта |
| `skills/startup-launch-audit/scripts/calc.py` | Все расчёты |
| `skills/startup-launch-audit/scripts/render_html.py` | HTML-отчёт с графиками |
| `skills/startup-launch-audit/assets/` | Карточка проекта, стиль отчёта, шаблон дашборда |
| `tests/` | Тесты скриптов (`python3 -m unittest discover tests`) и персонажи для проверочных прогонов |

## Благодарности

Идеи подсмотрены у открытых скиллов; текст не заимствовался:
- [garrytan/gstack](https://github.com/garrytan/gstack) (office-hours);
- [maxkmet/idea-validation-agents](https://github.com/maxkmet/idea-validation-agents);
- [ferdinandobons/startup-skill](https://github.com/ferdinandobons/startup-skill);
- [wondelai/skills](https://github.com/wondelai/skills);
- [wshobson/agents](https://github.com/wshobson/agents);
- [lool-ventures/founder-skills](https://github.com/lool-ventures/founder-skills);
- [deanpeters/Product-Manager-Skills](https://github.com/deanpeters/Product-Manager-Skills);
- [mbfinotti/sales-skills](https://github.com/mbfinotti/sales-skills);
- [coreyhaines31/marketingskills](https://github.com/coreyhaines31/marketingskills);
- [emotixco/claude-skills-founder](https://github.com/emotixco/claude-skills-founder);
- [getagentseal/founder-playbook](https://github.com/getagentseal/founder-playbook);
- [refoundai/lenny-skills](https://github.com/refoundai/lenny-skills).

## Лицензия

[CC BY-NC 4.0](LICENSE): пользоваться и изменять можно с указанием автора, продавать нельзя.
