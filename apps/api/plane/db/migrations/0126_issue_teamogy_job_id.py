from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0125_reset_project_deploy_board_identity"),
    ]

    operations = [
        migrations.AddField(
            model_name="issue",
            name="teamogy_job_id",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="issueversion",
            name="teamogy_job_id",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="draftissue",
            name="teamogy_job_id",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
