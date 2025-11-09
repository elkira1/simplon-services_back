from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from services.supabase_storage_service import (
    SupabaseStorageError,
    SupabaseStorageService,
)

from .models import (
    Attachment,
    CustomUser,
    PasswordResetCode,
    PurchaseRequest,
    RequestStep,
    UserActivity,
)

DEPARTMENT_CHOICES = [
    "Service Développement",
    "Service Communication",
    "Service Pédagogique",
    "Administration",
    "Comptabilité",
    "Ressources Humaines",
    "IT/Informatique",
    "Commercial",
    "Production",
    "Logistique",
    "Marketing",
    "Autre",
]


class CustomUserAdminForm(forms.ModelForm):
    department = forms.ChoiceField(
        choices=[("", "Sélectionner un département")] + [
            (dept, dept) for dept in DEPARTMENT_CHOICES
        ],
        required=False,
        label="Département",
    )

    class Meta:
        model = CustomUser
        fields = "__all__"


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    form = CustomUserAdminForm
    add_form = CustomUserAdminForm
    list_display = (
        'username',
        'email',
        'role',
        'department',
        'requests_count',
        'attachments_count',
        'is_active',
        'last_login',
    )
    list_filter = ('role', 'department', 'is_active', 'is_staff', 'date_joined')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    ordering = ('-date_joined',)
    list_per_page = 50
    autocomplete_fields = ('created_by',)
    readonly_fields = ('created_at', 'updated_at', 'last_login', 'date_joined')
    actions = ['activate_users', 'deactivate_users']

    fieldsets = UserAdmin.fieldsets + (
        (
            'Informations internes',
            {
                'fields': (
                    'role',
                    'department',
                    'phone',
                    'created_by',
                    'created_at',
                    'updated_at',
                )
            },
        ),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        (
            'Informations supplémentaires',
            {
                'fields': ('role', 'department', 'phone', 'created_by'),
            },
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('created_by').annotate(
            _requests_count=Count('requests', distinct=True),
            _attachments_count=Count('attachment', distinct=True),
        )

    @admin.display(description='Demandes')
    def requests_count(self, obj):
        return getattr(obj, '_requests_count', obj.requests.count())

    @admin.display(description='Pièces jointes')
    def attachments_count(self, obj):
        return getattr(obj, '_attachments_count', obj.attachment_set.count())

    @admin.action(description='Activer les utilisateurs sélectionnés')
    def activate_users(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(
            request,
            _('%d utilisateur(s) activé(s).') % updated,
            messages.SUCCESS,
        )

    @admin.action(description='Désactiver les utilisateurs sélectionnés')
    def deactivate_users(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(
            request,
            _('%d utilisateur(s) désactivé(s).') % updated,
            messages.WARNING,
        )


@admin.register(UserActivity)
class UserActivityAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'performed_by', 'timestamp', 'ip_address')
    list_filter = ('action', 'timestamp', 'performed_by__role')
    search_fields = ('user__username', 'performed_by__username', 'details')
    readonly_fields = ('timestamp',)
    list_select_related = ('user', 'performed_by')
    date_hierarchy = 'timestamp'

    def has_add_permission(self, request):
        return False  
    def has_change_permission(self, request, obj=None):
        return False  
    

@admin.register(PasswordResetCode)
class PasswordResetCodeAdmin(admin.ModelAdmin):
    list_display = ('user', 'code', 'created_at', 'expires_at', 'is_used', 'ip_address')
    list_filter = ('is_used', 'created_at')
    search_fields = ('user__username', 'code')
    readonly_fields = ('created_at', 'expires_at', 'code')
    list_select_related = ('user',)
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False  

    def has_change_permission(self, request, obj=None):
        return False



class RequestStepInline(admin.TabularInline):
    model = RequestStep
    extra = 0
    readonly_fields = ('created_at',)
    fields = ('user', 'action', 'comment', 'budget_check', 'created_at')
    autocomplete_fields = ('user',)
    show_change_link = True

class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0
    readonly_fields = ('created_at', 'file_size_mb', 'inline_download_link')
    fields = (
        'file_url',
        'file_type',
        'description',
        'uploaded_by',
        'file_size_mb',
        'inline_download_link',
        'created_at',
    )
    autocomplete_fields = ('uploaded_by',)
    show_change_link = True

    @admin.display(description='Ouvrir')
    def inline_download_link(self, obj):
        if obj.file_url:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener">Télécharger</a>',
                obj.file_url,
            )
        return '-'

@admin.register(PurchaseRequest)
class PurchaseRequestAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'user', 'item_description_short', 'estimated_cost', 
        'status_badge', 'urgency_badge', 'created_at'
    )
    list_filter = ('status', 'urgency', 'created_at', 'user__role')
    search_fields = ('item_description', 'user__username', 'justification')
    readonly_fields = ('created_at', 'updated_at', 'current_step')
    list_select_related = (
        'user',
        'mg_validated_by',
        'accounting_validated_by',
        'approved_by',
        'rejected_by',
    )
    autocomplete_fields = (
        'user',
        'mg_validated_by',
        'accounting_validated_by',
        'approved_by',
        'rejected_by',
    )
    list_per_page = 25
    save_on_top = True
    actions = [
        'set_pending',
        'set_mg_approved',
        'set_accounting_reviewed',
        'set_director_approved',
        'set_rejected',
    ]
    
    fieldsets = (
        ('Informations de base', {
            'fields': ('user', 'item_description', 'quantity', 'estimated_cost', 'urgency')
        }),
        ('Justification', {
            'fields': ('justification',)
        }),
        ('Workflow', {
            'fields': ('status', 'current_step', 'budget_available', 'final_cost')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    inlines = [RequestStepInline, AttachmentInline]
    
    def item_description_short(self, obj):
        return obj.item_description[:50] + "..." if len(obj.item_description) > 50 else obj.item_description
    item_description_short.short_description = "Description"
    
    def status_badge(self, obj):
        colors = {
            'pending': '#fbbf24',  # yellow
            'mg_approved': '#3b82f6',  # blue
            'accounting_reviewed': '#8b5cf6',  # purple
            'director_approved': '#10b981',  # green
            'rejected': '#ef4444'  # red
        }
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = "Statut"
    
    def urgency_badge(self, obj):
        colors = {
            'low': '#10b981',  # green
            'medium': '#f59e0b',  # amber
            'high': '#f97316',  # orange
            'critical': '#ef4444'  # red
        }
        color = colors.get(obj.urgency, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px;">{}</span>',
            color,
            obj.get_urgency_display()
        )
    urgency_badge.short_description = "Urgence"
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')

    def _bulk_status_update(self, request, queryset, status):
        updated = queryset.update(status=status)
        self.message_user(
            request,
            _('%d demande(s) mise(s) à jour.') % updated,
            level=messages.SUCCESS,
        )

    @admin.action(description="Marquer comme 'En attente'")
    def set_pending(self, request, queryset):
        self._bulk_status_update(request, queryset, 'pending')

    @admin.action(description="Marquer comme 'Validée MG'")
    def set_mg_approved(self, request, queryset):
        self._bulk_status_update(request, queryset, 'mg_approved')

    @admin.action(description="Marquer comme 'Étudiée comptabilité'")
    def set_accounting_reviewed(self, request, queryset):
        self._bulk_status_update(request, queryset, 'accounting_reviewed')

    @admin.action(description="Marquer comme 'Approuvée direction'")
    def set_director_approved(self, request, queryset):
        self._bulk_status_update(request, queryset, 'director_approved')

    @admin.action(description="Marquer comme 'Refusée'")
    def set_rejected(self, request, queryset):
        self._bulk_status_update(request, queryset, 'rejected')

@admin.register(RequestStep)
class RequestStepAdmin(admin.ModelAdmin):
    list_display = ('request_id', 'user', 'action', 'comment_short', 'created_at')
    list_filter = ('action', 'created_at', 'user__role')
    search_fields = ('request__item_description', 'user__username', 'comment')
    readonly_fields = ('created_at',)
    list_select_related = ('request', 'user')
    autocomplete_fields = ('request', 'user')
    date_hierarchy = 'created_at'
    
    def request_id(self, obj):
        return f"Demande #{obj.request.id}"
    request_id.short_description = "Demande"
    
    def comment_short(self, obj):
        return obj.comment[:50] + "..." if len(obj.comment) > 50 else obj.comment
    comment_short.short_description = "Commentaire"

@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'request_link',
        'description',
        'file_type',
        'uploaded_by',
        'file_size_mb',
        'storage_resource_type',
        'created_at',
        'admin_download_link',
    )
    list_filter = ('file_type', 'created_at', 'uploaded_by__role', 'storage_resource_type')
    search_fields = ('request__item_description', 'description', 'uploaded_by__username')
    readonly_fields = ('created_at', 'file_size_mb', 'admin_download_link')
    list_select_related = ('request', 'uploaded_by')
    autocomplete_fields = ('request', 'uploaded_by')
    actions = ['refresh_supabase_links']
    date_hierarchy = 'created_at'
    
    def request_link(self, obj):
        url = reverse('admin:core_purchaserequest_change', args=[obj.request_id])
        return format_html('<a href="{}">Demande #{}</a>', url, obj.request_id)
    request_link.short_description = "Demande"

    @admin.display(description='Ouvrir')
    def admin_download_link(self, obj):
        if obj.file_url:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener">Télécharger</a>',
                obj.file_url,
            )
        return '-'

    @admin.action(description='Rafraîchir les liens Supabase')
    def refresh_supabase_links(self, request, queryset):
        try:
            service = SupabaseStorageService()
        except SupabaseStorageError as exc:
            self.message_user(
                request,
                _("Supabase indisponible: %s") % exc,
                level=messages.ERROR,
            )
            return

        refreshed = 0
        for attachment in queryset:
            if (
                attachment.storage_resource_type == 'supabase'
                and attachment.storage_public_id
            ):
                try:
                    attachment.file_url = service.get_file_url(
                        attachment.storage_public_id
                    )
                    attachment.save(update_fields=['file_url'])
                    refreshed += 1
                except SupabaseStorageError as exc:
                    self.message_user(
                        request,
                        _("Échec pour %s: %s") % (attachment, exc),
                        level=messages.WARNING,
                    )

        if refreshed:
            self.message_user(
                request,
                _('%d lien(s) mis à jour avec succès.') % refreshed,
                level=messages.SUCCESS,
            )

admin.site.site_header = "Administration - Gestion Moyens Généraux"
admin.site.site_title = "Gestion MG"
admin.site.index_title = "Tableau de bord administrateur"
