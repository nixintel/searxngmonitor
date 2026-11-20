from django.core.management.base import BaseCommand

from monitor.services import tor_config


class Command(BaseCommand):
    help = "Write the tor service's torrc from the Tor setting saved in the UI (off by default)."

    def handle(self, *args, **options):
        changed = tor_config.apply()
        self.stdout.write("Tor config " + ("updated." if changed else "already up to date."))
