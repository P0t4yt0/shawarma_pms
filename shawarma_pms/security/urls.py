from django.urls import path, include
from . import access, views

app_name = "security"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("forgot-password/", views.forgot_password, name="forgot_password"),
    path("home/", access.role_home, name="home"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("api/clock-in/", views.clock_in, name="clock_in"),
    path("api/clock-out/", views.clock_out, name="clock_out"),
    path('settings/user-roles/', views.user_roles_view, name='user_roles'),
    path('employees/', views.employee_list_view, name='employee_list'),
    path('attendance/', views.attendance_view, name='attendance'),
    path('work-schedules/', views.work_schedules_view, name='work_schedules'),
    path('settings/general/', views.general_settings_view, name='general_settings'),
    path('settings/account-security/', views.account_security_view, name='account_security'),
    path('activity/', include('activity_log.urls')),    
    
]