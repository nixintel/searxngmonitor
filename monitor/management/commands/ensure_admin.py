import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token


class Command(BaseCommand):
    help = "Create or update the login user from ADMIN_USER / ADMIN_PASSWORD."

    def handle(self, *args, **options):
        username = os.environ.get("ADMIN_USER")
        password = os.environ.get("ADMIN_PASSWORD")
        if not username or not password:
            self.stdout.write("ADMIN_USER/ADMIN_PASSWORD not set; skipping.")
            return
        if password == "change-me":
            self.stderr.write(self.style.WARNING("ADMIN_PASSWORD is still the default 'change-me'!"))
        User = get_user_model()
        user, created = User.objects.get_or_create(username=username)
        user.is_staff = user.is_superuser = True
        user.email = os.environ.get("ADMIN_EMAIL", user.email)
        user.set_password(password)
        user.save()
        Token.objects.get_or_create(user=user)
        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} user '{username}'."))
