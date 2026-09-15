from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("web", "0019_reflectionjob_message_output_bubbles")]

    operations = [
        migrations.CreateModel(
            name="PreprocessingCheckpoint",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("source_fingerprint", models.CharField(max_length=64)),
                ("chunk_index", models.PositiveIntegerField()),
                ("chunk_fingerprint", models.CharField(max_length=64)),
                ("result_json", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "character",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="web.character",
                    ),
                ),
            ],
            options={"db_table": "preprocessing_checkpoint"},
        ),
        migrations.AddConstraint(
            model_name="preprocessingcheckpoint",
            constraint=models.UniqueConstraint(
                fields=("character", "source_fingerprint", "chunk_index"),
                name="unique_character_source_chunk",
            ),
        ),
        migrations.AddIndex(
            model_name="preprocessingcheckpoint",
            index=models.Index(
                fields=["character", "source_fingerprint"],
                name="checkpoint_char_source",
            ),
        ),
    ]
