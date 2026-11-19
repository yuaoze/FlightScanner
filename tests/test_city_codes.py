"""city_codes 静态表一致性测试。

国内机场数据以 DOMESTIC_AIRPORT_MAP 为唯一数据源，
这里校验派生关系、代码格式及关键城市映射，防止数据漂移。
"""

from flightscanner.utils.city_codes import (
    ALL_CITIES_LIST,
    CITY_CODE_MAP,
    DOMESTIC_AIRPORT_MAP,
    _DOMESTIC_ALIASES,
    _DOMESTIC_CITIES,
    get_airport_list,
    get_city_code,
    is_international_route,
)


class TestCodeFormat:
    def test_all_codes_are_three_uppercase_letters(self):
        for city, code in CITY_CODE_MAP.items():
            assert len(code) == 3, f"{city} -> {code}"
            assert code.isupper() and code.isalpha(), f"{city} -> {code}"

    def test_airport_codes_are_three_uppercase_letters(self):
        for city, airports in DOMESTIC_AIRPORT_MAP.items():
            assert airports, f"{city} 没有机场"
            for airport in airports:
                code = airport["code"]
                assert len(code) == 3 and code.isupper() and code.isalpha(), (
                    f"{city} -> {code}"
                )
                assert airport["name"], f"{city} {code} 缺少机场名"


class TestTableConsistency:
    def test_every_domestic_city_has_code(self):
        for city in DOMESTIC_AIRPORT_MAP:
            assert get_city_code(city) is not None, city

    def test_domestic_cities_derived_from_airport_map_and_aliases(self):
        assert _DOMESTIC_CITIES == set(DOMESTIC_AIRPORT_MAP) | set(_DOMESTIC_ALIASES)

    def test_aliases_resolve_to_primary_city_code(self):
        for alias, primary in _DOMESTIC_ALIASES.items():
            assert primary in DOMESTIC_AIRPORT_MAP, alias
            assert get_city_code(alias) == get_city_code(primary), alias

    def test_city_code_matches_airport_list(self):
        for city, airports in DOMESTIC_AIRPORT_MAP.items():
            codes = {a["code"] for a in airports}
            city_code = get_city_code(city)
            # 城市级代码（BJS/SHA/CTU）或首个机场代码
            assert city_code in codes or city_code in {"BJS", "SHA", "CTU"}, city

    def test_all_cities_list_has_no_duplicates(self):
        assert len(ALL_CITIES_LIST) == len(set(ALL_CITIES_LIST))

    def test_domestic_cities_appear_in_ui_list(self):
        for city in DOMESTIC_AIRPORT_MAP:
            assert city in ALL_CITIES_LIST, city


class TestKnownMappings:
    def test_metro_city_codes(self):
        assert get_city_code("北京") == "BJS"
        assert get_city_code("上海") == "SHA"
        assert get_city_code("成都") == "CTU"

    def test_small_cities_supported(self):
        assert get_city_code("林芝") == "LZY"
        assert get_city_code("日喀则") == "RKZ"
        assert get_city_code("昌都") == "BPX"
        assert get_city_code("阿里") == "NGQ"
        assert get_city_code("山南") == "LGZ"

    def test_relocated_airports_use_new_codes(self):
        assert get_city_code("达州") == "DZH"  # 金垭机场，原河市 DAX 已关闭
        assert get_city_code("营口") == "YKH"  # 兰旗机场，AOG 是鞍山
        assert get_city_code("加格达奇") == "JGD"  # GMQ 是果洛

    def test_multi_airport_cities(self):
        assert {a["code"] for a in get_airport_list("北京")} == {"PEK", "PKX"}
        assert {a["code"] for a in get_airport_list("上海")} == {"PVG", "SHA"}
        assert {a["code"] for a in get_airport_list("成都")} == {"TFU", "CTU"}

    def test_domestic_route_not_international(self):
        assert not is_international_route("成都", "林芝")
        assert not is_international_route("上海", "喀什")
        assert not is_international_route("大兴安岭", "金华")  # 别名也算国内

    def test_international_route_detected(self):
        assert is_international_route("成都", "东京")
        assert is_international_route("未知城市", "拉萨")