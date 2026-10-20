from django.urls import path
from . import views

app_name = 'maintenance' 

urlpatterns = [

    path('settings/', views.maintenance_dashboard, name='maintenance_dashboard'),
    path('backup/', views.trigger_backup, name='trigger_backup'),
    path('restore/', views.trigger_restore, name='trigger_restore'),
    path('delete-all/', views.delete_all_data, name='delete_all_data'),
]