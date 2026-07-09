from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from tg_event.event_schema import ParsedEvent, validate_event_response


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def build_prompt(text: str, published_at: str, source: str, city: str) -> str:
    return f"""
Ты извлекаешь события из постов публичных Telegram-каналов для афиши города.

Контекст:
- город: {city}
- источник: {source}
- дата публикации: {published_at}

Верни только строгий JSON без Markdown и комментариев.
Схема:
{{
  "events": [
    {{
      "is_event": true,
      "title": string | null,
      "date": "YYYY-MM-DD" | null,
      "end_date": "YYYY-MM-DD" | null,
      "time": "HH:MM" | null,
      "end_time": "HH:MM" | null,
      "place": string | null,
      "address": string | null,
      "category": "music" | "cinema" | "lecture" | "exhibition" | "market" | "workshop" | "food" | "sport" | "party" | "kids" | "other",
      "price": string | null,
      "confidence": number,
      "reason": string
    }}
  ],
  "post_reason": string
}}

Правила:
- Один Telegram-пост может содержать 0, 1 или много событий.
- Для каждого найденного события добавь отдельный объект в "events".
- Если в посте нет конкретных событий для посещения, верни пустой массив "events": [].
- Внутри массива "events" поле "is_event" всегда true.
- Если дата относительная, интерпретируй её относительно даты публикации.
- Если событие длится несколько дней, заполни "date" датой начала и "end_date" датой окончания.
- Если дата окончания совпадает с "date", ставь "end_date": null.
- Если указано время окончания, заполни "end_time"; если только время начала, "end_time": null.
- Если указана стоимость или вход свободный, заполни "price" исходной короткой формулировкой.
- Категория должна быть только одной из: music, cinema, lecture, exhibition, market, workshop, food, sport, party, kids, other.
- place пиши как название площадки в именительном падеже, без предлогов "в", "на", "у" и без падежных форм.
- Не считай постоянные услуги, товары, меню, скидки, обычный режим работы и просто места событием.
- Событие должно иметь конкретную дату или ограниченный период проведения, а не только расписание работы места.
- Если данных нет или они неоднозначны, используй null и снижай confidence.
- confidence должен быть от 0 до 1.

Пост:
{text}
""".strip()


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Model response does not contain a JSON object")

    try:
        parsed = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model response contains invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Model response JSON must be an object")
    return parsed


def parse_event_with_openrouter(
    *,
    api_key: str,
    model: str,
    fallback_model: str,
    text: str,
    published_at: str,
    source: str,
    city: str,
) -> list[ParsedEvent]:
    prompt = build_prompt(
        text=text,
        published_at=published_at,
        source=source,
        city=city,
    )

    try:
        payload = _request_openrouter(api_key=api_key, model=model, prompt=prompt)
    except (ValueError, urllib.error.HTTPError, urllib.error.URLError):
        payload = _request_openrouter(api_key=api_key, model=fallback_model, prompt=prompt)

    content = payload["choices"][0]["message"]["content"]
    return validate_event_response(extract_json_object(content)).events


def _request_openrouter(api_key: str, model: str, prompt: str) -> dict[str, Any]:
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Return valid JSON only. Do not wrap it in Markdown.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://example.com",
            "X-Title": "tg-event-poc",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))
