from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Garden, Trough, WitherBatch


def make_garden(name="云雾岭一号园", altitude="800-1000m"):
    return Garden.objects.create(name=name, altitudeBand=altitude)


def make_trough(garden, code="A-01", status=Trough.STATUS_LOADING):
    return Trough.objects.create(
        garden=garden,
        troughCode=code,
        cultivar="福鼎大白",
        loadKg=Decimal("100.00"),
        status=status,
    )


class GardenDeleteTests(TestCase):
    """有槽必拒删、拒删后数据完整、无槽才真正删除。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "tester", "tester@example.com", "pw"
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_delete_garden_with_troughs_is_refused_and_data_intact(self):
        garden = make_garden()
        trough = make_trough(garden)

        response = self.client.post(
            reverse("garden_delete", args=[garden.pk]), follow=True
        )

        self.assertRedirects(response, reverse("garden_list"))
        # 拒删后数据完整：茶园还在、槽还在、槽的归属没有被置空
        garden.refresh_from_db()
        self.assertEqual(garden.name, "云雾岭一号园")
        trough.refresh_from_db()
        self.assertEqual(trough.garden_id, garden.pk)
        # 只报错误，没有「已删除」的假成功提示
        msgs = [m.message for m in response.context["messages"]]
        self.assertTrue(any("无法删除" in m for m in msgs), msgs)
        self.assertFalse(any("已删除" in m for m in msgs), msgs)

    def test_delete_empty_garden_really_deletes(self):
        garden = make_garden(name="空茶园")

        response = self.client.post(
            reverse("garden_delete", args=[garden.pk]), follow=True
        )

        self.assertRedirects(response, reverse("garden_list"))
        self.assertFalse(Garden.objects.filter(pk=garden.pk).exists())
        msgs = [m.message for m in response.context["messages"]]
        self.assertTrue(any("已删除" in m for m in msgs), msgs)

    def test_delete_is_enforced_at_orm_level_not_just_ui(self):
        """只加界面提示不算完成：直接 ORM 删除也必须被 PROTECT 拦住。"""
        garden = make_garden()
        trough = make_trough(garden)

        with self.assertRaises(ProtectedError):
            garden.delete()

        self.assertTrue(Garden.objects.filter(pk=garden.pk).exists())
        trough.refresh_from_db()
        self.assertEqual(trough.garden_id, garden.pk)

    def test_confirm_page_warns_and_offers_no_submit_when_troughs_exist(self):
        garden = make_garden()
        make_trough(garden)

        response = self.client.get(reverse("garden_delete", args=[garden.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "不能删除")
        self.assertNotContains(response, "确认删除")

    def test_confirm_page_allows_deleting_empty_garden(self):
        garden = make_garden(name="空茶园")

        response = self.client.get(reverse("garden_delete", args=[garden.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "确认删除")


class LegacyOrphanTroughTests(TestCase):
    """历史脏数据（garden 已被置空的槽位）：各列表与表单页必须能正常打开。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "tester2", "tester2@example.com", "pw"
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.orphan = make_trough(None, code="X-09")
        self.batch = WitherBatch.objects.create(
            trough=self.orphan,
            startedAt=timezone.now(),
            targetMoisture=Decimal("38.00"),
            actualMoisture=None,
            rollGrade="待评",
        )

    def test_trough_str_tolerates_missing_garden(self):
        self.assertIn("X-09", str(self.orphan))

    def test_trough_list_renders_with_orphan(self):
        response = self.client.get(reverse("trough_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "X-09")
        self.assertContains(response, "（茶园已删除）")

    def test_trough_list_htmx_refresh_renders_with_orphan(self):
        response = self.client.get(
            reverse("trough_list"), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "X-09")

    def test_batch_list_renders_with_orphan_trough(self):
        response = self.client.get(reverse("batch_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "X-09")

    def test_batch_create_form_renders_with_orphan_trough(self):
        # 槽位下拉框会渲染每个 Trough 的 __str__
        response = self.client.get(reverse("batch_create"))
        self.assertEqual(response.status_code, 200)

    def test_batch_delete_confirm_renders_with_orphan_trough(self):
        response = self.client.get(reverse("batch_delete", args=[self.batch.pk]))
        self.assertEqual(response.status_code, 200)

    def test_trough_delete_confirm_renders_with_orphan(self):
        response = self.client.get(reverse("trough_delete", args=[self.orphan.pk]))
        self.assertEqual(response.status_code, 200)


class CountReconciliationTests(TestCase):
    """首页茶园数与列表行数始终可对账。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "tester3", "tester3@example.com", "pw"
        )

    def setUp(self):
        self.client.force_login(self.user)

    def assert_counts_reconcile(self):
        home = self.client.get(reverse("home"))
        self.assertEqual(home.context["garden_count"], Garden.objects.count())
        listing = self.client.get(reverse("garden_list"))
        self.assertEqual(len(listing.context["gardens"]), Garden.objects.count())

    def test_counts_reconcile_after_refused_and_successful_deletes(self):
        g1 = make_garden(name="甲茶园", altitude="700m")
        g2 = make_garden(name="乙茶园", altitude="900m")
        make_trough(g2)

        # 拒删（有槽）：两边计数不变且一致
        self.client.post(reverse("garden_delete", args=[g2.pk]))
        self.assert_counts_reconcile()

        # 真删（无槽）：两边计数同步减一
        self.client.post(reverse("garden_delete", args=[g1.pk]))
        self.assert_counts_reconcile()
