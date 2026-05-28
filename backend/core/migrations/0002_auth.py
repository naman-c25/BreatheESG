import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="password_hash",
            field=models.CharField(max_length=200, blank=True),
        ),
        migrations.CreateModel(
            name="Session",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("token", models.CharField(max_length=64, unique=True, db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sessions", to="core.user")),
            ],
        ),
    ]
