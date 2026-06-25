from web.models.user import UserProfile


def get_or_create_user_profile(user):
    user_profile, _ = UserProfile.objects.get_or_create(user=user)
    return user_profile
