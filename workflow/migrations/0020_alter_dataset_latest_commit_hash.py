# Generated by Django 4.2.11 on 2024-05-03 21:09

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workflow", "0019_remove_workflows_dataset_dataset_workflow_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="dataset",
            name="latest_commit_hash",
            field=models.CharField(blank=True, null=True),
        ),
    ]