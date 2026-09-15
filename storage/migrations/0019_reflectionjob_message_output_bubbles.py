from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("web", "0018_memoryevidence_alter_message_output")]

    operations = [
        migrations.AddField(
            model_name="message",
            name="output_bubbles",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name="ReflectionJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("chat_day", models.DateField()),
                ("status", models.CharField(choices=[
                    ("pending", "等待处理"),
                    ("running", "处理中"),
                    ("done", "已完成"),
                    ("failed", "处理失败"),
                ], default="pending", max_length=20)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("locked_at", models.DateTimeField(blank=True, null=True)),
                ("error_message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("friend", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to="web.friend",
                )),
            ],
            options={"db_table": "reflection_job"},
        ),
        migrations.AddConstraint(
            model_name="reflectionjob",
            constraint=models.UniqueConstraint(
                fields=("friend", "chat_day"), name="unique_friend_reflection_day"
            ),
        ),
        migrations.AddIndex(
            model_name="reflectionjob",
            index=models.Index(fields=["status", "chat_day"], name="reflection_status_day"),
        ),
    ]
