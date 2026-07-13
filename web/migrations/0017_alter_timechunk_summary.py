from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("web", "0016_character_style_profile_friend_last_reflected_chat_day")]

    operations = [
        migrations.AlterField(
            model_name="timechunk",
            name="summary",
            field=models.TextField(default=""),
        ),
    ]
