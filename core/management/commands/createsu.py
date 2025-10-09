from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
import os

User = get_user_model()


class Command(BaseCommand):
    help = 'Crée un superuser automatiquement si aucun utilisateur n\'existe'

    def handle(self, *args, **options):
        username = os.environ.get('DJANGO_SUPERUSER_USERNAME', 'admin')
        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', 'vsawadogo@ext.simplon.co')
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '14209')
        
        try:
            # Vérifier si le superuser existe déjà
            if User.objects.filter(username=username).exists():
                self.stdout.write(
                    self.style.WARNING(f'⚠️  Le superuser "{username}" existe déjà.')
                )
                return
            
            # Créer le superuser
            User.objects.create_superuser(
                username=username,
                email=email,
                password=password
            )
            
            self.stdout.write(
                self.style.SUCCESS(f'✅ Superuser "{username}" créé avec succès!')
            )
            self.stdout.write(
                self.style.SUCCESS(f'   Email: {email}')
            )
            
        except IntegrityError as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Erreur d\'intégrité: {e}')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Erreur inattendue: {e}')
            )