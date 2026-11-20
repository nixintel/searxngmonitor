from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("monitor", "0004_appsettings_proxy_enabled_appsettings_proxy_host_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="appsettings",
            name="tor_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Connect to the Tor network so searches can use ahmia and torch (.onion sites).",
            ),
        ),
    ]
