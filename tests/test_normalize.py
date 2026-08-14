from ksa.dedup import normalize as n


class TestPhones:
    def test_saudi_local_forms_collapse_to_one_number(self):
        forms = ["0512345678", "+966512345678", "966512345678", "00966512345678",
                 "512345678", "05 12 34 56 78", "+966 51-234-5678"]
        assert {n.normalize_phone(f) for f in forms} == {"+966512345678"}

    def test_extracts_every_written_form_from_running_text(self):
        """Одна и та же запись в трёх начертаниях — один номер."""
        for text in ("Звонить 0512345678", "тел +966 51-234-5678",
                     "Контакт: 966512345678"):
            assert n.extract_phones(text) == ["+966512345678"], text

    def test_arabic_digits(self):
        assert n.normalize_phone("٠٥١٢٣٤٥٦٧٨") == "+966512345678"

    def test_rejects_non_phones(self):
        assert n.normalize_phone("2500") is None
        assert n.normalize_phone("комнат: 3") is None

    def test_extracts_from_ad_text(self):
        text = "Сдаётся квартира в Джидде. Звонить: 0512345678 или +7 999 123-45-67"
        assert n.extract_phones(text) == ["+966512345678", "+79991234567"]


class TestUsernames:
    def test_at_and_link_forms(self):
        text = "Писать @ahmed_rent или https://t.me/ahmed_rent, канал t.me/ksa_arenda"
        assert n.extract_usernames(text) == ["ahmed_rent", "ksa_arenda"]


class TestNormalizeText:
    def test_repost_noise_does_not_change_the_text(self):
        first = (
            "🔥СДАЁТСЯ КВАРТИРА в Медине!!! 2 комнаты, 2500 риалов/мес.\n"
            "Телефон: 0512345678\n#аренда #медина\n"
            "Подписывайтесь на наш канал @ksa_arenda"
        )
        second = (
            "Сдается квартира в Медине. 2 комнаты, 2500 риалов/мес 🏠🏠\n"
            "тел +966512345678\nhttps://t.me/ksa_arenda\n"
            "Реклама у нас: @admin"
        )
        assert n.normalize_text(first) == n.normalize_text(second)

    def test_digits_survive(self):
        assert "2500" in n.normalize_text("Цена 2500 риалов")


class TestPrices:
    def test_currency_forms(self):
        assert n.parse_price("аренда 2 500 SAR в месяц") == (2500.0, "SAR")
        assert n.parse_price("цена 3000 риалов") == (3000.0, "SAR")
        assert n.parse_price("стоит 1,200 ريال") == (1200.0, "SAR")

    def test_no_currency_means_no_guess(self):
        assert n.parse_price("2 комнаты, 3 этаж") is None

    def test_bucket_tolerates_small_edits_but_not_big_ones(self):
        assert n.price_bucket(2500) == n.price_bucket(2600)
        assert n.price_bucket(2500) != n.price_bucket(9000)
        assert n.price_bucket(None) == "na"
