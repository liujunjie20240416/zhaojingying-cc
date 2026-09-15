from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("web", "0026_semanticmemory_trajectory_and_conversationcollapse")]

    operations = [
        migrations.AddField(
            model_name="message",
            name="reply_provenance",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
