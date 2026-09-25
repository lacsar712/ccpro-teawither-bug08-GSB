import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gardens", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="trough",
            name="garden",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="troughs",
                to="gardens.garden",
                verbose_name="茶园",
            ),
        ),
    ]
