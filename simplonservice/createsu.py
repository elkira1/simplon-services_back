from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
import os

User = get_user_model()

class Command(BaseCommand):
    help = 'Crée un superuser automatiquement'

    def handle(self, *args, **options):
        username = os.environ.get('DJANGO_SUPERUSER_USERNAME', 'admin')
        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', 'vsawadogo.ext@simplon.co')
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '14209')
        
        try:
            if not User.objects.filter(username=username).exists():
                User.objects.create_superuser(
                    username=username,
                    email=email,
                    password=password
                )
                self.stdout.write(
                    self.style.SUCCESS(f'✅ Superuser "{username}" créé avec succès!')
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f'⚠️  Superuser "{username}" existe déjà')
                )
        except IntegrityError as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Erreur lors de la création: {e}')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Erreur inattendue: {e}')
            )