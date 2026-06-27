"""ImportAnalysis / TimeChunk / TopicTag — 导入聊天记录预处理结果"""

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("web", "0011_semanticmemory_user_controls"),
    ]

    operations = [
        migrations.CreateModel(
            name="ImportAnalysis",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("total_messages", models.IntegerField(default=0)),
                ("user_profile", models.TextField(default="")),
                ("relationship_overview", models.TextField(default="")),
                ("timeline_json", models.TextField(default="[]")),
                (
                    "status",
                    models.CharField(
                        default="pending",
                        max_length=20,
                        choices=[
                            ("pending", "待分析"),
                            ("analyzing", "分析中"),
                            ("done", "已完成"),
                            ("failed", "失败"),
                        ],
                    ),
                ),
                ("error_message", models.TextField(default="")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "character",
                    models.OneToOneField(
                        to="web.Character", on_delete=models.CASCADE
                    ),
                ),
            ],
            options={"db_table": "import_analysis"},
        ),
        migrations.CreateModel(
            name="TimeChunk",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("label", models.CharField(max_length=100)),
                ("start_msg_index", models.IntegerField()),
                ("end_msg_index", models.IntegerField()),
                ("summary", models.CharField(max_length=200)),
                ("key_events", models.TextField(default="[]")),
                (
                    "character",
                    models.ForeignKey(
                        to="web.Character", on_delete=models.CASCADE
                    ),
                ),
            ],
            options={"db_table": "time_chunk"},
        ),
        migrations.AddIndex(
            model_name="timechunk",
            index=models.Index(
                fields=["character", "start_msg_index"],
                name="tc_char_sidx",
            ),
        ),
        migrations.CreateModel(
            name="TopicTag",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("tag", models.CharField(max_length=100)),
                ("msg_indices", models.TextField(default="[]")),
                (
                    "character",
                    models.ForeignKey(
                        to="web.Character", on_delete=models.CASCADE
                    ),
                ),
            ],
            options={"db_table": "topic_tag"},
        ),
    ]
