# Système de Gestion des Achats - Backend API

Un système Django REST Framework pour la gestion des demandes d'achat avec workflow de validation multi-niveaux.

## Table des matières

- [Fonctionnalités](#fonctionnalités)
- [Installation](#installation)
- [Configuration](#configuration)
- [Utilisation](#utilisation)
- [API Documentation](#api-documentation)
- [Modèles de données](#modèles-de-données)
- [Architecture](#architecture)
- [Déploiement](#déploiement)

## Fonctionnalités

- **Gestion des utilisateurs** avec 4 rôles : Employé, Moyens Généraux, Comptabilité, Direction
- **Workflow de validation** à 3 étapes pour les demandes d'achat
- **Authentification JWT** avec cookies HttpOnly sécurisés
- **Notifications email automatiques** via Mailjet
- **Upload de fichiers** (PDF, images) via Cloudinary (SaaS) ou stockage local
- **Dashboard** avec statistiques et analytics
- **Réinitialisation de mot de passe** par code à 5 chiffres
- **Journal d'activité** pour audit et traçabilité

## Installation

### Prérequis

- Python 3.9+
- PostgreSQL
- Compte [Mailjet](https://mailjet.com)
- Compte [Cloudinary](https://cloudinary.com/users/register/free) (plan gratuit)

### Installation des dépendances

```bash
pip install -r requirements.txt
```

### Configuration de la base de données

```bash
python manage.py migrate
python manage.py createsuperuser
```

### Démarrage du serveur

```bash
python manage.py runserver
```

## Configuration

### Variables d'environnement

Créer un fichier `.env` à la racine du projet :

```env
# Django
SECRET_KEY=your_secret_key_here
ALLOWED_HOSTS=your-domain.com,localhost
DATABASE_NAME=your_db_name
DATABASE_USER=your_db_user
DATABASE_PASSWORD=your_db_password
DATABASE_HOST=localhost
DATABASE_PORT=5432

# CORS & Security
CORS_ALLOWED_ORIGINS=https://your-frontend.com,http://localhost:3000
CSRF_TRUSTED_ORIGINS=https://your-frontend.com,http://localhost:3000
JWT_COOKIE_DOMAIN=your-domain.com

# Fichier d'environnement
Copiez `.env.example` vers `.env` et remplissez les valeurs sensibles.

# Email (SMTP / Gmail / Mailjet / Brevo)
# Option 1 : Gmail (mot de passe applicatif requis)
EMAIL_PROVIDER=gmail  # alias smtp -> smtp.gmail.com
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=ton-adresse@gmail.com
EMAIL_HOST_PASSWORD=mot_de_passe_app_google  # généré via la 2FA
DEFAULT_FROM_EMAIL=ton-adresse@gmail.com
DEFAULT_FROM_NAME=Your Company Name

# Option 2 : Mailjet
# EMAIL_PROVIDER=mailjet
# MAILJET_API_KEY=your_mailjet_api_key
# MAILJET_SECRET_KEY=your_mailjet_secret_key
# DEFAULT_FROM_EMAIL=noreply@your-domain.com
# DEFAULT_FROM_NAME=Your Company Name
# EMAIL_HOST=smtp.mailjet.com
# EMAIL_PORT=587
# MAILJET_SANDBOX_MODE=False
# MAILJET_TRACK_OPENS=enabled   # enabled | disabled
# MAILJET_TRACK_CLICKS=enabled  # enabled | disabled
# MAILJET_MAX_RETRIES=3
# MAILJET_RETRY_BACKOFF=1.5

# Option 3 : Brevo (ex Sendinblue) via API HTTPS (recommandé sur Render)
# EMAIL_PROVIDER=brevo
# BREVO_API_KEY=your_brevo_v3_api_key
# DEFAULT_FROM_EMAIL=noreply@your-domain.com
# DEFAULT_FROM_NAME=Your Company Name

# Option 4 : Resend (API HTTPS)
# EMAIL_PROVIDER=resend
# RESEND_API_KEY=re_your_resend_api_key
# DEFAULT_FROM_EMAIL=no-reply@ton-domaine.verifie (sender vérifié chez Resend)
# DEFAULT_FROM_NAME=Your Company Name

# Stockage (Supabase)
SUPABASE_ENABLED=true
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_SERVICE_KEY=your_service_role_key
SUPABASE_BUCKET=attachments
SUPABASE_FOLDER=purchase-requests
SUPABASE_PUBLIC_BUCKET=false
SUPABASE_SIGNED_URL_TTL=3600

# Superuser bootstrap (optionnel, utilisé par la migration 0008)
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_EMAIL=admin@example.com
DJANGO_SUPERUSER_PASSWORD=motdepassefort
DJANGO_SUPERUSER_ROLE=director

# Frontend
FRONTEND_URL=https://your-frontend.com
COMPANY_NAME=Your Company Name
```

## Utilisation

### Rôles utilisateur

| Rôle                | Permissions                             |
| ------------------- | --------------------------------------- |
| **Employé**         | Créer et consulter ses propres demandes |
| **Moyens Généraux** | Valider les demandes (étape 1)          |
| **Comptabilité**    | Étudier le budget et valider (étape 2)  |
| **Direction**       | Approbation finale (étape 3)            |

### Workflow des demandes

1. **Employé** crée une demande → Status: `pending`
2. **Moyens Généraux** valide → Status: `mg_approved`
3. **Comptabilité** étudie le budget → Status: `accounting_reviewed`
4. **Direction** approuve → Status: `director_approved`

À chaque étape, la demande peut être rejetée avec un motif.

## API Documentation

### Base URL

```
https://your-api-domain.com/api/
```

### Authentification

#### Login

**POST** `/auth/login/`

```json
// Request
{
  "login": "username_or_email",
  "password": "password"
}

// Response (200)
{
  "user": {
    "id": 1,
    "username": "john.doe",
    "email": "john@example.com",
    "first_name": "John",
    "last_name": "Doe",
    "role": "employee",
    "department": "IT",
    "phone": "+225 01 23 45 67"
  },
  "message": "Connexion réussie. Bienvenue John!",
  "success": true
}

// Error (401)
{
  "error": "Identifiants invalides",
  "details": "Vérifiez votre nom d'utilisateur/email et votre mot de passe"
}
```

#### Refresh Token

**POST** `/auth/refresh/`

```json
// Response (200)
{
  "success": true,
  "message": "Token rafraîchi avec succès"
}

// Error (401)
{
  "error": "Refresh token invalide ou expiré"
}
```

#### Logout

**POST** `/auth/logout/`

```json
// Response (200)
{
  "message": "Déconnexion réussie",
  "success": true
}
```

#### Profil utilisateur

**GET** `/auth/me/`

```json
// Response (200)
{
  "id": 1,
  "username": "john.doe",
  "email": "john@example.com",
  "first_name": "John",
  "last_name": "Doe",
  "role": "employee",
  "department": "IT",
  "phone": "+225 01 23 45 67"
}
```

### Gestion des utilisateurs

#### Créer un utilisateur (Admin uniquement)

**POST** `/auth/register/`

```json
// Request
{
  "username": "jane.smith",
  "email": "jane@example.com",
  "first_name": "Jane",
  "last_name": "Smith",
  "role": "mg",
  "department": "Moyens Généraux",
  "phone": "+225 07 89 01 23"
}

// Response (201)
{
  "id": 2,
  "username": "jane.smith",
  "email": "jane@example.com",
  "first_name": "Jane",
  "last_name": "Smith",
  "role": "mg",
  "department": "Moyens Généraux",
  "phone": "+225 07 89 01 23",
  "generated_password": "Kz8@mN3pQ9x!",
  "created_at": "2024-01-15T10:30:00Z",
  "is_active": true,
  "email_sent": true,
  "message": "Utilisateur créé avec succès. Mot de passe généré : Kz8@mN3pQ9x!. Email de bienvenue : envoyé."
}
```

#### Liste des utilisateurs

**GET** `/users/`

```json
// Response (200)
{
  "count": 25,
  "next": "http://api.example.com/users/?page=2",
  "previous": null,
  "results": [
    {
      "id": 1,
      "username": "john.doe",
      "email": "john@example.com",
      "first_name": "John",
      "last_name": "Doe",
      "full_name": "John Doe",
      "role": "employee",
      "role_display": "Employé",
      "department": "IT",
      "phone": "+225 01 23 45 67",
      "is_active": true,
      "created_by_name": "admin",
      "created_by_role": "Direction",
      "created_at": "2024-01-10T08:00:00Z",
      "updated_at": "2024-01-10T08:00:00Z"
    }
  ]
}
```

### Demandes d'achat

#### Créer une demande

**POST** `/requests/`

```json
// Request
{
  "item_description": "Ordinateur portable Dell XPS 13",
  "quantity": 1,
  "estimated_cost": 1200000,
  "urgency": "high",
  "justification": "Mon ordinateur actuel ne fonctionne plus et j'ai besoin d'un nouvel équipement pour continuer mes tâches quotidiennes."
}

// Response (201)
{
  "id": 15,
  "user": 1,
  "user_name": "john.doe",
  "department": "IT",
  "item_description": "Ordinateur portable Dell XPS 13",
  "quantity": 1,
  "estimated_cost": "1200000.00",
  "urgency": "high",
  "urgency_display": "Élevée",
  "justification": "Mon ordinateur actuel ne fonctionne plus...",
  "status": "pending",
  "status_display": "En attente",
  "current_step": "Moyens Généraux",
  "created_at": "2024-01-15T14:30:00Z",
  "updated_at": "2024-01-15T14:30:00Z"
}
```

#### Liste des demandes

**GET** `/requests/`

Query parameters:

- `status`: Filtrer par statut (`pending`, `mg_approved`, `accounting_reviewed`, `director_approved`, `rejected`)
- `urgency`: Filtrer par urgence (`low`, `medium`, `high`, `critical`)
- `search`: Recherche dans la description

```json
// Response (200)
{
  "count": 50,
  "next": "http://api.example.com/requests/?page=2",
  "previous": null,
  "results": [
    {
      "id": 15,
      "user": 1,
      "user_id": 1,
      "user_name": "john.doe",
      "department": "IT",
      "created_by": 1,
      "item_description": "Ordinateur portable Dell XPS 13",
      "quantity": 1,
      "estimated_cost": "1200000.00",
      "final_cost": null,
      "urgency": "high",
      "urgency_display": "Élevée",
      "status": "pending",
      "status_display": "En attente",
      "current_step": "Moyens Généraux",
      "created_at": "2024-01-15T14:30:00Z",
      "updated_at": "2024-01-15T14:30:00Z",
      "justification": "Mon ordinateur actuel ne fonctionne plus...",
      "rejected_by": null,
      "rejected_by_name": null,
      "rejected_by_role": null,
      "accounting_validated_by": null,
      "accounting_validated_by_name": null,
      "approved_by": null,
      "approved_by_name": null
    }
  ]
}
```

#### Détail d'une demande

**GET** `/requests/{id}/`

```json
// Response (200)
{
  "id": 15,
  "user": 1,
  "user_name": "john.doe",
  "item_description": "Ordinateur portable Dell XPS 13",
  "quantity": 1,
  "estimated_cost": "1200000.00",
  "urgency": "high",
  "urgency_display": "Élevée",
  "justification": "Mon ordinateur actuel ne fonctionne plus...",
  "status": "mg_approved",
  "status_display": "Validée par Moyens Généraux",
  "current_step": "Comptabilité",
  "budget_available": null,
  "final_cost": null,
  "created_at": "2024-01-15T14:30:00Z",
  "updated_at": "2024-01-15T16:45:00Z",
  "rejection_reason": null,
  "rejected_by": null,
  "rejected_by_name": null,
  "rejected_at": null,
  "rejected_by_role": null,
  "steps": [
    {
      "id": 23,
      "user": 2,
      "user_name": "jane.smith",
      "user_role": "Moyens Généraux",
      "action": "approved",
      "comment": "Demande validée, équipement nécessaire",
      "budget_check": null,
      "created_at": "2024-01-15T16:45:00Z"
    },
    {
      "id": 22,
      "user": 1,
      "user_name": "john.doe",
      "user_role": "Employé",
      "action": "submitted",
      "comment": "Demande créée",
      "budget_check": null,
      "created_at": "2024-01-15T14:30:00Z"
    }
  ],
  "attachments": [
    {
      "id": 8,
      "file_url": "https://drive.google.com/uc?id=FILE_ID&export=download",
      "file_type": "quote",
      "description": "Devis Dell XPS 13",
      "uploaded_by": 1,
      "uploaded_by_name": "john.doe",
      "file_size_mb": 0.25,
      "created_at": "2024-01-15T14:35:00Z"
    }
  ]
}
```

#### Valider/Rejeter une demande

**POST** `/requests/{id}/validate/`

```json
// Request (Approbation MG)
{
  "action": "approve",
  "comment": "Demande validée, équipement nécessaire"
}

// Request (Approbation Comptabilité)
{
  "action": "approve",
  "comment": "Budget disponible",
  "budget_available": true,
  "final_cost": 1150000
}

// Request (Rejet)
{
  "action": "reject",
  "comment": "Budget insuffisant pour ce type d'équipement"
}

// Response (200)
{
  "message": "Demande approuvée avec succès",
  "status": "accounting_reviewed",
  "current_step": "Direction"
}

// Error (400)
{
  "error": "Vous n'avez pas les permissions pour valider cette demande",
  "details": "Cette demande doit être traitée par: Moyens Généraux"
}
```

### Pièces jointes

#### Upload de fichier

**POST** `/attachments/`

```json
// Request (multipart/form-data)
{
  "file": [FILE],
  "file_type": "quote",
  "description": "Devis officiel Dell",
  "request": 15
}

// Response (201)
{
  "id": 8,
  "file_url": "https://drive.google.com/uc?id=FILE_ID&export=download",
  "file_type": "quote",
  "description": "Devis officiel Dell",
  "request": 15,
  "uploaded_by": 1,
  "uploaded_by_name": "john.doe",
  "file_size_mb": 0.25,
  "created_at": "2024-01-15T14:35:00Z"
}

// Error (400)
{
  "file": [
    "Format de fichier non supporté: application/msword. Formats acceptés: application/pdf, image/jpeg, image/png, image/jpg"
  ]
}
```

#### Supprimer un fichier

**DELETE** `/attachments/{id}/`

```json
// Response (204)
// Pas de contenu

// Error (403)
{
  "error": "Vous ne pouvez supprimer que vos propres fichiers"
}
```

### Dashboard

#### Statistiques globales

**GET** `/dashboard/`

```json
// Response (200)
{
  "total_requests": 156,
  "pending_requests": 23,
  "in_progress_requests": 45,
  "approved_requests": 78,
  "rejected_requests": 10,
  "user_requests_count": 12,
  "requests_by_status": {
    "pending": 23,
    "mg_approved": 18,
    "accounting_reviewed": 27,
    "director_approved": 78,
    "rejected": 10
  },
  "monthly_stats": [
    {
      "month": "2024-01",
      "total": 45,
      "approved": 32,
      "rejected": 5
    }
  ],
  "recent_requests": [
    // Liste des 10 dernières demandes
  ],
  "all_requests": [
    // Toutes les demandes selon le rôle
  ],
  // Statistiques spécifiques par rôle
  "accounting_total": 89,
  "accounting_pending": 27,
  "validation_rate": 85,
  "processing_delays": {
    "average_days": 3.2,
    "by_urgency": {
      "critical": 0.5,
      "high": 1.2,
      "medium": 3.1,
      "low": 5.8
    }
  }
}
```

### Réinitialisation de mot de passe

#### Demander un code de réinitialisation

**POST** `/auth/password-reset/request/`

```json
// Request
{
  "email": "john@example.com"
}

// Response (200)
{
  "message": "Un code de réinitialisation a été envoyé à votre adresse email",
  "success": true
}

// Error (400)
{
  "email": ["Aucun compte associé à cet email."]
}
```

#### Vérifier le code

**POST** `/auth/password-reset/verify/`

```json
// Request
{
  "email": "john@example.com",
  "code": "12345"
}

// Response (200)
{
  "message": "Code vérifié avec succès",
  "token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "success": true
}

// Error (400)
{
  "non_field_errors": ["Code expiré."]
}
```

#### Confirmer le nouveau mot de passe

**POST** `/auth/password-reset/confirm/`

```json
// Request
{
  "token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "new_password": "NewSecurePassword123!",
  "confirm_password": "NewSecurePassword123!"
}

// Response (200)
{
  "message": "Mot de passe réinitialisé avec succès",
  "success": true
}
```

## Modèles de données

### CustomUser

- **Rôles** : `employee`, `mg`, `accounting`, `director`
- **Champs** : username, email, first_name, last_name, role, department, phone
- **Relations** : created_by (auto-référence), activities, requests

### PurchaseRequest

- **Statuts** : `pending`, `mg_approved`, `accounting_reviewed`, `director_approved`, `rejected`
- **Urgence** : `low`, `medium`, `high`, `critical`
- **Champs** : item_description, quantity, estimated_cost, final_cost, justification
- **Relations** : user, steps, attachments, validation_fields

### RequestStep

Journal des actions sur une demande

- **Actions** : `submitted`, `approved`, `rejected`, `reviewed`
- **Champs** : user, action, comment, budget_check, created_at

### Attachment

Pièces jointes liées aux demandes

- **Types** : `quote` (devis), `invoice` (facture), `justification`, `other`
- **Stockage** : Cloudinary (PDF, JPG, PNG) ou fallback local
- **Limite** : 10MB par fichier

## Architecture

### Structure du projet

```
simplonservice/
├── core/                    # Application principale
│   ├── models.py           # Modèles CustomUser, PurchaseRequest, etc.
│   ├── views.py            # Vues API REST
│   ├── serializers.py      # Sérialiseurs DRF
│   ├── urls.py             # Configuration des URLs
│   ├── authentication.py   # Classes d'auth personnalisées
│   ├── jwt_views.py        # Vues JWT avec cookies
│   ├── signals.py          # Signaux Django
│   └── admin.py            # Interface d'administration
├── services/
│   └── email_service.py    # Service Mailjet
└── simplonservice/
    ├── settings.py         # Configuration Django
    └── urls.py             # URLs racine
```

### Stack technique

- **Backend** : Django 5.2 + Django REST Framework 3.16
- **Base de données** : PostgreSQL
- **Authentification** : JWT avec cookies HttpOnly
- **Email** : Mailjet REST API
- **Fichiers** : Cloudinary
- **Serveur** : Gunicorn + WhiteNoise

## Dépendances

```txt
asgiref==3.9.1
certifi==2025.8.3
charset-normalizer==3.4.3
Django==5.2.4
djangorestframework==3.16.0
djangorestframework_simplejwt==5.5.1
gunicorn==23.0.0
idna==3.10
mailjet-rest==1.5.1
packaging==25.0
pillow==11.3.0
psycopg2-binary==2.9.10
PyJWT==2.10.1
python-dateutil==2.9.0.post0
python-decouple==3.8
python-dotenv==1.0.0
requests==2.32.5
six==1.17.0
sqlparse==0.5.3
tzdata==2025.2
urllib3==2.5.0
whitenoise==6.10.0
dj-database-url==2.1.0
cloudinary==1.40.0
```

## Déploiement

### Checklist production

1. **Variables d'environnement**

   ```bash
   DEBUG=False
   JWT_COOKIE_SECURE=True
   CSRF_COOKIE_SECURE=True
   ```

2. **Base de données**

   ```bash
   python manage.py migrate
   python manage.py collectstatic --noinput
   python manage.py createsuperuser
   ```

3. **Services externes**

   - Configurer Mailjet (API keys)
   - Configurer Cloudinary (cloud name, API key/secret, dossier)
   - Vérifier les domaines CORS

4. **Serveur**
   ```bash
   gunicorn simplonservice.wsgi:application --bind 0.0.0.0:8000
   ```

### Sécurité

- **HTTPS obligatoire** en production
- **Cookies HttpOnly/Secure** pour les JWT
- **CORS** configuré pour le frontend uniquement
- **Validation stricte** des données d'entrée
- **Audit trail** complet des actions

## Maintenance

### Logs importants

- Connexions/déconnexions utilisateurs
- Validation/rejet des demandes
- Échecs d'envoi d'emails
- Erreurs d'upload de fichiers

### Monitoring

- Performance de la base de données
- Taux de succès des emails Mailjet
- Consommation de l'espace Cloudinary (quota gratuit 25 Go)
- Temps de réponse des endpoints

## Support

Pour toute question technique ou problème :

1. Vérifier les logs de l'application
2. Consulter cette documentation
3. Tester les endpoints avec les exemples fournis
4. Contacter l'équipe de développement

---

**Version**: 1.0  
**Dernière mise à jour**: 19 Septembre 2025
