from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("web", "0027_message_reply_provenance")]

    operations = [
        migrations.DeleteModel(
            name="EpisodicMemory",
        ),
    ]
