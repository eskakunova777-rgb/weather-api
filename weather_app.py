"""Консольное приложение погоды на OpenWeather API.

Ключ берётся из файла .env (переменная API_KEY), в коде его нет.
Режимы: 1 - по городу, 2 - по координатам, 0 - выход.
Последний успешный ответ сохраняется в weather_cache.json,
при сбое сети программа предлагает показать его, если ему меньше 3 часов.
"""

import json
import os
import time
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("API_KEY")

# Адреса можно переопределить переменными окружения (удобно для проверки сбоев сети)
BASE_URL = os.getenv("OPENWEATHER_BASE_URL", "https://api.openweathermap.org")
GEO_URL = f"{BASE_URL}/geo/1.0/direct"
WEATHER_URL = f"{BASE_URL}/data/2.5/weather"
TIMEOUT = float(os.getenv("OPENWEATHER_TIMEOUT", "10"))

CACHE_FILE = "weather_cache.json"
CACHE_MAX_AGE = timedelta(hours=3)
RETRY_DELAYS = [1, 2, 4]  # паузы перед повторами, секунды
RETRY_STATUSES = {429, 500, 502, 503, 504}


class WeatherError(Exception):
    """Понятная пользователю ошибка (выводится без трейсбека)."""


class NetworkError(WeatherError):
    """Сеть недоступна или сервер не ответил после всех повторов."""


def _request(url: str, params: dict) -> requests.Response:
    """GET-запрос с ключом и повторами при 429, 5xx и временных сбоях сети."""
    if not API_KEY:
        raise WeatherError(
            "Не найден API-ключ. Создайте файл .env по образцу .env.example "
            "и впишите в него API_KEY=ваш_ключ."
        )
    params = {**params, "appid": API_KEY}
    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(attempts):
        try:
            response = requests.get(url, params=params, timeout=TIMEOUT)
        except (requests.ConnectionError, requests.Timeout):
            # Текст исключения не печатаем: в нём адрес запроса вместе с ключом
            response = None
        if response is not None and response.status_code not in RETRY_STATUSES:
            return response
        if attempt < len(RETRY_DELAYS):
            delay = RETRY_DELAYS[attempt]
            reason = (
                "сервер просит подождать (429)"
                if response is not None and response.status_code == 429
                else "временная ошибка сети или сервера"
            )
            print(f"  {reason}, повтор через {delay} с ({attempt + 1}/{len(RETRY_DELAYS)})")
            time.sleep(delay)
    if response is not None and response.status_code == 429:
        raise WeatherError("Превышен лимит запросов к OpenWeather (429). Попробуйте позже.")
    raise NetworkError("Нет связи с сервером OpenWeather. Проверьте интернет.")


def _check_status(response: requests.Response) -> None:
    """Переводит код ответа в понятное сообщение."""
    if response.status_code == 200:
        return
    if response.status_code == 401:
        raise WeatherError(
            "Ключ API не принят (401). Проверьте API_KEY в .env. "
            "Новый ключ OpenWeather начинает работать не сразу, а в течение пары часов."
        )
    if response.status_code == 404:
        raise WeatherError("Данные не найдены (404).")
    raise WeatherError(f"OpenWeather вернул ошибку {response.status_code}.")


def _parse_json(response: requests.Response):
    try:
        return response.json()
    except ValueError:
        raise WeatherError("Сервер прислал ответ не в формате JSON.") from None


def get_coordinates(city: str) -> tuple[float, float]:
    """Координаты города через Geocoding API (limit=1, lang=ru)."""
    city = city.strip()
    if not city:
        raise WeatherError("Название города пустое.")
    response = _request(GEO_URL, {"q": city, "limit": 1, "lang": "ru"})
    _check_status(response)
    data = _parse_json(response)
    if not data:
        raise WeatherError(f"Город «{city}» не найден. Проверьте написание.")
    return data[0]["lat"], data[0]["lon"]


def get_weather_by_coordinates(lat: float, lon: float) -> dict:
    """Текущая погода по координатам (units=metric, lang=ru)."""
    response = _request(
        WEATHER_URL, {"lat": lat, "lon": lon, "units": "metric", "lang": "ru"}
    )
    _check_status(response)
    return _parse_json(response)


def get_current_weather(city: str = None, latitude: float = None, longitude: float = None) -> dict:
    """Погода по городу (сначала геокодинг) или сразу по координатам."""
    if city:
        latitude, longitude = get_coordinates(city)
    if latitude is None or longitude is None:
        raise WeatherError("Нужно указать город или обе координаты.")
    weather = get_weather_by_coordinates(latitude, longitude)
    save_cache(weather, city, latitude, longitude)
    return weather


def format_weather(weather: dict, city: str = None) -> str:
    """Строка вида «Погода в Москва: 12.4°C, пасмурно»."""
    place = city or weather.get("name") or "указанной точке"
    temp = weather["main"]["temp"]
    description = weather["weather"][0]["description"]
    return f"Погода в {place}: {temp}°C, {description}"


# ---------- кэш ----------

def save_cache(weather: dict, city, lat: float, lon: float) -> None:
    record = {
        "city": city,
        "lat": lat,
        "lon": lon,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "data": weather,
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)


def load_fresh_cache():
    """Кэш, если он есть и моложе 3 часов, иначе None."""
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            record = json.load(f)
        fetched_at = datetime.fromisoformat(record["fetched_at"])
    except (OSError, ValueError, KeyError):
        return None
    if datetime.now() - fetched_at > CACHE_MAX_AGE:
        return None
    return record


def offer_cache() -> None:
    record = load_fresh_cache()
    if record is None:
        print("Свежих данных в кэше нет (кэш хранится 3 часа).")
        return
    age_min = int((datetime.now() - datetime.fromisoformat(record["fetched_at"])).total_seconds() // 60)
    place = record["city"] or f"{record['lat']}, {record['lon']}"
    answer = input(f"Показать последние сохранённые данные ({place}, {age_min} мин назад)? (д/н): ")
    if answer.strip().lower() in ("д", "да", "y", "yes"):
        print(format_weather(record["data"], record["city"]) + f" [из кэша, {record['fetched_at']}]")


# ---------- консоль ----------

def ask_float(prompt: str, low: float, high: float) -> float:
    value = input(prompt).strip().replace(",", ".")
    try:
        number = float(value)
    except ValueError:
        raise WeatherError(f"«{value}» не число.") from None
    if not low <= number <= high:
        raise WeatherError(f"Значение должно быть от {low} до {high}.")
    return number


def run_once(mode: str) -> None:
    try:
        if mode == "1":
            city = input("Город: ").strip()
            weather = get_current_weather(city=city)
            print(format_weather(weather, city))
        else:
            lat = ask_float("Широта (-90..90): ", -90, 90)
            lon = ask_float("Долгота (-180..180): ", -180, 180)
            weather = get_current_weather(latitude=lat, longitude=lon)
            print(format_weather(weather))
    except NetworkError as e:
        print(f"Ошибка: {e}")
        offer_cache()
    except WeatherError as e:
        print(f"Ошибка: {e}")


def main() -> None:
    print("Погода OpenWeather")
    while True:
        print("\n1 - по городу, 2 - по координатам, 0 - выход")
        mode = input("Выбор: ").strip()
        if mode == "0":
            print("До свидания!")
            break
        if mode in ("1", "2"):
            run_once(mode)
        else:
            print("Введите 1, 2 или 0.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nВыход.")
