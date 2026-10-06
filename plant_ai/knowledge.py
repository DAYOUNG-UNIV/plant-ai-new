"""Small, offline lexical RAG index for the project's user-authored basil guide.

Retrieval combines BM25, Korean character n-grams and explicit topic synonyms.
It does not use semantic embeddings, an LLM, an API key, or a network service.
Guide contents are preserved as evidence, not asserted to be validated advice.
"""

from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
import re
import unicodedata


MAX_QUERY_CHARS = 500

# Topic detection is intentionally conservative: an unrelated question must not
# acquire an apparently relevant citation through incidental character overlap.
_PATTERNS = {
    "identity": r"학명|원산지|어디서\s*(왔|유래)|무슨\s*(종류|식물)|이름|분류|basilicum|\b(origin|species|name)\b",
    "temperature": r"온도|기온|몇\s*도|더워|더운|덥|뜨거|추워|추운|춥|차가|저온|고온|서리|얼어|따뜻|\b(temperature|cold|hot|frost|heat)\b",
    "humidity": r"습도|습한|습해|공기\s*(수분|건조)|\b(humidity|humid)\b",
    "light": r"햇빛|햇볕|일조|광량|직사광|빛|그늘|조명|\b(light|sunlight|sun|shade)\b",
    "soil": r"토양|흙|배양토|펄라이트|버미큘라이트|코코피트|양토|산성|배수|\b(soil|ph|potting|perlite)\b",
    "watering": r"물\s*(주|줘|줄|줬|주어|주면)|물을|물이|물은|물의|물\s*양|물\s*부족|급수|관수|목말|수분|과습|말랐|마르|말라|촉촉|\b(water|watering|moisture|dry|thirsty|overwater\w*)\b",
    "cultivation": r"키우|기르|재배|생장|성장|크기|키가|높이|\b(grow|growing|cultivation|height)\b",
    "seeding": r"파종|씨앗|발아|심어|심는|심을|\b(seed|seeding|germination)\b",
    "pruning": r"수확|가지치기|순치기|순지르|생장점|잘라|따도|따는|\b(prune|pruning|harvest)\b",
    "care": r"관리|주의|조심|통풍|환기|바람|\b(care|ventilation|airflow)\b",
    "pests": r"벌레|해충|진딧물|응애|곤충|병충해|병해충|\b(bug|bugs|pest|pests|aphid|mites)\b",
    "benefits": r"장점|좋은\s*점|초보|쉬워|쉬운|공간\s*정화|\b(benefit|benefits|beginner)\b",
    "hydroponics": r"수경|물\s*교체|물갈이|\b(hydroponics|hydroponic)\b",
    "soil_cultivation": r"토양\s*재배|흙\s*재배|\b(soil\s+cultivation)\b",
    "fertilizer": r"비료|양분|영양제|\b(fertilizer|fertiliser|nutrient)\b",
    "leaf_curl": r"잎.{0,8}(말림|말리|말려|말렸|오그라|돌돌)|\b(leaf\s*curl|curling|curled)\b",
    "drooping": r"축\s*처|처지|처져|늘어지|시들|투명|\b(droop\w*|wilt\w*)\b",
    "mold": r"곰팡이|하얀\s*가루|흰\s*가루|흰가루|\b(mold|mould|mildew)\b",
    "growth_problem": r"성장\s*저하|안\s*자라|못\s*자라|성장.{0,6}(느|멈)|자라.{0,6}느|\b(stunted|slow\s+growth)\b",
    "uses": r"요리|용도|향기|냄새|허브\s*향|페스토|파스타|피자|샐러드|먹어|먹는|먹을|\b(use|uses|cooking|pesto|aroma)\b",
    "healthy_leaf": r"잎.{0,7}(특징|색|윤기|진초록|건강)|건강.{0,5}잎|\b(healthy\s+leav\w*|leaf\s+color)\b",
}
_TOPICS = {key: re.compile(pattern) for key, pattern in _PATTERNS.items()}
_SOIL_MOISTURE = re.compile(r"토양\s*(수분|습도)|흙.{0,3}(수분|습도)|soil\s+(moisture|humidity)")
_AIR_HUMIDITY = re.compile(r"(공기|대기|상대|실내)\s*습도|air\s+humidity")

_SECTIONS = {
    ("optimal_conditions", "temperature"): ("적정 온도와 저온 주의", {"temperature"}),
    ("optimal_conditions", "humidity"): ("공기 습도와 통풍", {"humidity"}),
    ("optimal_conditions", "sunlight"): ("햇빛과 일조량", {"light"}),
    ("optimal_conditions", "soil"): ("토양 종류, 배수, 산도와 배합", {"soil"}),
    ("watering",): ("물주기와 토양 수분", {"watering"}),
    ("cultivation",): ("파종, 성장, 수확과 가지치기", {"cultivation", "seeding", "pruning"}),
    ("care_points", "주의사항"): ("관리 주의사항과 병해충 예방", {"care", "pests"}),
    ("care_points", "장점"): ("바질 재배의 장점", {"benefits"}),
    ("cultivation_methods", "토양재배"): ("토양재배와 비료", {"soil_cultivation", "fertilizer"}),
    ("cultivation_methods", "수경재배"): ("수경재배와 물 교체", {"hydroponics", "fertilizer"}),
    ("troubleshooting", "잎_말림"): ("문제 해결: 잎 말림", {"leaf_curl"}),
    ("troubleshooting", "투명하고_축_처짐"): ("문제 해결: 투명하고 축 처짐", {"drooping"}),
    ("troubleshooting", "하얀가루_곰팡이"): ("문제 해결: 하얀 가루와 곰팡이", {"mold"}),
    ("troubleshooting", "성장_저하"): ("문제 해결: 성장 저하", {"growth_problem"}),
    ("characteristics",): ("바질의 향, 용도와 건강한 잎", {"uses", "healthy_leaf"}),
}

_LABELS = {
    "plant_name": "식물 이름", "scientific_name": "학명", "category": "분류", "origin": "원산지",
    "day": "낮", "night": "밤", "growth_range": "생장 온도 범위", "min": "최소", "max": "최대",
    "unit": "단위", "warning": "주의", "optimal_range": "적정 범위", "daily_requirement": "하루 필요량",
    "type": "종류", "ph": "pH", "description": "설명", "characteristics": "특징", "composition": "배합",
    "frequency": "빈도", "method": "방법", "soil_moisture": "토양 수분", "timing": "시간대",
    "growth_stage": "성장 단계", "seeding_period": "파종 시기", "growth_height": "성장 높이",
    "harvest_tip": "수확 요령", "pruning": "가지치기",
}


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).lower()).strip()


def _tokens(value: str) -> list[str]:
    terms: list[str] = []
    for word in re.findall(r"[가-힣]+|[a-z]+", _normalize(value)):
        terms.append("w:" + word)
        if re.fullmatch(r"[가-힣]+", word):
            terms.extend("g:" + word[i:i + size] for size in (2, 3) for i in range(len(word) - size + 1))
    return terms


def _concepts(query: str) -> set[str]:
    found = {key for key, pattern in _TOPICS.items() if pattern.search(query)}
    if _SOIL_MOISTURE.search(query):
        found.add("watering")
        if not _AIR_HUMIDITY.search(query):
            found.discard("humidity")
        # Soil moisture is not soil composition or atmospheric humidity.
        if not re.search(r"종류|배합|배수|산도|배양토|\bph\b", query):
            found.discard("soil")
    if re.fullmatch(r"(?:바질|basil|sweet basil)(?:이란|에 대해|에 대해서)?\s*(?:알려\s*줘|소개해\s*줘|설명해\s*줘|뭐야)?[?!. ]*", query):
        found.add("identity")
    return found


def _render(value: object, prefix: tuple[str, ...] = ()) -> list[str]:
    """Keep every source leaf, including numeric ranges, without paraphrasing."""
    if isinstance(value, dict):
        lines: list[str] = []
        for key, child in value.items():
            label = _LABELS.get(key, key.replace("_", " "))
            lines.extend(_render(child, prefix + (label,)))
        return lines
    if isinstance(value, list):
        lines = []
        for index, child in enumerate(value, 1):
            lines.extend(_render(child, prefix + (str(index),)))
        return lines
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return [(" / ".join(prefix) + ": " if prefix else "") + text]


class BasilKnowledge:
    """Load the original JSON guide once and retrieve small, traceable passages."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("The basil guide must be a JSON object.")
        self._documents: list[dict] = []
        profile = {key: value for key, value in data.items() if not isinstance(value, (dict, list))}
        if profile:
            self._add_document((), "바질 기본 정보", profile, {"identity"})
        for key, value in data.items():
            if not isinstance(value, (dict, list)):
                continue
            if key in {"optimal_conditions", "care_points", "cultivation_methods", "troubleshooting"} and isinstance(value, dict):
                for subkey, child in value.items():
                    self._add_section((key, subkey), child)
            else:
                self._add_section((key,), value)
        self._counts = [Counter(_tokens(doc["title"] + " " + doc["text"])) for doc in self._documents]
        self._lengths = [sum(counts.values()) for counts in self._counts]
        self._average_length = sum(self._lengths) / len(self._lengths) if self._lengths else 1.0
        self._document_frequency: Counter = Counter()
        for counts in self._counts:
            self._document_frequency.update(counts.keys())

    def _add_section(self, path: tuple[str, ...], value: object) -> None:
        title, topics = _SECTIONS.get(path, (" / ".join(path).replace("_", " "), set()))
        self._add_document(path, title, value, topics)

    def _add_document(self, path: tuple[str, ...], title: str, value: object, topics: set[str]) -> None:
        # JSON Pointer escaping keeps original Korean keys and exact locations.
        pointer = "".join("/" + part.replace("~", "~0").replace("/", "~1") for part in path)
        text = "\n".join(_render(value))
        self._documents.append({
            "id": ".".join(path) if path else "plant_profile",
            "title": title,
            "text": text,
            "source": self.path.name + "#" + pointer,
            "topics": set(topics) or _concepts(_normalize(title + " " + text)),
        })

    def search(self, query: str, top_k: int = 3) -> dict:
        """Return JSON-serializable matches, or an explicit no-evidence status.

        Results cite the supplied project file, never an external publication.
        Very long and non-string queries are rejected; result count is 1–5.
        """
        response = {
            "query": query if isinstance(query, str) and len(query) <= MAX_QUERY_CHARS else "",
            "matches": [],
            "status": "no_match",
            "retrieval_method": "local_lexical_bm25_character_ngrams",
            "source_type": "user_authored_guide",
            "notice": "기존 프로젝트의 사용자 작성 바질 가이드입니다. 외부 논문이나 검증된 농업 자료를 인용한 결과가 아닙니다.",
        }
        if not isinstance(query, str):
            response["status"] = "invalid_query"
            return response
        if len(query) > MAX_QUERY_CHARS:
            response["status"] = "query_too_long"
            return response
        normalized = _normalize(query)
        if not normalized:
            response["status"] = "empty_query"
            return response
        try:
            limit = max(1, min(5, int(top_k)))
        except (TypeError, ValueError, OverflowError):
            limit = 3
        query_topics = _concepts(normalized)
        if not query_topics:
            return response
        terms = set(_tokens(normalized))
        query_ngrams = {term for term in terms if term.startswith("g:")}
        ranked: list[tuple[float, dict]] = []
        for index, document in enumerate(self._documents):
            matched_topics = query_topics & document["topics"]
            if not matched_topics:
                continue
            counts = self._counts[index]
            bm25 = 0.0
            for term in terms:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                df = self._document_frequency[term]
                inverse_frequency = math.log(1 + (len(self._documents) - df + 0.5) / (df + 0.5))
                denominator = frequency + 1.5 * (0.25 + 0.75 * self._lengths[index] / max(self._average_length, 1))
                bm25 += inverse_frequency * frequency * 2.5 / denominator
            document_ngrams = {term for term in counts if term.startswith("g:")}
            overlap = len(query_ngrams & document_ngrams) / math.sqrt(max(1, len(query_ngrams) * len(document_ngrams)))
            # Topic precision prevents long, generic sections winning on length.
            topic_score = len(matched_topics) / math.sqrt(len(document["topics"]))
            score = 3.0 * topic_score + bm25 / (1.0 + bm25) + overlap
            ranked.append((score, document))
        ranked.sort(key=lambda item: (-item[0], item[1]["id"]))
        response["matches"] = [
            {**{key: document[key] for key in ("id", "title", "text", "source")}, "score": round(score, 6)}
            for score, document in ranked[:limit]
        ]
        if response["matches"]:
            response["status"] = "ok"
        return response
