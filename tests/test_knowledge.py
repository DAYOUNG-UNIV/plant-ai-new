"""Behavior checks for offline guide retrieval and honest source attribution."""

import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from plant_ai.knowledge import BasilKnowledge, MAX_QUERY_CHARS


GUIDE = Path(__file__).resolve().parents[1] / "basil_knowledge.json"


class BasilKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.knowledge = BasilKnowledge(GUIDE)

    def first(self, query):
        result = self.knowledge.search(query)
        self.assertEqual(result["status"], "ok", query)
        return result["matches"][0]

    def test_korean_temperature_phrasings(self):
        for query in ("바질 적정 온도가 몇 도야?", "오늘 너무 추워요", "더워도 괜찮아?", "서리가 내리면?"):
            with self.subTest(query=query):
                match = self.first(query)
                self.assertEqual(match["id"], "optimal_conditions.temperature")
                self.assertIn("24", match["text"])
                self.assertIn("29", match["text"])

    def test_watering_and_soil_humidity_are_not_air_humidity(self):
        for query in ("물을 얼마나 줘야 해?", "토양 수분은 몇 퍼센트가 좋아?", "토양습도가 낮아요", "흙 수분이 부족해", "soil moisture"):
            with self.subTest(query=query):
                result = self.knowledge.search(query)
                self.assertEqual(result["matches"][0]["id"], "watering")
                self.assertNotIn("optimal_conditions.humidity", {match["id"] for match in result["matches"]})
                self.assertNotIn("50", result["matches"][0]["text"])

    def test_air_humidity_still_returns_air_humidity(self):
        match = self.first("공기 습도는 어느 정도가 좋아?")
        self.assertEqual(match["id"], "optimal_conditions.humidity")
        self.assertIn("50", match["text"])
        self.assertIn("60", match["text"])

    def test_specific_symptoms_are_independent_passages(self):
        cases = {
            "잎이 돌돌 말려요": "troubleshooting.잎_말림",
            "잎이 투명하고 축 처져요": "troubleshooting.투명하고_축_처짐",
            "하얀 가루가 생겼어": "troubleshooting.하얀가루_곰팡이",
            "바질이 안 자라요": "troubleshooting.성장_저하",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(self.first(query)["id"], expected)

    def test_bugs_return_only_existing_prevention_information(self):
        match = self.first("벌레가 보여요. 해충 예방은 어떻게 해?")
        self.assertEqual(match["id"], "care_points.주의사항")
        self.assertIn("통풍 관리로 병해충 예방", match["text"])
        self.assertNotIn("살충제", match["text"])

    def test_other_guide_sections_can_be_found(self):
        cases = {
            "원산지가 어디야?": "plant_profile",
            "햇빛은 얼마나 필요해?": "optimal_conditions.sunlight",
            "펄라이트 배합을 알려줘": "optimal_conditions.soil",
            "가지치기를 어떻게 해?": "cultivation",
            "장점이 뭐야?": "care_points.장점",
            "수경재배 물 교체는 언제 해?": "cultivation_methods.수경재배",
            "페스토에 써도 돼?": "characteristics",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(self.first(query)["id"], expected)

    def test_unrelated_questions_do_not_produce_random_citations(self):
        for query in ("비트코인 가격 전망", "오늘 축구 경기 결과", "양자 물리학을 설명해줘", "대통령은 누구야?", "hello"):
            with self.subTest(query=query):
                result = self.knowledge.search(query)
                self.assertEqual(result["status"], "no_match")
                self.assertEqual(result["matches"], [])

    def test_sources_are_stable_and_resolve_to_original_json(self):
        result = self.knowledge.search("온도 습도 물주기 햇빛 토양", top_k=5)
        self.assertEqual(result, BasilKnowledge(GUIDE).search("온도 습도 물주기 햇빛 토양", top_k=5))
        data = json.loads(GUIDE.read_text(encoding="utf-8-sig"))
        for match in result["matches"]:
            filename, pointer = match["source"].split("#", 1)
            self.assertEqual(filename, "basil_knowledge.json")
            section = data
            for part in pointer.lstrip("/").split("/") if pointer else []:
                section = section[part.replace("~1", "/").replace("~0", "~")]
            self.assertIsNotNone(section)
        self.assertEqual(result["source_type"], "user_authored_guide")
        self.assertIn("lexical", result["retrieval_method"])
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_input_bounds(self):
        query = "온도 습도 물주기 햇빛 토양 수확 벌레"
        self.assertEqual(len(self.knowledge.search(query, top_k=100)["matches"]), 5)
        self.assertEqual(len(self.knowledge.search(query, top_k=0)["matches"]), 1)
        self.assertEqual(self.knowledge.search("   ")["status"], "empty_query")
        self.assertEqual(self.knowledge.search("물" * (MAX_QUERY_CHARS + 1))["status"], "query_too_long")
        self.assertEqual(self.knowledge.search(None)["status"], "invalid_query")

    def test_retrieval_never_needs_network(self):
        with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")), patch.dict("os.environ", {}, clear=True):
            result = BasilKnowledge(GUIDE).search("흙이 말랐어. 물을 줘야 하나?")
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
