from django.db import migrations, models
from django.db.models import F


def remove_cross_user_imported_memories(apps, schema_editor):
    """Existing characters become private by default; remove leaked projections.

    Imported Chat remains intact on Character. Semantic Memory is derived and can
    be regenerated or shared again after the owner explicitly chooses public.
    """
    SemanticMemory = apps.get_model("web", "SemanticMemory")
    SemanticMemory.objects.filter(source="import").exclude(
        friend__me_id=F("friend__character__author_id")
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("web", "0020_preprocessingcheckpoint")]

    operations = [
        migrations.AddField(
            model_name="character",
            name="imported_memory_visibility",
            field=models.CharField(
                choices=[
                    ("private", "仅自己可用"),
                    ("public", "所有使用该角色的用户可用"),
                ],
                default="private",
                help_text="控制导入聊天原文及其派生记忆是否允许其他用户检索",
                max_length=10,
            ),
        ),
        migrations.RunPython(
            remove_cross_user_imported_memories,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
