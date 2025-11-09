import secrets
from decimal import Decimal, InvalidOperation
from django.shortcuts import render
from django.contrib.auth import get_user_model


# Create your views here.
from django.shortcuts import get_object_or_404
from django.db.models import Q, Count, Sum, Avg, Case, When, Value, F, DecimalField
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from datetime import datetime, timedelta
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from .models import PasswordResetCode, PurchaseRequest, RequestStep, Attachment, UserActivity
from .serializers import (
    PasswordChangeSerializer, PasswordResetConfirmSerializer, PasswordResetRequestSerializer, PasswordResetVerifySerializer, PurchaseRequestListSerializer, PurchaseRequestDetailSerializer,
    PurchaseRequestCreateSerializer, UserActivitySerializer, UserListSerializer, UserProfileUpdateSerializer, UserRegistrationSerializer, UserUpdateSerializer, ValidateRequestSerializer,
    AttachmentSerializer, DashboardSerializer, UserSerializer
)

from django.core.paginator import Paginator
from django.db.models.functions import Extract
from django.conf import settings
import logging

from rest_framework.permissions import AllowAny
from services.email_service import EmailService
from services.supabase_storage_service import (
    SupabaseStorageService,
    SupabaseStorageError,
)


logger = logging.getLogger(__name__)



User = get_user_model()

# Mapping des filtres par rôle pour les listes de demandes
ROLE_REQUEST_FILTERS = {
    'employee': lambda user: Q(user=user),
    'mg': lambda user: None,  # Aucun filtre, accès complet
    'accounting': lambda user: (
        Q(status__in=['mg_approved', 'accounting_reviewed', 'director_approved']) |
        Q(status='rejected', rejected_by_role='accounting') |
        Q(rejected_by=user.id, rejected_by_role='accounting')
    ),
    'director': lambda user: (
        Q(status__in=['accounting_reviewed', 'director_approved']) |
        Q(status='rejected', rejected_by_role='director') |
        Q(rejected_by=user.id, rejected_by_role='director')
    ),
}

# Actions autorisées par rôle sur une demande en fonction du statut actuel
ALLOWED_STATUS_BY_ROLE = {
    'mg': {'pending'},
    'accounting': {'mg_approved'},
    'director': {'accounting_reviewed'},
}


def get_purchase_requests_queryset_for_user(user):
    """Retourne le queryset filtré selon le rôle de l'utilisateur."""
    base_queryset = PurchaseRequest.objects.all()
    role = getattr(user, 'role', None)
    filter_builder = ROLE_REQUEST_FILTERS.get(role)

    if not filter_builder:
        return base_queryset.none()

    role_filter = filter_builder(user)
    return base_queryset if role_filter is None else base_queryset.filter(role_filter)


def get_role_specific_requests(requests, role, user_id):
    """Filtrer les demandes visibles selon le rôle pour le dashboard"""
    if role == 'employee':
        return requests.filter(user_id=user_id)
    elif role == 'mg':
        return requests
    elif role == 'accounting':
        return requests.filter(
            Q(status__in=['mg_approved', 'accounting_reviewed', 'director_approved']) |
            Q(status='rejected', rejected_by_role='accounting')
        )
    elif role == 'director':
        return requests.filter(
            Q(status__in=['accounting_reviewed', 'director_approved']) |
            Q(status='rejected', rejected_by_role='director')
        )
    return requests.none()

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def current_user(request):
    """Retourne les infos de l'utilisateur connecté"""
    serializer = UserSerializer(request.user)
    return Response(serializer.data)

def get_client_ip(request):
    """Récupérer l'adresse IP du client"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_request(request):
    
    serializer = PasswordResetRequestSerializer(data=request.data)
    
    if serializer.is_valid():
        email = serializer.validated_data['email']
        
        try:
            user = User.objects.get(email__iexact=email)
            
            PasswordResetCode.objects.filter(
                user=user, 
                is_used=False
            ).update(is_used=True)
            
            reset_code = PasswordResetCode.objects.create(
                user=user,
                ip_address=get_client_ip(request)
            )
            
            email_service = EmailService()
            email_sent = email_service.send_password_reset_code(user, reset_code.code)
            
            if email_sent:
                logger.info(f"Password reset code sent to {email}")
                return Response({
                    'message': 'Code de vérification envoyé par email',
                    'expires_in': 300  
                }, status=status.HTTP_200_OK)
            else:
                logger.error(f"Failed to send password reset code to {email}")
                return Response({
                    'error': 'Erreur lors de l\'envoi de l\'email'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
        except User.DoesNotExist:
            return Response({
                'message': 'Si cet email existe, un code a été envoyé'
            }, status=status.HTTP_200_OK)
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_verify(request):
    serializer = PasswordResetVerifySerializer(data=request.data)
    
    if serializer.is_valid():
        reset_code = serializer.validated_data['reset_code']
        
        reset_code.is_used = True
        reset_code.save()
        
        reset_token = secrets.token_urlsafe(32)
        
        from django.core.cache import cache
        cache.set(f'reset_token_{reset_token}', reset_code.user.id, timeout=300)  
        
        logger.info(f"Password reset code verified for user {reset_code.user.username}")
        
        return Response({
            'message': 'Code vérifié avec succès',
            'reset_token': reset_token
        }, status=status.HTTP_200_OK)
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_confirm(request):
    serializer = PasswordResetConfirmSerializer(data=request.data)
    
    if serializer.is_valid():
        reset_token = serializer.validated_data['token']
        new_password = serializer.validated_data['new_password']
        
        from django.core.cache import cache
        user_id = cache.get(f'reset_token_{reset_token}')
        
        if not user_id:
            return Response({
                'error': 'Token invalide ou expiré'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            user = User.objects.get(id=user_id)
            user.set_password(new_password)
            user.save()
            
            cache.delete(f'reset_token_{reset_token}')
            
            from .models import UserActivity
            UserActivity.objects.create(
                user=user,
                performed_by=user,
                action='updated',
                details={'action': 'password_reset'},
                ip_address=get_client_ip(request)
            )
            
            logger.info(f"Password successfully reset for user {user.username}")
            
            return Response({
                'message': 'Mot de passe réinitialisé avec succès'
            }, status=status.HTTP_200_OK)
            
        except User.DoesNotExist:
            return Response({
                'error': 'Utilisateur non trouvé'
            }, status=status.HTTP_400_BAD_REQUEST)
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



@api_view(['POST'])
@permission_classes([IsAuthenticated])
def register_user(request):
    
    if request.user.role not in ['mg', 'director']:
        return Response(
            {'error': 'Vous n\'avez pas les droits pour créer des utilisateurs'}, 
            status=status.HTTP_403_FORBIDDEN
        )

    logger.info(f"User registration attempt by: {request.user.username} (role: {request.user.role})")
    logger.info(f"Registration data: {request.data}")

    serializer = UserRegistrationSerializer(
        data=request.data,
        context={
            'created_by': request.user,
            'ip_address': get_client_ip(request)
        }
    )

    if serializer.is_valid():
        try:
            user = serializer.save()
            logger.info(f"User created successfully: {user.username}")
            
            email_status = getattr(user, 'email_sent', False)
            if email_status:
                logger.info(f"Welcome email sent successfully to {user.email}")
            else:
                logger.warning(f"Failed to send welcome email to {user.email}")
            
            return Response(
                serializer.data, 
                status=status.HTTP_201_CREATED
            )
                    
        except Exception as e:
            logger.error(f"Error creating user: {e}")
            return Response(
                {'error': f'Erreur lors de la création de l\'utilisateur: {str(e)}'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
    else:
        logger.warning(f"Registration validation errors: {serializer.errors}")
        return Response(
            serializer.errors, 
            status=status.HTTP_400_BAD_REQUEST
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def users_list(request):
    
    if request.user.role not in ['mg', 'director']:
        return Response(
            {'error': 'Vous n\'avez pas les droits pour voir la liste des utilisateurs'}, 
            status=status.HTTP_403_FORBIDDEN
        )
    
    search = request.GET.get('search', '')
    role_filter = request.GET.get('role', '')
    created_by_filter = request.GET.get('created_by', '')
    is_active = request.GET.get('is_active', '')
    page = request.GET.get('page', 1)
    
    queryset = User.objects.all().select_related('created_by')
    
    if search:
        queryset = queryset.filter(
            Q(username__icontains=search) |
            Q(first_name__icontains=search) |
            Q(last_name__icontains=search) |
            Q(email__icontains=search)
        )
    
    if role_filter:
        queryset = queryset.filter(role=role_filter)
    
    if created_by_filter:
        queryset = queryset.filter(created_by__username=created_by_filter)
    
    if is_active:
        queryset = queryset.filter(is_active=is_active.lower() == 'true')
    
    paginator = Paginator(queryset, 20)
    users = paginator.get_page(page)
    
    serializer = UserListSerializer(users, many=True)
    
    stats = {
        'total_users': User.objects.count(),
        'active_users': User.objects.filter(is_active=True).count(),
        'users_created_by_me': User.objects.filter(created_by=request.user).count(),
        'recent_users': User.objects.filter(created_by=request.user).count(),
        'users_created_last_7_days': User.objects.filter(
        created_by=request.user,
        date_joined__gte=timezone.now() - timedelta(days=7)).count()
    
    }
    
    logger.debug("Users created last 7 days (by %s): %s", request.user.username, stats['users_created_last_7_days'])
    
    creators = User.objects.filter(
        created_users__isnull=False
    ).distinct().values('username', 'first_name', 'last_name')
    
    return Response({
        'users': serializer.data,
        'pagination': {
            'current_page': users.number,
            'total_pages': users.paginator.num_pages,
            'total_count': users.paginator.count,
            'has_next': users.has_next(),
            'has_previous': users.has_previous()
        },
        'stats': stats,
        'filters': {
            'creators': list(creators),
            'roles': User.ROLES
        }
    })


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def user_detail(request, user_id):
    
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response(
            {'error': 'Utilisateur non trouvé'}, 
            status=status.HTTP_404_NOT_FOUND
        )
    
    if request.method == 'GET':
        serializer = UserListSerializer(user)
        
        activities = UserActivity.objects.filter(user=user)[:10]  # 10 dernières activités
        activities_serializer = UserActivitySerializer(activities, many=True)
        
        data = serializer.data
        data['activities'] = activities_serializer.data
        
        return Response(data)
    
    elif request.method == 'PATCH':        
        if user == request.user:
            serializer = UserProfileUpdateSerializer(
                user, 
                data=request.data, 
                partial=True,
                context={'ip_address': get_client_ip(request)}
            )
        elif request.user.role in ['mg', 'director']:
            serializer = UserUpdateSerializer(
                user, 
                data=request.data, 
                partial=True,
                context={
                    'performed_by': request.user,
                    'ip_address': get_client_ip(request)
                }
            )
        else:
            return Response(
                {'error': 'Vous n\'avez pas les droits pour modifier cet utilisateur'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def users_stats(request):
    
    if request.user.role not in ['mg', 'director']:
        return Response(
            {'error': 'Accès non autorisé'}, 
            status=status.HTTP_403_FORBIDDEN
        )
    
    
    now = timezone.now()
    last_30_days = now - timedelta(days=30)
    last_7_days = now - timedelta(days=7)
    
    stats = {
        'total_users': User.objects.count(),
        'active_users': User.objects.filter(is_active=True).count(),
        'users_by_role': dict(
            User.objects.values('role').annotate(
                count=Count('role')
            ).values_list('role', 'count')
        ),
        'users_created_last_30_days': User.objects.filter(
            created_at__gte=last_30_days
        ).count(),
        'users_created_last_7_days': User.objects.filter(
            created_at__gte=last_7_days
        ).count(),
        'users_created_by_me': User.objects.filter(
            created_by=request.user
        ).count(),
        'recent_activities': UserActivitySerializer(
            UserActivity.objects.select_related('user', 'performed_by')[:5],
            many=True
        ).data
    }
    
    return Response(stats)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def change_password(request):
    """Changer le mot de passe de l'utilisateur connecté"""
    
    serializer = PasswordChangeSerializer(
        data=request.data,
        context={'request': request, 'ip_address': get_client_ip(request)}
    )
    
    if serializer.is_valid():
        try:
            serializer.save()
            return Response(
                {'message': 'Mot de passe modifié avec succès'}, 
                status=status.HTTP_200_OK
            )
        except Exception as e:
            logger.error(f"Error changing password for user {request.user.username}: {e}")
            return Response(
                {'error': 'Erreur lors du changement de mot de passe'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
    else:
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def purchase_requests_list(request):
    """Liste des demandes d'achat + Création d'une nouvelle demande"""
    
    if request.method == 'GET':
        frontend_url = getattr(settings, 'FRONTEND_URL', None)
        logger.debug("Resolved frontend URL for request list: %s", frontend_url)

        queryset = get_purchase_requests_queryset_for_user(request.user).select_related(
            'user',
            'rejected_by',
        )

        # Filtres dynamiques
        query_params = request.query_params
        status_filter = query_params.get('status')
        urgency_filter = query_params.get('urgency')
        search_term = query_params.get('search')
        created_by = query_params.get('created_by')
        date_from = query_params.get('date_from')
        date_to = query_params.get('date_to')
        min_amount = query_params.get('min_amount')
        max_amount = query_params.get('max_amount')

        if status_filter:
            if status_filter == 'in_progress':
                queryset = queryset.filter(status__in=['mg_approved', 'accounting_reviewed'])
            else:
                queryset = queryset.filter(status=status_filter)

        if urgency_filter:
            queryset = queryset.filter(urgency=urgency_filter)

        if created_by:
            if created_by == 'me':
                queryset = queryset.filter(user=request.user)
            elif created_by.isdigit():
                queryset = queryset.filter(user_id=int(created_by))

        if search_term:
            queryset = queryset.filter(
                Q(item_description__icontains=search_term) |
                Q(justification__icontains=search_term) |
                Q(user__username__icontains=search_term) |
                Q(user__first_name__icontains=search_term) |
                Q(user__last_name__icontains=search_term)
            )

        if date_from:
            try:
                start = datetime.strptime(date_from, "%Y-%m-%d")
                queryset = queryset.filter(created_at__date__gte=start.date())
            except ValueError:
                logger.warning("Invalid date_from format received: %s", date_from)

        if date_to:
            try:
                end = datetime.strptime(date_to, "%Y-%m-%d")
                queryset = queryset.filter(created_at__date__lte=end.date())
            except ValueError:
                logger.warning("Invalid date_to format received: %s", date_to)

        def _apply_amount_filter(value, lookup, qs):
            try:
                amount = Decimal(value)
                return qs.filter(**{lookup: amount})
            except (InvalidOperation, TypeError):
                logger.warning("Invalid amount filter provided: %s", value)
                return qs

        if min_amount:
            queryset = _apply_amount_filter(min_amount, 'estimated_cost__gte', queryset)
        if max_amount:
            queryset = _apply_amount_filter(max_amount, 'estimated_cost__lte', queryset)

        ordering = query_params.get('ordering', '-created_at')
        allowed_ordering = {'created_at', '-created_at', 'estimated_cost', '-estimated_cost', 'urgency', '-urgency'}
        if ordering not in allowed_ordering:
            ordering = '-created_at'

        queryset = queryset.order_by(ordering)
        
        paginator = PageNumberPagination()
        try:
            page_size = int(query_params.get('page_size', 20))
        except (TypeError, ValueError):
            page_size = 20
        paginator.page_size = max(1, min(page_size, 100))
        page = paginator.paginate_queryset(queryset, request)
        
        serializer = PurchaseRequestListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
    
    elif request.method == 'POST':
        if request.user.role not in ['employee', 'mg']:
            return Response(
                {'error': 'Seuls les employés et les managers peuvent créer des demandes'}, 
                status=status.HTTP_403_FORBIDDEN
            )

        auto_validate_mg = request.data.get('auto_validate_mg', False)
        logger.debug(
            "Purchase request creation by %s (%s) with auto_validate_mg=%s",
            request.user.username,
            request.user.role,
            auto_validate_mg,
        )
        
        serializer = PurchaseRequestCreateSerializer(data=request.data)
        if serializer.is_valid():
            purchase_request = serializer.save(user=request.user)
            
            if auto_validate_mg and request.user.role == 'mg':
                logger.info(
                    "Auto-validating request %s for MG user %s",
                    purchase_request.id,
                    request.user.username,
                )
                
                purchase_request.status = 'mg_approved'
                purchase_request.mg_validated_by = request.user
                purchase_request.mg_validated_at = timezone.now()
                purchase_request.save()
                
                RequestStep.objects.create(
                    request=purchase_request,
                    user=request.user,
                    action='approved',
                    comment="Auto-validé par le créateur (Moyens Généraux)"
                )
                
                logger.debug(
                    "Request %s auto-validated; new status %s",
                    purchase_request.id,
                    purchase_request.status,
                )
               
            try:
                
                email_service = EmailService()
                
                if request.user.role == 'mg' and auto_validate_mg:
                    recipients_role = 'accounting'
                    logger.info(f"Envoi de notification à la comptabilité pour la demande #{purchase_request.id} (auto-validée par MG)")
                else:
                    recipients_role = 'mg'
                    logger.info(f"Envoi de notification aux Moyens Généraux pour la demande #{purchase_request.id}")
                
                email_sent = email_service.send_purchase_request_notification(
                    purchase_request=purchase_request,
                    recipients_role=recipients_role
                )
                
                if email_sent:
                    logger.info(f"Notification envoyée avec succès pour la demande #{purchase_request.id}")
                else:
                    logger.warning(f"Échec de l'envoi de notification pour la demande #{purchase_request.id}")
                    
            except Exception as e:
                logger.error(f"Erreur lors de l'envoi de notification pour la demande #{purchase_request.id}: {str(e)}")
         
            detail_serializer = PurchaseRequestDetailSerializer(purchase_request)
            return Response(detail_serializer.data, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def purchase_request_detail(request, pk):
    """Détail d'une demande d'achat"""
    purchase_request = get_object_or_404(PurchaseRequest, pk=pk)
    
    user_role = request.user.role
    
    if user_role == 'employee' and purchase_request.user != request.user:
        return Response(
            {'error': 'Vous ne pouvez voir que vos propres demandes'}, 
            status=status.HTTP_403_FORBIDDEN
        )
    
    serializer = PurchaseRequestDetailSerializer(purchase_request)
    return Response(serializer.data)



@api_view(['POST'])
@permission_classes([IsAuthenticated])
def validate_request(request, pk):
    """Valider ou rejeter une demande selon le rôle"""
    purchase_request = get_object_or_404(PurchaseRequest, pk=pk)
    user_role = request.user.role
    
    action = request.data.get('action')
    comment = request.data.get('comment', '')

    logger.debug(
        "Validation action received: action=%s, comment=%s, user=%s(%s), request_status=%s",
        action,
        comment,
        request.user.username,
        user_role,
        purchase_request.status,
    )
    
    if action not in {'approve', 'reject'}:
        return Response(
            {'error': f"Action '{action}' non supportée"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    allowed_statuses = ALLOWED_STATUS_BY_ROLE.get(user_role, set())

    if purchase_request.status not in allowed_statuses:
        logger.warning(
            "Refus de validation: user=%s (%s), action=%s, status=%s",
            request.user.username,
            user_role,
            action,
            purchase_request.status,
        )
        return Response(
            {'error': f'Vous ne pouvez pas agir sur cette demande à cette étape. Status: {purchase_request.status}, Role: {user_role}'}, 
            status=status.HTTP_403_FORBIDDEN
        )
    
    serializer = ValidateRequestSerializer(data=request.data, context={'request': request})
    if serializer.is_valid():
        action = serializer.validated_data['action']
        comment = serializer.validated_data.get('comment', '')
        budget_available = serializer.validated_data.get('budget_available')
        final_cost = serializer.validated_data.get('final_cost')
        
        
        if action == 'reject':
            purchase_request.status = 'rejected'
            purchase_request.rejection_reason = comment
            purchase_request.rejected_by = request.user
            purchase_request.rejected_at = timezone.now()
            purchase_request.rejected_by_role = user_role
            
        else:  
            if user_role == 'mg':
                    purchase_request.status = 'mg_approved'
                    purchase_request.mg_validated_by = request.user  
                    purchase_request.mg_validated_at = timezone.now() 
                    
                    if final_cost is not None:
                        purchase_request.final_cost = final_cost 
            elif user_role == 'accounting':
                    purchase_request.status = 'accounting_reviewed'
                    purchase_request.budget_available = budget_available
                    purchase_request.accounting_validated_by = request.user  
                    purchase_request.accounting_validated_at = timezone.now()
                      
                    if final_cost is not None:
                        purchase_request.final_cost = final_cost
            elif user_role == 'director':
                    purchase_request.status = 'director_approved'
                    purchase_request.approved_by = request.user  
                    purchase_request.approved_at = timezone.now()
        
        purchase_request.save()
        
        RequestStep.objects.create(
            request=purchase_request,
            user=request.user,
            action='approved' if action == 'approve' else 'rejected',
            comment=comment,
            budget_check=budget_available if user_role == 'accounting' else None
        )

        UserActivity.objects.create(
            user=purchase_request.user,
            performed_by=request.user,
            action='updated',
            details={
                'request_id': purchase_request.id,
                'action': action,
                'status': purchase_request.status
            },
            ip_address=get_client_ip(request)
        )
        
        detail_serializer = PurchaseRequestDetailSerializer(purchase_request)
        return Response(detail_serializer.data)
    else:
        logger.warning("Validation errors while processing request %s: %s", purchase_request.id, serializer.errors)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_rejection_reason(request, pk):
    """Modifier le motif de refus"""
    purchase_request = get_object_or_404(PurchaseRequest, pk=pk)
    
    if purchase_request.status != 'rejected':
        return Response({'error': 'Cette demande n\'est pas refusée'}, status=400)
    
    if purchase_request.rejected_by != request.user:
        return Response({'error': 'Vous ne pouvez modifier que vos propres refus'}, status=403)
    
    new_comment = request.data.get('comment', '').strip()
    if not new_comment:
        return Response({'error': 'Le commentaire ne peut pas être vide'}, status=400)
    
    purchase_request.rejection_reason = new_comment
    purchase_request.save()
    
    return Response(PurchaseRequestDetailSerializer(purchase_request).data)


from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.conf import settings
import os
import uuid
import logging
from .models import Attachment, PurchaseRequest
from .serializers import AttachmentSerializer

logger = logging.getLogger(__name__)

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def attachments_list(request):
    """Liste des pièces jointes + Upload d'un nouveau fichier"""
    
    if request.method == 'GET':
        request_id = request.query_params.get('request_id')
        if request_id:
            attachments = Attachment.objects.filter(request_id=request_id)
        else:
            attachments = Attachment.objects.all()
        
        serializer = AttachmentSerializer(attachments, many=True, context={'request': request})
        return Response(serializer.data)
    
    elif request.method == 'POST':
        if 'file' not in request.FILES:
            return Response({'error': "Le fichier est requis"}, status=400)
        
        if 'request' not in request.data:
            return Response({'error': "Le champ 'request' est requis"}, status=400)

        uploaded_file = request.FILES['file']

        if uploaded_file.size > 10 * 1024 * 1024:
            return Response({'error': 'Fichier trop volumineux (max 10MB)'}, status=400)

        allowed_types = ['application/pdf', 'image/jpeg', 'image/png', 'image/jpg']
        if uploaded_file.content_type not in allowed_types:
            return Response({'error': f"Format non supporté: {uploaded_file.content_type}"}, status=400)

        request_id = request.data.get('request')
        try:
            purchase_request = get_object_or_404(PurchaseRequest, pk=request_id)
        except Exception as e:
            return Response({'error': f"Demande introuvable: {str(e)}"}, status=400)

        # Vérification des permissions d'upload
        can_upload = False
        if (purchase_request.user == request.user and 
            purchase_request.status not in ['rejected', 'director_approved']):
            can_upload = True
        elif (request.user.role == 'mg' and 
            purchase_request.status in ['pending', 'mg_approved']):
            can_upload = True

        if not can_upload:
            return Response({'error': 'Vous ne pouvez pas ajouter de pièce jointe à cette étape'}, status=403)

        try:
            if getattr(settings, 'SUPABASE_ENABLED', False):
                return handle_supabase_upload(request, uploaded_file, purchase_request)
            return handle_local_upload(request, uploaded_file, purchase_request)
        except Exception as e:
            logger.exception("Erreur lors de l'upload pour la demande %s", request_id)
            return Response({'error': f'Erreur lors de l\'upload: {str(e)}'}, status=500)


def handle_supabase_upload(request, uploaded_file, purchase_request):
    """Gestion de l'upload via Supabase Storage."""
    if not getattr(settings, 'SUPABASE_ENABLED', False):
        raise SupabaseStorageError("Supabase Storage est désactivé.")

    try:
        folder = settings.SUPABASE_FOLDER or 'attachments'
        extension = os.path.splitext(uploaded_file.name)[1]
        unique_filename = f"{uuid.uuid4()}{extension}"
        storage_path = f"{folder}/{purchase_request.id}/{unique_filename}"

        service = SupabaseStorageService()
        public_url = service.upload(
            uploaded_file,
            storage_path,
            content_type=getattr(uploaded_file, 'content_type', None)
        )

        provided_type = request.data.get('file_type')
        content_type = getattr(uploaded_file, 'content_type', '') or ''
        if provided_type:
            file_type = provided_type
        elif content_type == 'application/pdf':
            file_type = 'pdf'
        elif content_type.startswith('image/'):
            file_type = content_type.split('/')[-1]
        else:
            file_type = 'other'

        attachment = Attachment.objects.create(
            file_url=public_url,
            file_type=file_type,
            request=purchase_request,
            uploaded_by=request.user,
            description=request.data.get('description', ''),
            storage_public_id=storage_path,
            storage_resource_type='supabase',
            file_size=uploaded_file.size,
            mime_type=content_type,
        )

        logger.info("Fichier uploadé sur Supabase: %s", storage_path)
        return Response(
            AttachmentSerializer(attachment, context={'request': request}).data,
            status=201,
        )
    except SupabaseStorageError as exc:
        logger.error("Erreur Supabase Storage: %s", exc)
        return Response({'error': str(exc)}, status=500)


def handle_local_upload(request, uploaded_file, purchase_request):
    """Gestion de l'upload en stockage local"""
    try:
        request_id = str(purchase_request.id)
        file_type = request.data.get('file_type', 'other')
        description = request.data.get('description', '')
        
        # Créer le dossier de destination
        upload_dir = os.path.join(settings.MEDIA_ROOT, 'attachments', request_id)
        os.makedirs(upload_dir, exist_ok=True)
        
        # Générer un nom de fichier unique
        original_name = uploaded_file.name
        file_extension = os.path.splitext(original_name)[1]
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        file_path = os.path.join(upload_dir, unique_filename)
        
        # Sauvegarder le fichier
        with open(file_path, 'wb+') as destination:
            for chunk in uploaded_file.chunks():
                destination.write(chunk)
        
        # URL relative pour le frontend
        relative_url = f"/media/attachments/{request_id}/{unique_filename}"
        
        # Déterminer le type de fichier pour le stockage local
        content_type = uploaded_file.content_type
        provided_type = request.data.get('file_type')
        if provided_type:
            file_type_display = provided_type
        elif content_type == 'application/pdf':
            file_type_display = 'pdf'
        elif content_type.startswith('image/'):
            file_type_display = content_type.split('/')[1]  # jpeg, png, etc.
        else:
            file_type_display = 'other'
        
        attachment = Attachment.objects.create(
            file_url=relative_url,
            file_type=file_type_display,
            request=purchase_request,
            uploaded_by=request.user,
            description=description,
            storage_resource_type='local',
            file_size=uploaded_file.size,
            mime_type=uploaded_file.content_type,
        )

        logger.info(f"Fichier uploadé localement: {file_path}")
        return Response(AttachmentSerializer(attachment, context={'request': request}).data, status=201)
        
    except Exception as e:
        logger.error(f"Erreur upload local: {str(e)}", exc_info=True)
        return Response(
            {'error': f'Échec de l\'upload local: {str(e)}'}, 
            status=500
        )
@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def attachment_delete(request, pk):
    """Supprimer une pièce jointe"""
    attachment = get_object_or_404(Attachment, pk=pk)
    
    can_delete = False
    
    if (attachment.request.user == request.user and 
        attachment.uploaded_by == request.user and 
        attachment.request.status not in ['rejected', 'director_approved']):
        can_delete = True
    
    elif (request.user.role == 'mg' and 
          attachment.request.status in ['pending', 'mg_approved']):
        can_delete = True
    
    elif request.user.is_staff:
        can_delete = True
    
    if not can_delete:
        return Response(
            {'error': 'Vous ne pouvez pas supprimer cette pièce jointe à cette étape'}, 
            status=status.HTTP_403_FORBIDDEN
        )

    # Supprimer le fichier du stockage externe si nécessaire
    if getattr(settings, 'SUPABASE_ENABLED', False) and attachment.storage_public_id:
        try:
            SupabaseStorageService().delete(attachment.storage_public_id)
        except SupabaseStorageError as exc:
            logger.warning("Suppression Supabase impossible: %s", exc)
        except Exception as exc:  # pragma: no cover
            logger.warning("Erreur lors de la suppression Supabase: %s", exc, exc_info=True)
    elif attachment.file_url and attachment.file_url.startswith('/media/'):
        local_path = os.path.join(str(settings.BASE_DIR), attachment.file_url.lstrip('/'))
        if os.path.exists(local_path):
            try:
                os.remove(local_path)
            except OSError as exc:  # pragma: no cover
                logger.warning("Impossible de supprimer le fichier local %s: %s", local_path, exc)

    attachment.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard(request):
    """Données détaillées pour le tableau de bord (flow, files d'attente, performance)"""
    user = request.user
    user_role = user.role
    user_id = user.id

    ROLE_QUEUE_STATUS = {
        'employee': ['pending', 'mg_approved', 'accounting_reviewed'],
        'mg': ['pending'],
        'accounting': ['mg_approved'],
        'director': ['accounting_reviewed'],
    }
    ROLE_VALIDATED_FIELD = {
        'mg': 'mg_validated_by',
        'accounting': 'accounting_validated_by',
        'director': 'approved_by',
    }
    ROLE_DURATION_FIELDS = {
        'mg': ('created_at', 'mg_validated_at'),
        'accounting': ('mg_validated_at', 'accounting_validated_at'),
        'director': ('accounting_validated_at', 'approved_at'),
    }
    ROLE_LABELS = {
        'employee': 'Employés',
        'mg': 'Moyens Généraux',
        'accounting': 'Comptabilité',
        'director': 'Direction',
    }

    now = timezone.now()
    today = timezone.localdate()

    all_requests = PurchaseRequest.objects.select_related(
        'user',
        'rejected_by',
        'mg_validated_by',
        'accounting_validated_by',
        'approved_by'
    ).prefetch_related('steps')

    owned_requests = all_requests.filter(user=user)

    # Helper pour sélectionner les demandes visibles selon le rôle
    if user_role == 'employee':
        visible_requests = owned_requests
    else:
        visible_requests = get_role_specific_requests(all_requests, user_role, user_id)
        if visible_requests is None:
            visible_requests = all_requests.none()

    queue_statuses = ROLE_QUEUE_STATUS.get(user_role, [])
    if user_role == 'employee':
        queue_queryset = owned_requests.filter(status__in=queue_statuses)
    else:
        queue_queryset = all_requests.filter(status__in=queue_statuses)

    validated_field = ROLE_VALIDATED_FIELD.get(user_role)
    if validated_field:
        my_validated = all_requests.filter(**{validated_field: user})
    else:
        my_validated = PurchaseRequest.objects.none()

    my_rejections = all_requests.filter(rejected_by=user, rejected_by_role=user_role)

    def serialize_actor(actor, timestamp):
        if not actor or not timestamp:
            if actor:
                return {
                    'id': actor.id,
                    'name': actor.get_full_name() or actor.username,
                    'performed_at': None,
                }
            return None
        return {
            'id': actor.id,
            'name': actor.get_full_name() or actor.username,
            'performed_at': timezone.localtime(timestamp),
        }

    def serialize_step(step):
        if not step:
            return None
        return {
            'id': step.id,
            'action': step.action,
            'action_display': step.get_action_display(),
            'comment': step.comment,
            'performed_at': timezone.localtime(step.created_at),
            'performed_by': {
                'id': step.user_id,
                'name': step.user.get_full_name() or step.user.username,
                'role': step.user.role,
            },
            'request_id': step.request_id,
            'request_description': step.request.item_description,
            'request_status': step.request.status,
            'request_status_display': step.request.get_status_display(),
        }

    def serialize_request_card(obj):
        steps = list(obj.steps.all())
        latest_step = steps[0] if steps else None
        amount = obj.final_cost or obj.estimated_cost or Decimal('0')
        return {
            'id': obj.id,
            'item_description': obj.item_description,
            'status': obj.status,
            'status_display': obj.get_status_display(),
            'current_step': obj.current_step,
            'urgency': obj.urgency,
            'urgency_display': obj.get_urgency_display(),
            'amount': float(amount),
            'requested_by': {
                'id': obj.user_id,
                'name': obj.user.get_full_name() or obj.user.username,
                'department': obj.user.department,
            },
            'submitted_at': obj.created_at,
            'waiting_days': max((now - obj.created_at).days, 0),
            'actors': {
                'mg': serialize_actor(obj.mg_validated_by, obj.mg_validated_at),
                'accounting': serialize_actor(obj.accounting_validated_by, obj.accounting_validated_at),
                'director': serialize_actor(obj.approved_by, obj.approved_at),
                'rejected': serialize_actor(obj.rejected_by, obj.rejected_at),
            },
            'last_action': serialize_step(latest_step),
        }

    queue_details = [
        serialize_request_card(req) for req in queue_queryset.order_by('created_at')[:8]
    ]
    flow_snapshot = [
        serialize_request_card(req) for req in visible_requests.order_by('-created_at')[:5]
    ]

    if user_role == 'employee':
        recent_actions_qs = RequestStep.objects.select_related('request', 'user').filter(
            request__user=user
        ).order_by('-created_at')[:6]
    else:
        recent_actions_qs = RequestStep.objects.select_related('request', 'user').filter(
            user=user
        ).order_by('-created_at')[:6]

    recent_actions = [serialize_step(step) for step in recent_actions_qs]

    def get_period_stats(months_offset=0, user_filter=None):
        now = timezone.now()
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - relativedelta(months=months_offset)
        if months_offset > 0:
            end_date = start_date + relativedelta(months=1) - timedelta(seconds=1)
        else:
            end_date = now

        if user_filter:
            period_requests = user_filter.filter(
                created_at__gte=start_date,
                created_at__lte=end_date
            )
        else:
            period_requests = all_requests.filter(
                created_at__gte=start_date,
                created_at__lte=end_date
            )
            
        approved_queryset = period_requests.filter(status='director_approved')
        
        total_amount = approved_queryset.annotate(
            cost_to_use = Case(
            When(final_cost__isnull=False, then=F('final_cost')),  # Priorité au final_cost
            When(final_cost__isnull=True, estimated_cost__isnull=False, then=F('estimated_cost')),
            default=Value(0),
            output_field=DecimalField(max_digits=12, decimal_places=2)
        )
        ).aggregate(total=Sum('cost_to_use'))['total'] or 0
        
        approved_count = approved_queryset.count()
        total_requests = period_requests.count()
        
        
        
        return {
            'total_requests': total_requests,
            'approved_requests': approved_count,
            'in_progress': period_requests.filter(
                status__in=['mg_approved', 'accounting_reviewed']
            ).count(),
            # 'total_amount': approved_requests.aggregate(
            #     total=Sum('estimated_cost')
            # )['total'] or 0,
            'total_amount': total_amount,
            'validation_rate': (
                approved_count / total_requests * 100
                if total_requests > 0 else 0
            )
        }
    
    def calculate_trend(current, previous):
        """Calculer la tendance en pourcentage avec gestion des cas limites"""
        if previous == 0 and current == 0:
            return {'value': 0, 'direction': 'neutral'}
        elif previous == 0:
            return {'value': 100, 'direction': 'up'}
        
        percent_change = ((current - previous) / previous) * 100
        rounded_change = round(percent_change)
        
        if rounded_change == 0 and current != previous:
            rounded_change = 1 if percent_change > 0 else -1
        
        return {
            'value': abs(rounded_change),
            'direction': 'up' if rounded_change > 0 else 'down' if rounded_change < 0 else 'neutral'
        }
    
    def calculate_processing_delays():
        """Calculer les délais moyens de traitement"""
        approved_requests = all_requests.filter(status='director_approved')
        
        if not approved_requests.exists():
            return {
                'average': 0,
                'mg_validation': 0,
                'accounting_review': 0,
                'director_approval': 0
            }
        
        delays = []
        for req in approved_requests:
            if req.updated_at and req.created_at:
                delta = req.updated_at - req.created_at
                delays.append(delta.days)
        
        if delays:
            avg_delay = sum(delays) / len(delays)
            return {
                'average': round(avg_delay, 1),
                'mg_validation': round(avg_delay * 0.3, 1),
                'accounting_review': round(avg_delay * 0.4, 1),
                'director_approval': round(avg_delay * 0.3, 1)
            }
        
        return {
            'average': 0,
            'mg_validation': 0,
            'accounting_review': 0,
            'director_approval': 0
        }
    
    def get_department_stats():
        """Obtenir les statistiques par département avec fallback"""
        try:
            dept_stats = all_requests.values('user__department').annotate(
                count=Count('id')
            ).exclude(user__department__isnull=True).exclude(user__department='').order_by('-count')
            
            result = []
            for stat in dept_stats:
                dept_name = stat.get('user__department') or 'Département inconnu'
                if dept_name and dept_name.strip():
                    result.append({
                        'department': dept_name,
                        'requests_count': stat['count']
                    })
            
            if not result:
                user_stats = all_requests.values('user__first_name', 'user__last_name').annotate(
                    count=Count('id')
                ).order_by('-count')[:5]
                
                for stat in user_stats:
                    full_name = f"{stat['user__first_name'] or ''} {stat['user__last_name'] or ''}".strip()
                    if not full_name:
                        full_name = "Utilisateur inconnu"
                    
                    result.append({
                        'department': full_name,
                        'requests_count': stat['count']
                    })
            
            return result[:5]  
            
        except Exception as e:
            logger.error("Erreur dans get_department_stats: %s", e, exc_info=True)
            return [{
                'department': 'Données indisponibles',
                'requests_count': all_requests.count()
            }]

    
    global_current_period = get_period_stats(0)
    global_previous_period = get_period_stats(1)
    current_period = get_period_stats(0, visible_requests)
    previous_period = get_period_stats(1, visible_requests)

    owned_active = owned_requests.exclude(status__in=['director_approved', 'rejected'])

    overview = {
        'role': user_role,
        'owned_requests': owned_requests.count(),
        'awaiting_feedback': owned_active.count(),
        'approved_owned': owned_requests.filter(status='director_approved').count(),
        'rejected_owned': owned_requests.filter(status='rejected').count(),
        'total_visible': visible_requests.count(),
        'awaiting_my_action': queue_queryset.count(),
        'validated_by_me': my_validated.count(),
        'rejected_by_me': my_rejections.count(),
    }

    def compute_avg_handle_time(role):
        start_field, end_field = ROLE_DURATION_FIELDS.get(role, (None, None))
        if not start_field or not end_field:
            return 0
        qs = all_requests.exclude(**{f"{start_field}__isnull": True}).exclude(**{f"{end_field}__isnull": True})
        durations = []
        for req in qs:
            start_value = getattr(req, start_field)
            end_value = getattr(req, end_field)
            if start_value and end_value and end_value > start_value:
                durations.append((end_value - start_value).total_seconds())
        if not durations:
            return 0
        return round((sum(durations) / len(durations)) / 86400, 2)

    if queue_queryset.exists():
        oldest_queue = queue_queryset.order_by('created_at').first()
        oldest_wait = max((now - oldest_queue.created_at).days, 0)
    else:
        oldest_wait = 0

    performance_block = {
        'avg_handle_time_days': compute_avg_handle_time(user_role),
        'actions_last_30_days': RequestStep.objects.filter(
            (Q(user=user) if user_role != 'employee' else Q(request__user=user)),
            created_at__gte=now - timedelta(days=30)
        ).count(),
        'queue_oldest_waiting_days': oldest_wait,
        'queue_size': queue_queryset.count(),
    }

    budget_block = {
        'current_total': global_current_period['total_amount'],
        'previous_total': global_previous_period['total_amount'],
        'trend': calculate_trend(
            global_current_period['total_amount'],
            global_previous_period['total_amount']
        ),
        'validation_rate': round(global_current_period['validation_rate']),
    }

    team_activity = {}
    for role_key, statuses in ROLE_QUEUE_STATUS.items():
        members_count = User.objects.filter(role=role_key, is_active=True).count()
        awaiting = all_requests.filter(status__in=statuses).count()
        validated_today = RequestStep.objects.filter(
            action='approved',
            user__role=role_key,
            created_at__date=today
        ).count()
        team_activity[role_key] = {
            'label': ROLE_LABELS.get(role_key, role_key.title()),
            'members': members_count,
            'awaiting': awaiting,
            'validated_today': validated_today,
        }

    def get_monthly_stats(scope_queryset):
        monthly_stats = []
        for i in range(6):
            date = timezone.now() - timedelta(days=30 * i)
            month_start = date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
            queryset = scope_queryset if scope_queryset is not None else all_requests
            month_count = queryset.filter(
                created_at__gte=month_start,
                created_at__lte=month_end
            ).count()
            monthly_stats.append({
                'month': date.strftime('%B %Y'),
                'count': month_count
            })
        monthly_stats.reverse()
        return monthly_stats

    if user_role == 'employee':
        status_source = owned_requests
        monthly_scope = owned_requests
    else:
        status_source = all_requests
        monthly_scope = all_requests

    requests_by_status = {
        'pending': status_source.filter(status='pending').count(),
        'mg_approved': status_source.filter(status='mg_approved').count(),
        'accounting_reviewed': status_source.filter(status='accounting_reviewed').count(),
        'director_approved': status_source.filter(status='director_approved').count(),
        'rejected': status_source.filter(status='rejected').count()
    }

    recent_requests_serializer = PurchaseRequestListSerializer(
        visible_requests.order_by('-created_at')[:10], many=True
    ).data

    if user_role in ['mg', 'director']:
        all_requests_payload = PurchaseRequestListSerializer(
            all_requests.order_by('-created_at'), many=True
        ).data
    else:
        all_requests_payload = PurchaseRequestListSerializer(
            visible_requests.order_by('-created_at'), many=True
        ).data

    trends = {
        'requests': calculate_trend(
            current_period['total_requests'], previous_period['total_requests']
        ),
        'approved': calculate_trend(
            current_period['approved_requests'], previous_period['approved_requests']
        ),
        'amount': calculate_trend(
            current_period['total_amount'], previous_period['total_amount']
        ),
    }

    data = {
        'overview': overview,
        'queue': queue_details,
        'recent_actions': recent_actions,
        'team_activity': team_activity,
        'performance': performance_block,
        'flow_snapshot': flow_snapshot,
        'budget': budget_block,
        'current_period_stats': current_period,
        'previous_period_stats': previous_period,
        'recent_requests': recent_requests_serializer,
        'all_requests': all_requests_payload,
        'monthly_stats': get_monthly_stats(monthly_scope),
        'requests_by_status': requests_by_status,
        'trends': trends,
        'queue_insights': {
            'awaiting': queue_queryset.count(),
            'oldest_waiting_days': oldest_wait,
        },
        'department_stats': get_department_stats(),
        'processing_delays': calculate_processing_delays(),
        'user_requests_count': owned_requests.count(),
    }

    # Compatibilité avec les anciens champs utilisés sur le front
    data['total_requests'] = status_source.count()
    data['pending_requests'] = requests_by_status['pending']
    data['in_progress_requests'] = requests_by_status['mg_approved'] + requests_by_status['accounting_reviewed']
    data['approved_requests'] = requests_by_status['director_approved']
    data['rejected_requests'] = requests_by_status['rejected']
    data['mes_demandes'] = overview['total_visible']
    data['en_cours'] = overview['awaiting_my_action']
    data['acceptees'] = overview['validated_by_me']
    data['refusees'] = overview['rejected_by_me']

    logger.debug(
        "Dashboard data compiled for %s (ID %s): total=%s, status_breakdown=%s",
        user_role,
        user_id,
        data.get('total_requests', overview['total_visible']),
        data.get('requests_by_status'),
    )

    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def debug_auth(request):
    """Vue de debug pour tester l'authentification"""
    
    debug_info = {
        'user': {
            'id': request.user.id,
            'username': request.user.username,
            'email': request.user.email,
            'is_authenticated': request.user.is_authenticated,
        },
        'cookies_received': dict(request.COOKIES),
        'headers': {
            'authorization': request.META.get('HTTP_AUTHORIZATION', 'Non trouvé'),
            'user_agent': request.META.get('HTTP_USER_AGENT', 'Non trouvé'),
        },
        'method': request.method,
        'path': request.path,
    }
    
    logger.debug(
        "DEBUG AUTH - user=%s, cookies=%s, authorization=%s",
        request.user,
        list(request.COOKIES.keys()),
        request.META.get('HTTP_AUTHORIZATION'),
    )
    
    return Response(debug_info, status=status.HTTP_200_OK)

from rest_framework_simplejwt.tokens import UntypedToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

@api_view(['GET'])
@permission_classes([AllowAny])  
def test_cookies(request):
    """Vue de test pour diagnostiquer les cookies"""
    
    cookies = dict(request.COOKIES)
    access_token = request.COOKIES.get('access_token')
    refresh_token = request.COOKIES.get('refresh_token')
    
    result = {
        'cookies_count': len(cookies),
        'all_cookies': cookies,
        'has_access_token': bool(access_token),
        'has_refresh_token': bool(refresh_token),
        'authorization_header': request.META.get('HTTP_AUTHORIZATION', 'Absent'),
        'user_agent': request.META.get('HTTP_USER_AGENT', 'Non trouvé'),
        'host': request.META.get('HTTP_HOST', 'Non trouvé'),
        'origin': request.META.get('HTTP_ORIGIN', 'Non trouvé'),
        'referer': request.META.get('HTTP_REFERER', 'Non trouvé'),
    }
    
    if access_token:
        try:
            validated_token = UntypedToken(access_token)
            user_id = validated_token.get('user_id')
            user = User.objects.get(id=user_id)
            
            result['token_valid'] = True
            result['token_user'] = {
                'id': user.id,
                'username': user.username,
                'email': user.email
            }
            result['token_claims'] = dict(validated_token.payload)
            
        except (TokenError, InvalidToken, User.DoesNotExist) as e:
            result['token_valid'] = False
            result['token_error'] = str(e)
            result['access_token_preview'] = access_token[:50] + "..." if len(access_token) > 50 else access_token
    else:
        result['token_valid'] = False
        result['token_error'] = "Pas de token access_token dans les cookies"
    
    logger.debug(
        "TEST COOKIES - count=%s, access_token=%s, refresh_token=%s",
        len(cookies),
        bool(access_token),
        bool(refresh_token),
    )
    
    return Response(result, status=status.HTTP_200_OK)

@api_view(['GET'])
@permission_classes([AllowAny])
def test_auth_simple(request):
    """Test simple d'authentification"""
    
    access_token = request.COOKIES.get('access_token')
    
    if not access_token:
        return Response({
            'authenticated': False,
            'error': 'Pas de token dans les cookies',
            'cookies': dict(request.COOKIES)
        })
    
    try:
        from rest_framework_simplejwt.tokens import UntypedToken
        validated_token = UntypedToken(access_token)
        user_id = validated_token.get('user_id')
        user = User.objects.get(id=user_id)
        
        return Response({
            'authenticated': True,
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name
            }
        })
        
    except Exception as e:
        return Response({
            'authenticated': False,
            'error': str(e),
            'token_preview': access_token[:50] + "..."
        })

@api_view(['GET'])
@permission_classes([AllowAny])
def test_set_cookie(request):
    """Vue de test pour vérifier si les cookies peuvent être définis"""
    
    response = Response({
        'message': 'Test cookie set',
        'timestamp': str(request.META.get('HTTP_HOST', 'unknown')),
        'origin': request.META.get('HTTP_ORIGIN', 'No origin'),
        'user_agent': request.META.get('HTTP_USER_AGENT', 'No user agent')[:100],
    })
    
    response.set_cookie(
        'test_cookie_simple',
        'simple_value',
        max_age=300
    )
    
    response.set_cookie(
        'test_cookie_httponly',
        'httponly_value',
        max_age=300,
        httponly=True
    )
    
    response.set_cookie(
        'test_cookie_full',
        'full_config_value',
        max_age=300,
        httponly=True,
        secure=False,
        samesite='Lax',
        path='/'
    )
    
    logger.debug(
        "TEST SET COOKIE - origin=%s, host=%s, user_agent=%s",
        request.META.get('HTTP_ORIGIN', 'None'),
        request.META.get('HTTP_HOST', 'None'),
        request.META.get('HTTP_USER_AGENT', 'None')[:50],
    )
    
    return response

@api_view(['GET'])
@permission_classes([AllowAny])
def test_get_cookies(request):
    """Vue pour lire les cookies reçus"""
    
    cookies = dict(request.COOKIES)
    
    logger.debug("TEST GET COOKIES - cookies=%s", list(cookies.keys()))
    
    return Response({
        'cookies_received': cookies,
        'cookies_count': len(cookies),
        'has_test_cookies': {
            'simple': 'test_cookie_simple' in cookies,
            'httponly': 'test_cookie_httponly' in cookies,
            'full': 'test_cookie_full' in cookies,
        }
    })
