import os
import pyAesCrypt
from datetime import datetime
from django.shortcuts import render, redirect
from django.db import connection
from django.contrib import messages
from django.http import FileResponse
from django.core.management import call_command
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone

from django.contrib.auth import get_user_model, login # ADDED: import login
User = get_user_model()

from activity_log.services import log_activity
from .models import BackupRecord, RestoreRecord

BUFFER_SIZE = 64 * 1024

# HELPER FUNCTIONS

def get_current_db_size():
    """Gets total database size in MB (Supports MySQL & SQLite)."""
    try:
        if connection.vendor == 'mysql':
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT round(sum(data_length + index_length) / 1024 / 1024, 2) "
                    "FROM information_schema.tables WHERE table_schema = DATABASE();"
                )
                result = cursor.fetchone()
                return result[0] if result and result[0] else 0
        elif connection.vendor == 'sqlite3':
            db_path = connection.settings_dict.get('NAME')
            if db_path and os.path.exists(db_path):
                size_bytes = os.path.getsize(db_path)
                return round(size_bytes / (1024 * 1024), 2)
    except Exception:
        pass
    return 0

def get_last_backup_time():
    # 1. First, check the database for accuracy
    try:
        last_backup = BackupRecord.objects.filter(status='success').order_by('-date_created').first()
        if last_backup:
            # FIX: I-convert muna sa local timezone bago i-format (UTC to Local Time)
            local_time = timezone.localtime(last_backup.date_created)
            return local_time.strftime('%B %d, %Y, %I:%M %p')
    except Exception:
        pass

    # 2. Fallback: Filter strictly for .enc files only
    backup_dir = os.path.join(settings.BASE_DIR, 'secure_backups')
    if not os.path.exists(backup_dir):
        return "Never"

    valid_files = [
        os.path.join(backup_dir, f) for f in os.listdir(backup_dir)
        if os.path.isfile(os.path.join(backup_dir, f)) and f.endswith('.enc')
    ]

    if not valid_files:
        return "Never"

    latest_file = max(valid_files, key=os.path.getmtime)
    timestamp = os.path.getmtime(latest_file)
    return datetime.fromtimestamp(timestamp).strftime('%B %d, %Y, %I:%M %p')

# Main View
@login_required
@user_passes_test(lambda u: u.is_superuser or u.is_staff)
def maintenance_dashboard(request):
    report = []
    tables_to_optimize = [] 
    
    just_optimized = request.session.get('db_optimized_recently', False)

    # Clean up / diagnostics base sa database vendor
    if not just_optimized:
        if connection.vendor == 'mysql':
            with connection.cursor() as cursor:
                cursor.execute("SHOW TABLE STATUS WHERE Data_free > 0")
                tables = cursor.fetchall()
                for table in tables:
                    table_name = table[0]
                    raw_overhead = table[9] if table[9] is not None else 0
                    overhead_mb = round(raw_overhead / 1024 / 1024, 3)
                    
                    if overhead_mb > 0:
                        tables_to_optimize.append(table_name)
                        report.append(f"Some old records in '{table_name}' are taking up {overhead_mb} MB of extra space.")
        elif connection.vendor == 'sqlite3':
            # Sa SQLite, automatic na chine-check natin kung kailangan ng VACUUM
            pass

    if request.method == "POST":
        if connection.vendor == 'mysql':
            if tables_to_optimize: 
                with connection.cursor() as cursor:
                    tables_str = ", ".join(tables_to_optimize)
                    cursor.execute(f"OPTIMIZE TABLE {tables_str}")
                
                log_activity(
                    request.user,
                    "SYSTEM MAINTENANCE",
                    "maintenance",
                    None,
                    f"Cleaned up and optimized storage for: {tables_str}."
                )
                messages.success(request, "Storage successfully cleaned and optimized!")
            else:
                messages.info(request, "System is already optimized. No action needed.")
        elif connection.vendor == 'sqlite3':
            # Execute VACUUM command for SQLite storage cleanup
            with connection.cursor() as cursor:
                cursor.execute("VACUUM;")
            
            log_activity(
                request.user,
                "SYSTEM MAINTENANCE",
                "maintenance",
                None,
                "Executed SQLite VACUUM optimization."
            )
            messages.success(request, "Database successfully defragmented and optimized!")

        request.session['db_optimized_recently'] = True
        return redirect(request.path)

    context = {
        'db_size': get_current_db_size(),
        'report': report,
        'last_backup_time': get_last_backup_time(),
    }
    
    return render(request, 'backup_maintenance.html', context)

# BACKUP, RESTORE, AND DELETE VIEWS

import sqlite3
from django.db import connection

@login_required
@user_passes_test(lambda u: u.is_superuser or u.is_staff)
def trigger_backup(request):
    if request.method == 'POST':
        encryption_key = request.POST.get('encryption_key')
        confirm_key = request.POST.get('confirm_encryption_key')
        
        if not encryption_key or not confirm_key:
            messages.error(request, 'Encryption passwords are required.')
            return redirect(request.META.get('HTTP_REFERER', '/'))

        if encryption_key != confirm_key:
            messages.error(request, 'Passwords do not match. Backup cancelled.')
            return redirect(request.META.get('HTTP_REFERER', '/'))

        try:
            backup_dir = os.path.join(settings.BASE_DIR, 'secure_backups')
            os.makedirs(backup_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            raw_dump_path = os.path.join(backup_dir, f'backup_{timestamp}.sql')
            encrypted_file = f"{raw_dump_path}.enc"

            # 1. Lumikha ng raw SQL dump na may explicit UTF-8 encoding
            if connection.vendor == 'sqlite3':
                db_path = connection.settings_dict.get('NAME')
                # FORCE utf-8 encoding para hindi mag-fail sa Peso sign (₱) o ibang special characters
                with sqlite3.connect(db_path) as conn:
                    with open(raw_dump_path, 'w', encoding='utf-8') as f:
                        for line in conn.iterdump():
                            f.write(f'{line}\n')
            else:
                # Para sa MySQL
                with open(raw_dump_path, 'w', encoding='utf-8') as f:
                    call_command('dumpdata', stdout=f)

            # 2. I-encrypt gamit ang pyAesCrypt
            pyAesCrypt.encryptFile(raw_dump_path, encrypted_file, encryption_key, BUFFER_SIZE)
            
            # Burahin ang raw unencrypted file
            if os.path.exists(raw_dump_path):
                os.remove(raw_dump_path)
            
            log_activity(
                request.user, 
                "DATABASE BACKUP", 
                "maintenance", 
                None, 
                "Successfully generated and downloaded an ENCRYPTED database backup."
            )
            
            try:
                BackupRecord.objects.create(
                    created_by=request.user,
                    backup_type='manual',
                    file_name=os.path.basename(encrypted_file),
                    file_path=encrypted_file,
                    file_size_kb=os.path.getsize(encrypted_file) // 1024,
                    status='success'
                )
            except Exception:
                pass
            
            # Return file response safely
            response = FileResponse(open(encrypted_file, 'rb'), as_attachment=True)
            return response
            
        except Exception as e:
            messages.error(request, f'Backup failed: {e}')
            return redirect(request.META.get('HTTP_REFERER', '/'))


@login_required
@user_passes_test(lambda u: u.is_superuser or u.is_staff)
def trigger_restore(request):
    if request.method == 'POST' and request.FILES.get('backup_file'):
        decryption_key = request.POST.get('decryption_key')
        uploaded_file = request.FILES['backup_file']
        
        if not decryption_key:
            messages.error(request, 'Decryption password is required.')
            return redirect(request.META.get('HTTP_REFERER', '/'))

        backup_dir = os.path.join(settings.BASE_DIR, 'secure_backups')
        os.makedirs(backup_dir, exist_ok=True)

        fs = FileSystemStorage(location=backup_dir)
        filename = fs.save(uploaded_file.name, uploaded_file)
        encrypted_filepath = fs.path(filename)
        decrypted_filepath = encrypted_filepath.replace('.enc', '') if encrypted_filepath.endswith('.enc') else f"{encrypted_filepath}_decrypted.sql"

        try:
            # 1. Decrypt ang file
            try:
                pyAesCrypt.decryptFile(encrypted_filepath, decrypted_filepath, decryption_key, BUFFER_SIZE)
            except ValueError:
                raise Exception("Incorrect decryption password or corrupted backup file.")

            # 2. I-save ang kasalukuyang credentials ng Admin para hindi ma-lockout
            current_admin = request.user
            preserved_admin_data = {
                'username': getattr(current_admin, 'username', 'admin'),
                'password': getattr(current_admin, 'password', ''), 
                'is_superuser': getattr(current_admin, 'is_superuser', True),
                'is_staff': getattr(current_admin, 'is_staff', True),
                'is_active': getattr(current_admin, 'is_active', True),
            }

            # 3. I-execute ang SQL script sa database
            if connection.vendor == 'sqlite3':
                db_path = connection.settings_dict.get('NAME')
                with open(decrypted_filepath, 'r', encoding='utf-8') as f:
                    sql_script = f.read()
                
                connection.close() # Isara ang existing connection bago mag-restore
                with sqlite3.connect(db_path) as conn:
                    cursor = conn.cursor()
                    cursor.executescript(sql_script)

            # 4. Panatilihin ang Admin Account
            user_obj, created = User.objects.get_or_create(username=preserved_admin_data['username'])
            user_obj.password = preserved_admin_data['password']
            if hasattr(user_obj, 'is_superuser'):
                user_obj.is_superuser = preserved_admin_data['is_superuser']
            if hasattr(user_obj, 'is_staff'):
                user_obj.is_staff = preserved_admin_data['is_staff']
            if hasattr(user_obj, 'is_active'):
                user_obj.is_active = preserved_admin_data['is_active']
            user_obj.save()
            
            login(request, user_obj, backend='django.contrib.auth.backends.ModelBackend')
            
            log_activity(user_obj, "DATABASE RESTORE", "maintenance", None, f"Restored the database using encrypted backup file: '{uploaded_file.name}'.")

            try:
                RestoreRecord.objects.create(
                    restored_by=user_obj,
                    file_used=uploaded_file.name,
                    status='success'
                )
            except Exception:
                pass
            
            messages.success(request, 'System successfully restored from encrypted backup!')
            
        except Exception as e:
            messages.error(request, f'Restore failed: {e}')
            
        finally:
            if os.path.exists(encrypted_filepath):
                os.remove(encrypted_filepath)
            if os.path.exists(decrypted_filepath):
                os.remove(decrypted_filepath)
                
        return redirect(request.META.get('HTTP_REFERER', '/'))


@login_required
@user_passes_test(lambda u: u.is_superuser or u.is_staff)
def delete_all_data(request):
    if request.method == 'POST':
        protected_tables = [
            'security_user', 
            'security_user_groups', 
            'security_user_user_permissions',
            'auth_group', 
            'auth_permission', 
            'django_migrations',
            'django_content_type',
            'django_session',
            'django_admin_log',
            'security_storesettings', # Huwag burahin ang store profile settings
        ]

        try:
            with connection.cursor() as cursor:
                if connection.vendor == 'mysql':
                    cursor.execute("SHOW TABLES")
                    all_tables = [row[0] for row in cursor.fetchall()]

                    cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
                    for table in all_tables:
                        if table not in protected_tables:
                            cursor.execute(f"TRUNCATE TABLE `{table}`;")
                    cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")

                elif connection.vendor == 'sqlite3':
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                    all_tables = [row[0] for row in cursor.fetchall() if not row[0].startswith('sqlite_')]

                    cursor.execute("PRAGMA foreign_keys = OFF;")
                    for table in all_tables:
                        if table not in protected_tables:
                            cursor.execute(f"DELETE FROM `{table}`;")
                    cursor.execute("PRAGMA foreign_keys = ON;")

            # Panatilihing ligtas ang superusers
            User.objects.exclude(is_superuser=True).delete()

            log_activity(
                request.user,
                "SYSTEM WIPE",
                "maintenance",
                None,
                "Performed a full system wipe. All transactions and extra accounts were cleared."
            )

            messages.success(request, 'System wiped! All normal accounts and transactions are cleared, but ALL Admin accounts remain safe.')
        except Exception as e:
            messages.error(request, f'Error wiping data: {e}')
             
    return redirect(request.META.get('HTTP_REFERER', '/'))