from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("web", "0028_drop_episodicmemory")]

    operations = [
        migrations.AddField(
            model_name="character",
            name="import_data_version",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
