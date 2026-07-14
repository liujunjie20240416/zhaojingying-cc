from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("web", "0024_importanalysis_completed_chunks_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="friend",
            name="online_history_generation",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="reflectionjob",
            name="history_generation",
            field=models.PositiveBigIntegerField(default=0),
        ),
    ]
