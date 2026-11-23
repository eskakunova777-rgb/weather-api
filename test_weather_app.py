"""Проверка без сети и без настоящего ключа: ответы OpenWeather подменяются."""

import os
import tempfile
import unittest
from unittest import mock

import requests

import weather_app


def fake_response(status, payload):
    resp = mock.Mock(status_code=status)
    resp.json.return_value = payload
    return resp


WEATHER = {"name": "Moscow", "main": {"temp": 12.4}, "weather": [{"description": "пасмурно"}]}


class WeatherAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        os.chdir(self.tmp.name)
        patcher = mock.patch.object(weather_app, "API_KEY", "test-key")
        patcher.start()
        self.addCleanup(patcher.stop)
        sleep = mock.patch.object(weather_app.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    @mock.patch("weather_app.requests.get")
    def test_success_and_cache(self, get):
        get.side_effect = [fake_response(200, [{"lat": 55.75, "lon": 37.61}]), fake_response(200, WEATHER)]
        weather = weather_app.get_current_weather(city="Москва")
        self.assertEqual(weather_app.format_weather(weather, "Москва"), "Погода в Москва: 12.4°C, пасмурно")
        geo_params = get.call_args_list[0].kwargs["params"]
        self.assertEqual((geo_params["limit"], geo_params["lang"]), (1, "ru"))
        w_params = get.call_args_list[1].kwargs["params"]
        self.assertEqual((w_params["units"], w_params["lang"]), ("metric", "ru"))
        cache = weather_app.load_fresh_cache()
        self.assertEqual((cache["city"], cache["lat"], cache["lon"]), ("Москва", 55.75, 37.61))
        self.assertIn("fetched_at", cache)

    @mock.patch("weather_app.requests.get")
    def test_unknown_city(self, get):
        get.return_value = fake_response(200, [])
        with self.assertRaisesRegex(weather_app.WeatherError, "не найден"):
            weather_app.get_coordinates("Абвгдейка")

    @mock.patch("weather_app.requests.get")
    def test_bad_key(self, get):
        get.return_value = fake_response(401, {"cod": 401})
        with self.assertRaisesRegex(weather_app.WeatherError, "401"):
            weather_app.get_coordinates("Москва")

    @mock.patch("weather_app.requests.get")
    def test_retry_429_then_ok(self, get):
        get.side_effect = [fake_response(429, {}), fake_response(429, {}), fake_response(200, WEATHER)]
        weather_app.get_weather_by_coordinates(55.75, 37.61)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [1, 2])

    @mock.patch("weather_app.requests.get", side_effect=requests.ConnectionError("no net"))
    def test_network_error_after_retries(self, get):
        with self.assertRaises(weather_app.NetworkError):
            weather_app.get_weather_by_coordinates(55.75, 37.61)
        self.assertEqual(get.call_count, 4)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [1, 2, 4])

    def test_no_key(self):
        with mock.patch.object(weather_app, "API_KEY", None):
            with self.assertRaisesRegex(weather_app.WeatherError, ".env"):
                weather_app.get_coordinates("Москва")


if __name__ == "__main__":
    unittest.main(verbosity=2)
