"""Keyword search: multi-word matching, the cross-search Results page, the dashboard filter and the API."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token

from monitor.models import SavedSearch, SearchResult, Tag
from monitor.queries import filter_results, search_terms
from monitor.services.searxng import url_hash


def add(search, url, title="", content=""):
    return SearchResult.objects.create(search=search, url=url, url_hash=url_hash(url), title=title, content=content)


class SearchTermTests(TestCase):
    def test_splitting(self):
        cases = {
            "acme": ["acme"],
            "  acme   breach ": ["acme", "breach"],
            'acme "data breach"': ["acme", "data breach"],
            "o'brien": ["o'brien"],
            '"unclosed acme': ["unclosed", "acme"],
            '""': [],
            "": [],
            None: [],
        }
        for q, expected in cases.items():
            self.assertEqual(search_terms(q), expected, q)
        self.assertEqual(len(search_terms(" ".join(f"w{i}" for i in range(50)))), 10)  # capped


class MatchingTests(TestCase):
    def setUp(self):
        s = SavedSearch.objects.create(name="Acme", query="acme")
        self.a = add(s, "https://news.example/acme-hack", "Acme Corp hit by data breach", "Attackers stole records")
        self.b = add(s, "https://blog.example/breach-data", "Breach roundup", "acme mentioned in passing")
        self.c = add(s, "https://acme.example/careers", "Careers", "Join the team")

    def match(self, q):
        return set(filter_results({"q": q}, SearchResult.objects.all()))

    def test_single_word_unchanged(self):
        self.assertEqual(self.match("acme"), {self.a, self.b, self.c})  # title, content or url
        self.assertEqual(self.match("ACME"), {self.a, self.b, self.c})  # case-insensitive

    def test_every_word_must_match_somewhere(self):
        self.assertEqual(self.match("acme breach"), {self.a, self.b})  # words can be in different fields
        self.assertEqual(self.match("acme careers"), {self.c})
        self.assertEqual(self.match("acme nothinghere"), set())

    def test_quoted_phrase(self):
        self.assertEqual(self.match('"data breach"'), {self.a})  # b has "breach-data", not the phrase
        self.assertEqual(self.match('acme "data breach"'), {self.a})


class ResultsPageTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="pw-123456"))
        self.s1 = SavedSearch.objects.create(name="Acme watch", query="acme")
        self.s2 = SavedSearch.objects.create(name="Globex watch", query="globex")
        self.r1 = add(self.s1, "https://a.example/1", "Acme leak on forum")
        self.r2 = add(self.s2, "https://b.example/2", "Globex leak report")
        self.r3 = add(self.s2, "https://b.example/3", "Unrelated")

    def get(self, **params):
        return self.client.get(reverse("results"), params)

    def test_searches_across_all_saved_searches(self):
        resp = self.get(q="leak")
        self.assertEqual(set(resp.context["page"].object_list), {self.r1, self.r2})
        self.assertContains(resp, "Acme watch")  # each card names its saved search
        self.assertContains(resp, "Globex watch")
        self.assertNotContains(resp, 'name="ids"')  # no bulk checkboxes on this page

    def test_limit_to_one_search(self):
        resp = self.get(q="leak", search=self.s2.pk)
        self.assertEqual(list(resp.context["page"].object_list), [self.r2])

    def test_tabs_and_counts(self):
        self.r2.soft_delete()
        self.r1.tags.add(Tag.get_or_create_named("interesting"))
        resp = self.get(q="leak")
        self.assertEqual(resp.context["counts"], {"all": 1, "new": 1, "tagged": 1, "deleted": 1})
        self.assertEqual(list(self.get(q="leak", view="deleted").context["page"].object_list), [self.r2])

    def test_empty_states_and_nav(self):
        self.assertContains(self.get(q="nothingmatches"), "No results match")
        self.assertContains(self.get(), 'href="/results/"')  # nav link

    def test_pagination(self):
        for i in range(55):
            add(self.s1, f"https://p.example/{i}", f"Page item {i}")
        self.assertEqual(len(self.get(q="item").context["page"].object_list), 50)
        self.assertEqual(len(self.get(q="item", page=2).context["page"].object_list), 5)

    def test_card_action_keeps_results_page_layout(self):
        resp = self.client.post(reverse("result-action", args=[self.r1.pk, "star"]), {"view": "", "show_search": "1"})
        self.assertContains(resp, "Acme watch")
        self.assertNotContains(resp, 'name="ids"')
        resp = self.client.post(reverse("result-action", args=[self.r1.pk, "star"]), {"view": ""})
        self.assertContains(resp, 'name="ids"')  # per-search page unchanged

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.get(q="leak").status_code, 302)


class DashboardFilterTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="pw-123456"))
        SavedSearch.objects.create(name="Acme watch", query="acme corp")
        SavedSearch.objects.create(name="Globex", query='"globex" breach')

    def names(self, resp):
        return [s.name for s in resp.context["searches"]]

    def test_filter_by_name_or_query(self):
        self.assertEqual(self.names(self.client.get(reverse("dashboard-table"), {"q": "acme"})), ["Acme watch"])
        self.assertEqual(self.names(self.client.get(reverse("dashboard-table"), {"q": "breach"})), ["Globex"])
        self.assertEqual(len(self.names(self.client.get(reverse("dashboard-table")))), 2)

    def test_dashboard_page_and_no_match(self):
        self.assertEqual(self.names(self.client.get(reverse("dashboard"), {"q": "globex"})), ["Globex"])
        resp = self.client.get(reverse("dashboard-table"), {"q": "zzz"})
        self.assertContains(resp, "No saved searches match")
        self.assertContains(resp, "/results/?q=zzz")


class ApiKeywordTests(TestCase):
    def test_api_q_uses_word_matching(self):
        user = get_user_model().objects.create_user("api", password="pw-123456")
        s = SavedSearch.objects.create(name="Acme", query="acme")
        hit = add(s, "https://x.example/1", "Acme data breach")
        add(s, "https://x.example/2", "Acme careers")
        resp = self.client.get("/api/v1/results/", {"q": "acme breach"},
                               HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        rows = data["results"] if isinstance(data, dict) else data
        self.assertEqual([r["id"] for r in rows], [hit.pk])
