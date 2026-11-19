from django.core.management.base import BaseCommand

from monitor.services import proxy_config


class Command(BaseCommand):
    help = "Write the proxy SearXNG instance's settings.yml from the proxy settings saved in the UI."

    def handle(self, *args, **options):
        changed = proxy_config.apply()
        self.stdout.write("Proxy config " + ("updated." if changed else "already up to date."))
