# Generated manually for lost_at / motif on DodoCard



from django.db import migrations, models





class Migration(migrations.Migration):



    dependencies = [

        ("cards", "0001_initial"),

    ]



    operations = [

        migrations.AddField(

            model_name="dodocard",

            name="lost_at",

            field=models.DateTimeField(

                blank=True,

                help_text="Horodatage du signalement de perte/vol.",

                null=True,

            ),

        ),

        migrations.AddField(

            model_name="dodocard",

            name="motif",

            field=models.CharField(

                blank=True,

                help_text="Motif de révocation / réémission (ex. perte, vol, demande).",

                max_length=160,

            ),

        ),

    ]

