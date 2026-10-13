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
]