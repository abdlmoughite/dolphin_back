from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0009_user_page_permissions"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ReviewImage",
        ),
        migrations.DeleteModel(
            name="ProductReview",
        ),
    ]
