from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("web", "0014_alter_episodicmemory_id_alter_importanalysis_id_and_more")]

    operations = [
        migrations.AddField(
            model_name="semanticmemory",
            name="memory_state",
            field=models.CharField(
                choices=[
                    ("current", "当前有效"),
                    ("historical", "历史状态"),
                    ("superseded", "已替代"),
                ],
                default="current",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="semanticmemory",
            name="valid_from",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="semanticmemory",
            name="valid_to",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="semanticmemory",
            index=models.Index(
                fields=["friend", "memory_state", "is_active"],
                name="sem_friend_state_active",
            ),
        ),
    ]
