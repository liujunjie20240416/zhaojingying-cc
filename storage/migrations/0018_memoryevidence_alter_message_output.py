from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("web", "0017_alter_timechunk_summary")]

    operations = [
        migrations.AlterField(
            model_name="message",
            name="output",
            field=models.TextField(),
        ),
        migrations.CreateModel(
            name="MemoryEvidence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_type", models.CharField(choices=[
                    ("import_chat", "导入聊天"),
                    ("online_chat", "后续 AI 聊天"),
                    ("user_assertion", "用户手动维护"),
                ], max_length=20)),
                ("message_refs", models.JSONField(blank=True, default=list)),
                ("start_message_ref", models.IntegerField(blank=True, null=True)),
                ("end_message_ref", models.IntegerField(blank=True, null=True)),
                ("chat_day", models.DateField(blank=True, null=True)),
                ("excerpt", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("memory", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="evidences",
                    to="web.semanticmemory",
                )),
            ],
            options={"db_table": "memory_evidence"},
        ),
        migrations.AddIndex(
            model_name="memoryevidence",
            index=models.Index(fields=["memory", "source_type"], name="mem_evidence_source"),
        ),
    ]
