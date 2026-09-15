from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("web", "0021_character_imported_memory_visibility")]

    operations = [
        migrations.AddField(
            model_name="friend",
            name="conversation_summary",
            field=models.TextField(blank=True, default="", max_length=2000),
        ),
        migrations.AddField(
            model_name="friend",
            name="summary_through_message_id",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="friend",
            name="summary_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
