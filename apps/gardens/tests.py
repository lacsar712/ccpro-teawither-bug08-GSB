from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import TroughForm
from .models import Garden, Trough, WitherBatch


def make_garden(name="云雾岭一号园", **kwargs):
    defaults = {"altitudeBand": "800-1000m", "notes": ""}
    defaults.update(kwargs)
    return Garden.objects.create(name=name, **defaults)


def make_trough(garden, code="A-01", **kwargs):
    defaults = {"cultivar": "福鼎大白", "loadKg": Decimal("100.00")}
    defaults.update(kwargs)
    return Trough.objects.create(
        garden=garden, troughCode=code, **defaults
    )


def make_batch(trough, **kwargs):
    defaults = {
        "startedAt": timezone.now(),
        "targetMoisture": Decimal("38.00"),
        "rollGrade": "一级",
    }
    defaults.update(kwargs)
    return WitherBatch.objects.create(trough=trough, **defaults)


class GardenDeleteTests(TestCase):
    """有槽必拒删、拒删后数据完整、无槽才真正删除。"""

    def setUp(self):
        user = get_user_model().objects.create_user("op", password="pw")
        self.client.force_login(user)

    def test_delete_garden_with_troughs_is_blocked_and_data_intact(self):
        garden = make_garden()
        trough = make_trough(garden)
        batch = make_batch(trough)

        resp = self.client.post(
            reverse("garden_delete", args=[garden.pk]), follow=True
        )

        # 茶园、槽、批次全部原样保留,槽的归属不被置空
        self.assertTrue(Garden.objects.filter(pk=garden.pk).exists())
        trough.refresh_from_db()
        self.assertEqual(trough.garden_id, garden.pk)
        self.assertTrue(WitherBatch.objects.filter(pk=batch.pk).exists())

        # 提示拦截而非成功
        texts = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("无法删除" in t for t in texts), texts)
        self.assertFalse(any("已删除" in t for t in texts), texts)

    def test_delete_garden_with_troughs_blocked_via_htmx_too(self):
        garden = make_garden()
        make_trough(garden)
        resp = self.client.post(
            reverse("garden_delete", args=[garden.pk]),
            HTTP_HX_REQUEST="true",
        )
        self.assertRedirects(resp, reverse("garden_list"))
        self.assertTrue(Garden.objects.filter(pk=garden.pk).exists())

    def test_delete_empty_garden_succeeds(self):
        garden = make_garden()
        resp = self.client.post(
            reverse("garden_delete", args=[garden.pk]), follow=True
        )
        self.assertFalse(Garden.objects.filter(pk=garden.pk).exists())
        texts = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("已删除" in t for t in texts), texts)

    def test_model_level_protect_blocks_orphaning(self):
        garden = make_garden()
        make_trough(garden)
        with self.assertRaises(ProtectedError):
            garden.delete()
        # 槽的归属不被 SET_NULL
        self.assertEqual(Trough.objects.get().garden_id, garden.pk)

    def test_confirm_page_blocks_when_troughs_exist(self):
        garden = make_garden()
        make_trough(garden)
        make_trough(garden, code="A-02")
        resp = self.client.get(reverse("garden_delete", args=[garden.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "不能删除")
        self.assertContains(resp, "2")
        self.assertContains(resp, "disabled")

    def test_confirm_page_allows_when_empty(self):
        garden = make_garden()
        resp = self.client.get(reverse("garden_delete", args=[garden.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "没有关联槽位")
        self.assertNotContains(resp, "disabled")


class DirtyDataCompatTests(TestCase):
    """历史脏数据(空茶园槽)下,各列表与表单页都可正常打开。"""

    def setUp(self):
        user = get_user_model().objects.create_user("op", password="pw")
        self.client.force_login(user)
        self.garden = make_garden()
        # 模拟 SET_NULL 时代遗留的孤儿槽及其批次
        self.orphan = make_trough(None, code="X-09")
        self.orphan_batch = make_batch(self.orphan)
        self.normal = make_trough(self.garden, code="A-01")
        make_batch(self.normal)

    def test_trough_str_is_null_safe(self):
        self.assertIn("X-09", str(self.orphan))
        self.assertIn("未关联茶园", str(self.orphan))

    def test_trough_list_renders(self):
        for headers in ({}, {"HTTP_HX_REQUEST": "true"}):
            resp = self.client.get(reverse("trough_list"), **headers)
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "X-09")
            self.assertContains(resp, "未关联茶园")
            self.assertContains(resp, "云雾岭一号园")

    def test_batch_list_renders(self):
        for headers in ({}, {"HTTP_HX_REQUEST": "true"}):
            resp = self.client.get(reverse("batch_list"), **headers)
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "X-09")

    def test_garden_list_renders(self):
        resp = self.client.get(reverse("garden_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "云雾岭一号园")

    def test_home_renders(self):
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)

    def test_batch_create_form_renders_trough_choices(self):
        # 批次表单的下拉框会对每个槽调用 __str__,孤儿槽不得使其崩溃
        resp = self.client.get(reverse("batch_create"))
        self.assertEqual(resp.status_code, 200)

    def test_trough_delete_confirm_renders_for_orphan(self):
        resp = self.client.get(
            reverse("trough_delete", args=[self.orphan.pk])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "X-09")

    def test_orphan_trough_can_be_deleted(self):
        self.client.post(reverse("trough_delete", args=[self.orphan.pk]))
        self.assertFalse(Trough.objects.filter(pk=self.orphan.pk).exists())
        self.assertFalse(
            WitherBatch.objects.filter(pk=self.orphan_batch.pk).exists()
        )


class ReconciliationTests(TestCase):
    """首页茶园数与茶园列表行数始终一致。"""

    def setUp(self):
        user = get_user_model().objects.create_user("op", password="pw")
        self.client.force_login(user)

    def test_home_count_matches_list_rows(self):
        make_garden("甲园")
        make_garden("乙园")
        make_garden("丙园")
        make_trough(Garden.objects.get(name="甲园"))

        home = self.client.get(reverse("home"))
        self.assertEqual(home.context["garden_count"], Garden.objects.count())

        listing = self.client.get(reverse("garden_list"))
        rows = len(listing.context["gardens"])
        self.assertEqual(rows, Garden.objects.count())
        self.assertEqual(home.context["garden_count"], rows)

        # 删除空园后两边同步减少
        self.client.post(
            reverse("garden_delete", args=[Garden.objects.get(name="丙园").pk])
        )
        home = self.client.get(reverse("home"))
        listing = self.client.get(reverse("garden_list"))
        self.assertEqual(home.context["garden_count"], 2)
        self.assertEqual(len(listing.context["gardens"]), 2)


class TroughFormTests(TestCase):
    """新槽位必须归属茶园,不再产生新的孤儿槽。"""

    def test_garden_is_required(self):
        form = TroughForm(
            data={
                "garden": "",
                "troughCode": "A-01",
                "cultivar": "福鼎大白",
                "loadKg": "100.00",
                "status": "loading",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("garden", form.errors)

    def test_valid_with_garden(self):
        garden = make_garden()
        form = TroughForm(
            data={
                "garden": str(garden.pk),
                "troughCode": "A-01",
                "cultivar": "福鼎大白",
                "loadKg": "100.00",
                "status": "loading",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
