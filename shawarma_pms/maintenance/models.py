"""
Module 9 — Maintenance
Handles scheduled and manual database backups (SQL dumps) and restoration.
Backup files saved locally; user must copy to external drive manually.
Admin-only access.
"""
from django.db import models
from django.conf import settings
from django.utils import timezone


class BackupRecord(models.Model):
    """
    Log of every database backup operation.
    Backup files are compressed SQL dumps stored in BACKUP_DIR.
    """
    TYPE_CHOICES = [
        ('manual',    'Manual Backup'),
        ('scheduled', 'Scheduled Auto-Backup'),
    ]

    STATUS_CHOICES = [
        ('success', 'Success'),
        ('failed',  'Failed'),
        ('pending', 'Pending'),
    ]

    created_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,      # null = automated/system backup
        related_name='backups'
    )
    backup_type  = models.CharField(max_length=10, choices=TYPE_CHOICES, default='manual')
    file_name    = models.CharField(max_length=255)
    file_path    = models.CharField(max_length=500)
    file_size_kb = models.PositiveIntegerField(default=0)
    status       = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True)
    date_created = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'backup_records'
        ordering = ['-date_created']

    def __str__(self):
        return f'{self.file_name} — {self.get_status_display()} ({self.date_created:%Y-%m-%d %H:%M})'

    @property
    def file_size_display(self):
        if self.file_size_kb >= 1024:
            return f'{self.file_size_kb / 1024:.1f} MB'
        return f'{self.file_size_kb} KB'


class RestoreRecord(models.Model):
    """
    Log of every database restoration operation.
    Restoring overwrites current data — recorded for accountability.
    """
    STATUS_CHOICES = [
        ('success', 'Success'),
        ('failed',  'Failed'),
    ]

    restored_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='restores'
    )
    backup_record = models.ForeignKey(
        BackupRecord, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='restores'
    )
    file_used     = models.CharField(max_length=255)
    status        = models.CharField(max_length=10, choices=STATUS_CHOICES)
    error_message = models.TextField(blank=True)
    date_restored = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'restore_records'
        ordering = ['-date_restored']

    def __str__(self):
        return f'Restore from {self.file_used} — {self.get_status_display()} ({self.date_restored:%Y-%m-%d %H:%M})'
