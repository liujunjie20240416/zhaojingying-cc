from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("web", "0010_memory")]

    operations = [
        migrations.AddField(model_name="semanticmemory", name="source", field=models.CharField(
            choices=[("ai", "AI 自动整理"), ("user", "用户手动维护")], default="ai", max_length=10,
        )),
        migrations.AddField(model_name="semanticmemory", name="is_locked", field=models.BooleanField(default=False)),
    ]
