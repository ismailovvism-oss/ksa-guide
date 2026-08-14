"""Сценарии, ради которых всё и затевалось.

Каждый тест — реальная ситуация из тг-каналов: одно и то же объявление
кочует по каналам неделями, а рядом лежат похожие, но разные квартиры
одного и того же агента.
"""

from ksa.dedup import Item, compare, identity_key
from ksa.dedup.normalize import extract_contacts, normalize_text

REPOST_A = """🏠 Сдаётся 2-комнатная квартира в Медине, район Аль-Азизия.
2500 риалов в месяц. Кондиционер, кухня, рядом Харам — 10 минут.
Телефон: 0512345678"""

REPOST_B = """Сдается 2х комнатная квартира в Медине, р-н Аль-Азизия 🔥
2500 SAR/месяц. Есть кондиционер и кухня, до Харама 10 минут пешком.
тел +966512345678
Подписывайтесь: @ksa_arenda"""

OTHER_FLAT_SAME_AGENT = """Сдаётся студия в Медине, район Кубa.
900 риалов в месяц, без мебели.
Телефон: 0512345678"""


def item(text: str, **overrides) -> Item:
    base = dict(
        norm_text=normalize_text(text),
        contacts=extract_contacts(text),
        category="rent",
        city="Медина",
    )
    return Item(**{**base, **overrides})


class TestSameAdReposted:
    def test_merges_across_channels(self):
        result = compare(item(REPOST_A, price_amount=2500, rooms=2),
                         item(REPOST_B, price_amount=2500, rooms=2))
        assert result.merge, result.reasons

    def test_rewritten_text_with_the_same_photo_goes_to_a_moderator(self):
        """Фото и цена сошлись, текст переписан целиком.

        Это либо перепост, либо вторая квартира в том же доме с общей
        фотографией фасада. Автоматике такое решать нельзя — в серую зону.
        """
        left = item("Квартира в Медине, 2500 риалов", price_amount=2500,
                    media_phash="ffee1122aabb3344")
        right = item("Сдаю жильё рядом с Харамом, 2500 SAR", price_amount=2500,
                     media_phash="ffee1122aabb3345")
        result = compare(left, right)
        assert result.needs_review, result.reasons
        assert not result.merge

    def test_same_photo_and_similar_text_merges_outright(self):
        left = item(REPOST_A, price_amount=2500, media_phash="ffee1122aabb3344")
        right = item(REPOST_B, price_amount=2500, media_phash="ffee1122aabb3344")
        assert compare(left, right).merge


class TestDifferentAds:
    def test_same_agent_different_flats_stay_separate(self):
        """Самый опасный ложный склей: один телефон, но разные квартиры."""
        result = compare(item(REPOST_A, price_amount=2500, rooms=2),
                         item(OTHER_FLAT_SAME_AGENT, price_amount=900, rooms=1))
        assert not result.merge, result.reasons

    def test_different_categories_never_merge(self):
        result = compare(item(REPOST_A, category="rent"), item(REPOST_A, category="jobs"))
        assert result.score == 0.0

    def test_channel_wide_contact_is_weak_evidence(self):
        """Телефон канала во всех постах не должен склеивать всё подряд."""
        left = item("Ищу работу водителем в Джидде", category="jobs")
        right = item("Продам холодильник, Джидда", category="jobs")
        common = {"phone:+966512345678"}
        for candidate in (left, right):
            candidate.contacts = [{"type": "phone", "value": "+966512345678"}]
        assert not compare(left, right, common_contacts=common).merge


class TestIdentityKey:
    def test_same_ad_gets_same_key(self):
        assert identity_key(item(REPOST_A, price_amount=2500)) == \
               identity_key(item(REPOST_B, price_amount=2500))

    def test_no_contact_means_no_key(self):
        assert identity_key(item("Сдаётся квартира без контактов")) is None
