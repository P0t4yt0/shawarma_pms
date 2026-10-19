from django.urls import path
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
    path('settings/account-security/', views.account_security_view, name='account_security'),
]