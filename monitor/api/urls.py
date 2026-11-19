from django.urls import path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from monitor.api import views

router = DefaultRouter()
router.register("searches", views.SavedSearchViewSet, basename="api-search")
router.register("results", views.SearchResultViewSet, basename="api-result")
router.register("runs", views.SearchRunViewSet, basename="api-run")
router.register("logs", views.LogEntryViewSet, basename="api-log")
router.register("tags", views.TagViewSet, basename="api-tag")

urlpatterns = [
    path("auth/token/", obtain_auth_token, name="api-token"),
    path("health/", views.health_view, name="api-health"),
    path("engines/", views.engines_view, name="api-engines"),
    path("stats/", views.stats_view, name="api-stats"),
] + router.urls
