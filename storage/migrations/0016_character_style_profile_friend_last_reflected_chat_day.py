from django.db import migrations, models


def migrate_existing_profiles(apps, schema_editor):
    Character = apps.get_model("web", "Character")
    SemanticMemory = apps.get_model("web", "SemanticMemory")
    marker = "【从聊天记录自动学习】"
    signals = ("说话", "称呼", "语气", "口头禅", "表情", "叠词", "安慰", "撒娇", "生气")
    for character in Character.objects.all():
        if marker in (character.profile or ""):
            character.profile = character.profile.split(marker, 1)[0].rstrip()
        facts = list(SemanticMemory.objects.filter(
            friend__character_id=character.id,
            subject="girlfriend", is_active=True,
        ).order_by("-confidence", "id").values_list("fact", flat=True))
        selected = []
        for fact in facts:
            if any(signal in fact for signal in signals) and fact not in selected:
                selected.append(fact)
        character.style_profile = "\n".join(f"- {fact.rstrip('。')}" for fact in selected[:12])[:1500]
        character.save(update_fields=["profile", "style_profile"])


class Migration(migrations.Migration):
    dependencies = [("web", "0015_semanticmemory_memory_state_validity")]

    operations = [
        migrations.AddField(
            model_name="character",
            name="style_profile",
            field=models.TextField(blank=True, default="", max_length=2000),
        ),
        migrations.AddField(
            model_name="friend",
            name="last_reflected_chat_day",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(migrate_existing_profiles, migrations.RunPython.noop),
    ]
