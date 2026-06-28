from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("web", "0012_import_analysis")]

    operations = [
        migrations.AddField(
            model_name="semanticmemory",
            name="subject",
            field=models.CharField(
                choices=[
                    ("user", "用户"),
                    ("girlfriend", "女友"),
                    ("relationship", "两人关系"),
                ],
                default="user",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="semanticmemory",
            name="is_mutable",
            field=models.BooleanField(default=True),
        ),
        migrations.AddIndex(
            model_name="semanticmemory",
            index=models.Index(
                fields=["friend", "subject", "is_active"],
                name="sem_friend_subject_active",
            ),
        ),
    ]
