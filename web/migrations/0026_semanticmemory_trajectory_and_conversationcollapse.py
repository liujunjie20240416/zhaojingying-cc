from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("web", "0025_friend_online_history_generation_and_more")]

    operations = [
        migrations.AddField(
            model_name="semanticmemory",
            name="trajectory_key",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddIndex(
            model_name="semanticmemory",
            index=models.Index(
                fields=["friend", "trajectory_key", "is_active"],
                name="sem_friend_trajectory",
            ),
        ),
        migrations.CreateModel(
            name="ConversationCollapse",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("start_message_id", models.PositiveBigIntegerField()),
                ("end_message_id", models.PositiveBigIntegerField()),
                ("summary", models.TextField(max_length=2000)),
                ("topics", models.JSONField(blank=True, default=list)),
                ("token_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("friend", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="conversation_collapses", to="web.friend")),
            ],
            options={"db_table": "conversation_collapse"},
        ),
        migrations.AddConstraint(
            model_name="conversationcollapse",
            constraint=models.UniqueConstraint(
                fields=("friend", "start_message_id", "end_message_id"),
                name="unique_friend_collapse_range",
            ),
        ),
        migrations.AddIndex(
            model_name="conversationcollapse",
            index=models.Index(fields=["friend", "start_message_id"], name="collapse_friend_start"),
        ),
        migrations.AddIndex(
            model_name="conversationcollapse",
            index=models.Index(fields=["friend", "end_message_id"], name="collapse_friend_end"),
        ),
    ]
